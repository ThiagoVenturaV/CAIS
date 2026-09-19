"""
=====================================================================
MOTOR PREDITIVO DE ZELADORIA E ORDEM PÚBLICA - COLETA DE DADOS (v2)
=====================================================================
Diferença em relação à v1: em vez de mockar TUDO, este script primeiro
tenta baixar dados públicos reais do Portal de Dados Abertos do Recife
(dados.recife.pe.gov.br, plataforma CKAN) e só recorre a geração
sintética para as variáveis que realmente não têm fonte aberta
disponível (densidade de pessoas em tempo real, eventos, histórico de
ocorrências). Isso deixa a base muito mais defensável no pitch: dá pra
dizer exatamente o que é dado real e o que é simulado.

Fontes reais usadas:
  1) "Parques e Praças" (Secretaria de Infraestrutura) -> nomes e
     geolocalização oficiais das praças/parques do Recife.
     https://dados.recife.pe.gov.br/dataset/parques-e-pracas
  2) "Postes - Iluminação Pública do Recife" -> localização de cada
     poste de iluminação, usada para calcular uma taxa real de
     cobertura de iluminação por praça (buffer geográfico).
     https://dados.recife.pe.gov.br/dataset/postes-iluminacao-publica-do-recife

IMPORTANTE:
  - Eu não consegui inspecionar os nomes exatos das colunas dos CSVs a
    partir daqui (o portal devolve os arquivos como download binário).
    Por isso o script imprime `df.columns.tolist()` logo após cada
    download e usa detecção flexível de colunas (tenta várias
    variações comuns em PT-BR). Rode uma vez e ajuste os nomes no
    dicionário CANDIDATOS_COLUNAS se necessário.
  - Se a rede falhar (ex.: ambiente sem acesso ao domínio da
    prefeitura, ou API fora do ar), o script cai automaticamente para
    a lista de praças mockada da v1, para não travar o pipeline no
    dia do pitch.
"""

import io
import re
import unicodedata
import numpy as np
import pandas as pd
import requests

np.random.seed(42)  # reprodutibilidade na hora do pitch

CKAN_BASE = "https://dados.recife.pe.gov.br/api/3/action"
TIMEOUT = 20

# Praças de fallback (usadas só se o download real falhar)
PRACAS_FALLBACK = [
    "Praça do Arsenal", "Parque da Jaqueira", "Marco Zero",
    "Dona Lindu", "Sítio Trindade",
]


# =====================================================================
# UTILITÁRIOS
# =====================================================================
def _normaliza(col: str) -> str:
    """minúsculas, sem acento, sem espaço - pra comparar nomes de coluna."""
    col = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", col.lower())


def _acha_coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    """Procura a primeira coluna do df cujo nome normalizado bate com
    algum dos candidatos normalizados."""
    normalizadas = {_normaliza(c): c for c in df.columns}
    for cand in candidatos:
        c = _normaliza(cand)
        if c in normalizadas:
            return normalizadas[c]
    # também aceita coluna que CONTÉM o candidato (ex.: "nm_praca")
    for cand in candidatos:
        c = _normaliza(cand)
        for norm, original in normalizadas.items():
            if c in norm:
                return original
    return None


def _resource_csv_url(package_id: str, formato_preferido="CSV", posicao=None) -> str | None:
    """Consulta a Action API do CKAN (package_show) e devolve a URL de
    download do recurso CSV mais recente, em vez de depender de um link
    assinado (que expira em ~1h)."""
    r = requests.get(f"{CKAN_BASE}/package_show", params={"id": package_id}, timeout=TIMEOUT)
    r.raise_for_status()
    resources = r.json()["result"]["resources"]
    csvs = [res for res in resources if res.get("format", "").upper() == formato_preferido]
    if not csvs:
        return None
    if posicao is not None:
        for res in csvs:
            if res.get("position") == posicao:
                return res["url"]
    # sem posição específica -> pega o mais recente (last_modified)
    csvs.sort(key=lambda r: r.get("last_modified") or r.get("created") or "", reverse=True)
    return csvs[0]["url"]


def _haversine_km(lat1, lon1, lat2, lon2):
    """Distância em km entre pontos (arrays), fórmula de haversine."""
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


# =====================================================================
# 1a. COLETA REAL - PRAÇAS E PARQUES (dados oficiais da prefeitura)
# =====================================================================
def coletar_pracas_reais() -> pd.DataFrame:
    print("1a. Baixando 'Parques e Praças' do Portal de Dados Abertos do Recife...")
    url = _resource_csv_url("parques-e-pracas")
    if url is None:
        raise RuntimeError("Não encontrei recurso CSV no pacote 'parques-e-pracas'.")

    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    # tenta ; e , como separador (CKAN/PCR costuma exportar em ;)
    try:
        df = pd.read_csv(io.BytesIO(resp.content), sep=";", encoding="utf-8")
        if df.shape[1] == 1:
            raise ValueError("separador errado")
    except Exception:
        df = pd.read_csv(io.BytesIO(resp.content), sep=",", encoding="utf-8")

    print(f"   Colunas encontradas em Parques e Praças: {df.columns.tolist()}")

    col_nome = _acha_coluna(df, ["nome", "nm_praca", "nome_praca", "denominacao", "nome_oficial"])
    col_lat = _acha_coluna(df, ["latitude", "lat", "y"])
    col_lon = _acha_coluna(df, ["longitude", "lon", "long", "x"])

    if col_nome is None:
        raise RuntimeError(
            "Não achei a coluna de nome da praça automaticamente. "
            f"Colunas disponíveis: {df.columns.tolist()} - ajuste _acha_coluna/CANDIDATOS."
        )

    out = pd.DataFrame({"local": df[col_nome].astype(str).str.strip()})
    if col_lat and col_lon:
        out["lat"] = pd.to_numeric(df[col_lat], errors="coerce")
        out["lon"] = pd.to_numeric(df[col_lon], errors="coerce")
    else:
        # o dataset pode vir como polígono (geometry/WKT) em vez de lat/lon
        # centróide -> deixamos NaN aqui; o cálculo de iluminação real cai
        # pro fallback aleatório só para essas linhas.
        print("   Aviso: não achei lat/lon diretas; iluminação real pode ficar parcial "
              "(considere extrair o centróide da coluna de geometria, se houver).")
        out["lat"] = np.nan
        out["lon"] = np.nan

    out = out.dropna(subset=["local"]).drop_duplicates(subset=["local"]).reset_index(drop=True)
    print(f"   {len(out)} praças/parques reais carregados.")
    return out


# =====================================================================
# 1b. COLETA REAL - POSTES DE ILUMINAÇÃO PÚBLICA
# =====================================================================
def coletar_postes_iluminacao() -> pd.DataFrame:
    print("1b. Baixando 'Postes - Iluminação Pública do Recife' (mais recente)...")
    url = _resource_csv_url("postes-iluminacao-publica-do-recife", posicao=0)  # position=0 = ano mais recente
    if url is None:
        raise RuntimeError("Não encontrei recurso CSV no pacote de iluminação pública.")

    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    try:
        df = pd.read_csv(io.BytesIO(resp.content), sep=";", encoding="utf-8", low_memory=False)
        if df.shape[1] == 1:
            raise ValueError("separador errado")
    except Exception:
        df = pd.read_csv(io.BytesIO(resp.content), sep=",", encoding="utf-8", low_memory=False)

    print(f"   Colunas encontradas em Postes de Iluminação: {df.columns.tolist()}")

    col_lat = _acha_coluna(df, ["latitude", "lat", "y"])
    col_lon = _acha_coluna(df, ["longitude", "lon", "long", "x"])
    col_status = _acha_coluna(df, ["status", "situacao", "estado", "condicao"])

    if col_lat is None or col_lon is None:
        raise RuntimeError(
            "Não achei colunas de latitude/longitude nos postes. "
            f"Colunas disponíveis: {df.columns.tolist()}"
        )

    out = pd.DataFrame({
        "lat": pd.to_numeric(df[col_lat], errors="coerce"),
        "lon": pd.to_numeric(df[col_lon], errors="coerce"),
    })
    if col_status:
        # heurística: considera "ativo" tudo que não contenha termos de defeito/apagado
        status_txt = df[col_status].astype(str).str.lower()
        out["ativo"] = ~status_txt.str.contains("apagad|defeit|inativ|quebrad|manuten", na=False)
    else:
        out["ativo"] = True  # sem info de status -> assume operante

    out = out.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    print(f"   {len(out)} postes carregados.")
    return out


# =====================================================================
# 1c. FEATURE REAL - % DE ILUMINAÇÃO ATIVA POR PRAÇA (buffer geográfico)
# =====================================================================
def calcular_iluminacao_real(pracas: pd.DataFrame, postes: pd.DataFrame, raio_km=0.15) -> pd.DataFrame:
    """Para cada praça com lat/lon, calcula a % de postes ativos dentro
    de um raio (padrão 150 m). Praças sem coordenada ficam com NaN e
    recebem um valor sintético depois (fallback)."""
    print(f"1c. Calculando cobertura real de iluminação (raio = {int(raio_km*1000)} m)...")
    pct = []
    for _, praca in pracas.iterrows():
        if pd.isna(praca["lat"]) or pd.isna(praca["lon"]):
            pct.append(np.nan)
            continue
        dist = _haversine_km(praca["lat"], praca["lon"], postes["lat"].values, postes["lon"].values)
        proximos = postes[dist <= raio_km]
        if len(proximos) == 0:
            pct.append(np.nan)  # nenhum poste catalogado perto -> sem dado real
        else:
            pct.append(100 * proximos["ativo"].mean())
    pracas = pracas.copy()
    pracas["iluminacao_ativa_pct_real"] = pct
    cobertura = pracas["iluminacao_ativa_pct_real"].notna().mean() * 100
    print(f"   {cobertura:.1f}% das praças tiveram iluminação real calculada com sucesso.")
    return pracas


# =====================================================================
# 1d. ORQUESTRAÇÃO DA COLETA REAL (com fallback seguro)
# =====================================================================
def montar_base_pracas() -> pd.DataFrame:
    try:
        pracas = coletar_pracas_reais()
        postes = coletar_postes_iluminacao()
        pracas = calcular_iluminacao_real(pracas, postes)
        return pracas
    except Exception as e:
        print(f"⚠️  Coleta real falhou ({e}). Caindo para lista mockada da v1.")
        return pd.DataFrame({
            "local": PRACAS_FALLBACK,
            "lat": np.nan,
            "lon": np.nan,
            "iluminacao_ativa_pct_real": np.nan,
        })


# =====================================================================
# 2. GERAÇÃO SINTÉTICA (só para o que NÃO tem fonte pública aberta)
# =====================================================================
def gerar_dataset(pracas: pd.DataFrame, num_registros=5000) -> pd.DataFrame:
    print("2. Gerando eventos sintéticos sobre a base real de praças...")
    idx = np.random.choice(pracas.index, num_registros)
    base = pracas.loc[idx].reset_index(drop=True)

    dados = {
        "local": base["local"],
        "dia_semana": np.random.randint(0, 7, num_registros),
        "hora_dia": np.random.randint(0, 24, num_registros),
        "eventos_proximos": np.random.choice([0, 1], num_registros, p=[0.8, 0.2]),
        "historico_ocorrencias_7d": np.random.randint(0, 15, num_registros),
    }
    df = pd.DataFrame(dados)

    # iluminação: usa o dado REAL quando existe; senão, sintético como na v1
    ilum_real = base["iluminacao_ativa_pct_real"].values
    ilum_sintetica = np.random.randint(20, 100, num_registros)
    df["iluminacao_ativa_pct"] = np.where(pd.isna(ilum_real), ilum_sintetica, ilum_real).round(1)
    df["iluminacao_fonte"] = np.where(pd.isna(ilum_real), "sintetica", "real")

    df["densidade_pessoas"] = np.where(
        (df["dia_semana"] >= 4) & (df["hora_dia"] >= 18),
        np.random.randint(60, 100, num_registros),
        np.random.randint(10, 60, num_registros),
    )

    condicao_risco = (
        ((df["densidade_pessoas"] > 75) & (df["iluminacao_ativa_pct"] < 60)) |
        ((df["eventos_proximos"] == 1) & (df["historico_ocorrencias_7d"] > 8))
    )
    df["acao_preventiva_necessaria"] = np.where(condicao_risco, 1, 0)
    ruido = np.random.choice([0, 1], num_registros, p=[0.95, 0.05])
    df["acao_preventiva_necessaria"] = np.abs(df["acao_preventiva_necessaria"] - ruido)

    print(f"   Dataset final: {len(df)} registros "
          f"({(df['iluminacao_fonte'] == 'real').mean() * 100:.1f}% com iluminação real).")
    return df


# =====================================================================
# EXECUÇÃO
# =====================================================================
if __name__ == "__main__":
    pracas = montar_base_pracas()
    df = gerar_dataset(pracas, num_registros=5000)
    df.to_csv("dataset_seops.csv", index=False)
    print("\n✅ Base salva em 'dataset_seops.csv'. Pronta para a etapa de padronização/treinamento.")
    print(df.head())