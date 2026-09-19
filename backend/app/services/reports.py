from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Settings
from ..database import Database
from ..domain import DomainEvent, make_id
from ..event_bus import EventBus
from ..observability import Metrics
from .gemini_service import GeminiService


class ReportWorkflow:
    """Turns accumulated operational evidence into a reviewable management report."""

    def __init__(
        self,
        database: Database,
        bus: EventBus,
        gemini: GeminiService,
        metrics: Metrics,
    ) -> None:
        self.database = database
        self.bus = bus
        self.gemini = gemini
        self.metrics = metrics

    async def on_report_requested(self, domain_event: DomainEvent) -> None:
        reason = str(domain_event.payload.get("reason") or "manual")
        try:
            report = await self.generate(
                lookback_days=int(domain_event.payload.get("lookback_days") or 7),
                title=domain_event.payload.get("title"),
                reason=reason,
                created_by=domain_event.actor_id,
            )
            report_id = self.database.create_report(report)
            self.metrics.reports.labels(reason, "success").inc()
            await self.bus.publish(
                DomainEvent(
                    event_type="report.generated",
                    aggregate_id=report_id,
                    actor_id=domain_event.actor_id,
                    payload={
                        "report_id": report_id,
                        "title": report["title"],
                        "period_start": report["period_start"],
                        "period_end": report["period_end"],
                        "solution_options": len(report["solution_options"]),
                        "provider": report["provider"],
                    },
                )
            )
        except Exception as exc:
            self.metrics.reports.labels(reason, "failure").inc()
            await self.bus.publish(
                DomainEvent(
                    event_type="report.generation.failed",
                    aggregate_id=domain_event.aggregate_id,
                    actor_id=domain_event.actor_id,
                    payload={"reason": reason, "error_type": type(exc).__name__},
                )
            )
            raise

    async def generate(
        self,
        *,
        lookback_days: int,
        title: str | None,
        reason: str,
        created_by: str | None,
    ) -> dict[str, Any]:
        period_end_dt = datetime.now(UTC)
        period_start_dt = period_end_dt - timedelta(days=max(1, min(lookback_days, 365)))
        period_start = period_start_dt.isoformat(timespec="milliseconds")
        period_end = period_end_dt.isoformat(timespec="milliseconds")
        evidence = await asyncio.to_thread(
            self.database.report_evidence, period_start, period_end
        )
        statistics = self._statistics(evidence)
        evidence_ids = [event["id"] for event in evidence["events"]]
        findings = self._findings(statistics, evidence_ids)
        solution_options = self._solution_options(statistics, evidence_ids)
        executive_summary, provider = await self.gemini.write_report_summary(
            statistics, findings, solution_options
        )
        procurement_draft = self._procurement_draft(
            statistics, findings, solution_options, evidence_ids
        )
        default_title = (
            "Relatório operacional CAIS — "
            f"{period_start_dt.strftime('%d/%m/%Y')} a {period_end_dt.strftime('%d/%m/%Y')}"
        )
        return {
            "title": title or default_title,
            "report_type": "operational_periodic",
            "generation_reason": reason,
            "period_start": period_start,
            "period_end": period_end,
            "executive_summary": executive_summary,
            "statistics": statistics,
            "findings": findings,
            "solution_options": solution_options,
            "procurement_draft": procurement_draft,
            "evidence_event_ids": evidence_ids,
            "provider": provider,
            "created_by": created_by,
        }

    @staticmethod
    def _statistics(evidence: dict[str, Any]) -> dict[str, Any]:
        events = evidence["events"]
        probabilities = [float(event["probability"]) for event in events]
        dispatches = evidence["dispatches"]
        sent = int(dispatches.get("sent") or 0)
        acknowledged = int(dispatches.get("acknowledged") or 0)
        completed = int(dispatches.get("completed") or 0)
        return {
            "total_events": len(events),
            "average_probability": round(sum(probabilities) / len(probabilities), 4) if probabilities else 0,
            "by_criticality": evidence["by_criticality"],
            "by_status": evidence["by_status"],
            "top_locations": [
                {
                    "local": row["local"],
                    "count": int(row["count"]),
                    "high_count": int(row.get("high_count") or 0),
                    "average_probability": round(float(row.get("average_probability") or 0), 4),
                }
                for row in evidence["by_local"]
            ],
            "top_categories": [
                {
                    "category": row["category"],
                    "count": int(row["count"]),
                    "average_probability": round(float(row.get("average_probability") or 0), 4),
                }
                for row in evidence["by_category"]
            ],
            "dispatches": {
                "sent": sent,
                "acknowledged": acknowledged,
                "completed": completed,
                "acknowledgement_rate": round(acknowledged / sent, 4) if sent else 0,
                "completion_rate": round(completed / sent, 4) if sent else 0,
            },
            "data_notice": (
                "Indicadores do protótipo baseados em dados agregados/híbridos; não comprovam "
                "causalidade nem desempenho operacional em produção."
            ),
        }

    @staticmethod
    def _findings(statistics: dict[str, Any], evidence_ids: list[str]) -> list[dict[str, Any]]:
        total = statistics["total_events"]
        top_locations = statistics["top_locations"]
        high = int(statistics["by_criticality"].get("high", 0))
        pending = int(statistics["by_status"].get("pending_approval", 0))
        acknowledgement_rate = statistics["dispatches"]["acknowledgement_rate"]
        findings: list[dict[str, Any]] = [
            {
                "title": "Volume de sinais no período",
                "evidence": {"total_events": total},
                "interpretation": (
                    f"Foram consolidados {total} evento(s) de demanda operacional. "
                    "O volume deve ser comparado com períodos equivalentes antes de concluir tendência."
                ),
                "confidence": "descriptive",
                "evidence_event_ids": evidence_ids[:50],
            }
        ]
        if top_locations:
            leader = top_locations[0]
            findings.append(
                {
                    "title": "Concentração territorial observada",
                    "evidence": leader,
                    "interpretation": (
                        f"{leader['local']} reuniu {leader['count']} evento(s) no recorte. "
                        "A concentração é descritiva e requer validação de cobertura e exposição."
                    ),
                    "confidence": "descriptive",
                    "evidence_event_ids": evidence_ids[:50],
                }
            )
        findings.extend(
            [
                {
                    "title": "Eventos de maior criticidade",
                    "evidence": {"high_criticality_events": high},
                    "interpretation": (
                        f"Há {high} evento(s) de criticidade alta segundo o modelo local; "
                        "cada caso continua sujeito à revisão humana."
                    ),
                    "confidence": "model_assisted",
                    "evidence_event_ids": evidence_ids[:50],
                },
                {
                    "title": "Rastreabilidade da distribuição",
                    "evidence": {
                        "acknowledgement_rate": acknowledgement_rate,
                        "pending_approval": pending,
                    },
                    "interpretation": (
                        f"A taxa de confirmação de recebimento foi {acknowledgement_rate:.0%}; "
                        f"{pending} evento(s) aguardavam decisão no fechamento do recorte."
                    ),
                    "confidence": "descriptive",
                    "evidence_event_ids": evidence_ids[:50],
                },
            ]
        )
        return findings

    @staticmethod
    def _solution_options(
        statistics: dict[str, Any], evidence_ids: list[str]
    ) -> list[dict[str, Any]]:
        top_local = (
            statistics["top_locations"][0]["local"]
            if statistics["top_locations"] else "áreas com maior concentração"
        )
        common = {
            "evidence_event_ids": evidence_ids[:50],
            "requires_human_approval": True,
        }
        return [
            {
                **common,
                "id": "option_operational",
                "title": "Ajuste operacional com recursos existentes",
                "description": (
                    f"Testar, por prazo definido, ajuste de horários, presença e coordenação em {top_local}, "
                    "sem presumir aumento permanente de efetivo."
                ),
                "rationale": "Alternativa reversível para validar o padrão antes de comprometer orçamento.",
                "cost_band": "sem nova contratação ou custo incremental baixo",
                "timeframe": "curto prazo, a definir pelo gestor",
                "procurement_posture": "não pressupõe contratação",
                "expected_impact": "melhorar cobertura e produzir evidência comparável",
                "prerequisites": ["validar escala", "definir grupo de comparação", "registrar baseline"],
                "risks": ["deslocar demanda para outra área", "sobrecarregar equipe", "viés de cobertura"],
                "success_metrics": ["tempo de resposta", "recorrência por faixa horária", "cobertura realizada"],
            },
            {
                **common,
                "id": "option_interagency",
                "title": "Intervenção intersecretarial focalizada",
                "description": (
                    "Combinar ação da Guarda com iluminação, manutenção urbana, ordenamento e agenda de eventos "
                    "nos pontos sustentados pelas evidências."
                ),
                "rationale": "Ataca fatores contextuais sem tratar presença ostensiva como única resposta.",
                "cost_band": "baixo a médio, sujeito a levantamento",
                "timeframe": "curto a médio prazo",
                "procurement_posture": "priorizar capacidade e contratos existentes; avaliar contratação somente após diagnóstico",
                "expected_impact": "reduzir recorrência contextual e melhorar coordenação municipal",
                "prerequisites": ["designar responsáveis", "vistoria técnica", "confirmar contratos vigentes"],
                "risks": ["dependência entre órgãos", "prazo de manutenção", "atribuição institucional incorreta"],
                "success_metrics": ["itens corrigidos", "prazo de atendimento", "recorrência após intervenção"],
            },
            {
                **common,
                "id": "option_structural",
                "title": "Piloto estruturado de capacidade e tecnologia",
                "description": (
                    "Avaliar piloto limitado para integração de dados, equipamentos, capacitação ou serviço especializado, "
                    "com metas de resultado, proteção de dados e avaliação antes de escalar."
                ),
                "rationale": "Alternativa estrutural apenas quando as opções internas forem insuficientes e a necessidade for comprovada.",
                "cost_band": "médio a alto, obrigatoriamente a estimar por pesquisa de mercado",
                "timeframe": "médio prazo",
                "procurement_posture": "pode exigir processo de contratação; modalidade não definida pelo CAIS",
                "expected_impact": "aumentar capacidade analítica e rastreabilidade, sujeito a avaliação do piloto",
                "prerequisites": ["ETP validado", "pesquisa de mercado", "dotação orçamentária", "avaliação LGPD e segurança"],
                "risks": ["dependência de fornecedor", "integração inadequada", "custo total subestimado", "uso indevido de dados"],
                "success_metrics": ["qualidade dos dados", "disponibilidade", "tempo de triagem", "custo por resultado", "taxa de falso alerta"],
            },
        ]

    @staticmethod
    def _procurement_draft(
        statistics: dict[str, Any],
        findings: list[dict[str, Any]],
        solution_options: list[dict[str, Any]],
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        top_local = (
            statistics["top_locations"][0]["local"]
            if statistics["top_locations"] else "territórios a validar"
        )
        return {
            "document_kind": "insumo_preliminar_para_etp_e_termo_de_referencia",
            "status": "minuta_tecnica_nao_publicavel",
            "warning": (
                "Este conteúdo não é edital, termo de referência aprovado, parecer jurídico ou autorização de despesa. "
                "Não pode ser publicado nem usado para selecionar fornecedor sem instrução formal e revisão humana."
            ),
            "title": "Minuta técnica preliminar — capacidade de prevenção e resposta operacional",
            "problem_definition": (
                f"O recorte registrou {statistics['total_events']} evento(s) de demanda operacional, com "
                f"concentração descritiva em {top_local}. É necessário validar se ajustes internos, coordenação "
                "intersecretarial ou eventual solução contratada entregam melhor resultado público."
            ),
            "public_interest_need": (
                "Aprimorar a capacidade de antecipar, coordenar, registrar e avaliar respostas preventivas da Guarda "
                "Municipal, preservando decisão humana, direitos fundamentais e rastreabilidade."
            ),
            "scope_boundary": [
                "não inclui reconhecimento facial ou perfil de risco individual",
                "não autoriza decisão automatizada de despacho ou sanção",
                "não presume aquisição antes da comparação de alternativas",
                "não usa score do modelo como prova de crime, autoria ou causalidade",
            ],
            "requirements": [
                "integração por API documentada e auditável",
                "controle de acesso por papel e trilha de auditoria",
                "métricas de qualidade, disponibilidade, latência e falso alerta",
                "portabilidade e exportação dos dados sem dependência indevida de fornecedor",
                "segurança da informação, minimização de dados e avaliação LGPD",
                "plano de implantação, capacitação, suporte e reversibilidade",
            ],
            "solution_comparison": solution_options,
            "recommended_next_step": (
                "Executar primeiro diagnóstico técnico e piloto operacional de baixo custo; somente se a insuficiência "
                "for documentada, iniciar ETP formal comparando manutenção interna, cooperação e mercado."
            ),
            "object_draft": (
                "[PREENCHER APÓS ETP] Eventual contratação de solução ou serviço para apoiar integração de dados, "
                "monitoramento de indicadores e coordenação operacional, com escopo, quantitativos e níveis de serviço "
                "definidos pela unidade requisitante."
            ),
            "quantity_and_budget": {
                "status": "pendente",
                "required_inputs": [
                    "unidades e usuários abrangidos", "volume e retenção de dados", "prazo do piloto/contrato",
                    "inventário existente", "memória de cálculo", "dotação e compatibilidade com o PCA",
                ],
                "price_research": (
                    "Realizar pesquisa formal conforme a Lei 14.133/2021, art. 23, regras municipais vigentes e "
                    "fontes oficiais; o CAIS não estima preço nem fornecedor."
                ),
            },
            "acceptance_and_outcomes": [
                "metas mensuráveis definidas a partir de baseline validado",
                "testes de integração, segurança e recuperação",
                "aceite por resultado, sem usar quantidade de alertas como sucesso isolado",
                "relatório do piloto com benefícios, custos, erros e recomendação de continuidade",
            ],
            "risk_map": [
                {"risk": "contratar antes de comprovar a necessidade", "impact": "alto", "mitigation": "ETP e comparação com alternativas internas", "owner": "unidade requisitante"},
                {"risk": "viés ou falso alerta", "impact": "alto", "mitigation": "validação temporal, supervisão humana e auditoria", "owner": "área técnica"},
                {"risk": "tratamento excessivo de dados", "impact": "alto", "mitigation": "minimização, base legal, RIPD quando aplicável e controles de acesso", "owner": "encarregado/segurança"},
                {"risk": "dependência tecnológica", "impact": "médio", "mitigation": "portabilidade, padrões abertos e plano de saída", "owner": "TI/contratos"},
                {"risk": "orçamento ou quantitativo sem memória", "impact": "alto", "mitigation": "pesquisa de preços e memória de cálculo revisadas", "owner": "compras/orçamento"},
            ],
            "legal_and_policy_references": [
                {
                    "reference": "Lei Federal nº 14.133/2021, arts. 5º, 6º, XX e XXIII, e 18",
                    "application": "princípios, ETP, termo de referência e fase preparatória",
                    "official_url": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm",
                },
                {
                    "reference": "Lei Federal nº 14.133/2021, arts. 23, 54 e 174",
                    "application": "pesquisa de preços, publicidade e PNCP",
                    "official_url": "https://www.gov.br/pncp/pt-br",
                },
                {
                    "reference": "Normativos, manuais e modelos vigentes da Prefeitura do Recife",
                    "application": "validação obrigatória pela área municipal de contratação antes de instruir ou publicar",
                    "official_url": "https://transparencia.recife.pe.gov.br/",
                },
            ],
            "municipal_alignment": {
                "status": "pendente_de_validacao_formal",
                "checklist": [
                    "confirmar regulamentos municipais vigentes da Lei 14.133/2021",
                    "usar o modelo oficial atual de ETP/TR/edital do órgão competente",
                    "verificar Plano de Contratações Anual e dotação",
                    "submeter à unidade de compras, controle interno e assessoria jurídica",
                    "aplicar regras de publicidade no Diário Oficial e PNCP quando cabíveis",
                ],
            },
            "mandatory_review_gates": [
                "gestor responsável e unidade requisitante",
                "área técnica e tecnologia da informação",
                "compras/licitações e orçamento",
                "proteção de dados e segurança da informação",
                "controle interno e assessoria jurídica",
                "autoridade competente",
            ],
            "missing_inputs": [
                "objeto final", "quantitativos", "pesquisa de mercado e preços", "dotação",
                "modalidade e critério de julgamento", "prazos", "responsáveis", "pareceres e aprovações",
            ],
            "evidence": {
                "report_statistics": statistics,
                "findings": findings,
                "operational_event_ids": evidence_ids[:100],
            },
        }


class ReportScheduler:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        bus: EventBus,
        metrics: Metrics,
    ) -> None:
        self.settings = settings
        self.database = database
        self.bus = bus
        self.metrics = metrics
        self.running = False
        self.last_triggered_at: str | None = None
        self.next_run_at: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if not self.settings.report_schedule_enabled or self.running:
            return
        self.running = True
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="cais-report-scheduler")

    async def stop(self) -> None:
        self.running = False
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def trigger(
        self,
        *,
        reason: str,
        lookback_days: int | None = None,
        actor_id: str | None = None,
        title: str | None = None,
    ) -> str:
        job_id = make_id("rptjob")
        domain_event = DomainEvent(
            event_type="report.generation.requested",
            aggregate_id=job_id,
            actor_id=actor_id,
            payload={
                "reason": reason,
                "lookback_days": lookback_days or self.settings.report_lookback_days,
                "title": title,
            },
        )
        self.metrics.domain_events.labels(domain_event.event_type).inc()
        await self.bus.publish(domain_event)
        self.last_triggered_at = domain_event.occurred_at
        return job_id

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.report_schedule_enabled,
            "running": self.running,
            "interval_seconds": self.settings.report_interval_seconds,
            "lookback_days": self.settings.report_lookback_days,
            "run_on_startup": self.settings.report_run_on_startup,
            "last_triggered_at": self.last_triggered_at,
            "next_run_at": self.next_run_at,
        }

    async def _run(self) -> None:
        if self.settings.report_run_on_startup and self.database.count_reports() == 0:
            if await self._wait(self.settings.report_startup_delay_seconds):
                return
            await self.trigger(reason="startup")
        while self.running:
            self.next_run_at = (
                datetime.now(UTC) + timedelta(seconds=self.settings.report_interval_seconds)
            ).isoformat(timespec="milliseconds")
            if await self._wait(self.settings.report_interval_seconds):
                return
            await self.trigger(reason="scheduled")

    async def _wait(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False


def render_procurement_markdown(report: dict[str, Any]) -> str:
    draft = report["procurement_draft"]
    lines = [
        f"# {draft['title']}",
        "",
        f"**Status:** {draft['status']}",
        "",
        f"> {draft['warning']}",
        "",
        f"Relatório de origem: {report['title']}",
        f"Período: {report['period_start']} a {report['period_end']}",
        "",
        "## 1. Necessidade de interesse público",
        "",
        draft["public_interest_need"],
        "",
        "## 2. Problema observado",
        "",
        draft["problem_definition"],
        "",
        "## 3. Limites do escopo",
        "",
        *[f"- {item}" for item in draft["scope_boundary"]],
        "",
        "## 4. Requisitos preliminares",
        "",
        *[f"- {item}" for item in draft["requirements"]],
        "",
        "## 5. Alternativas comparadas",
        "",
    ]
    for option in draft["solution_comparison"]:
        lines.extend(
            [
                f"### {option['title']}",
                "",
                option["description"],
                "",
                f"- Justificativa: {option['rationale']}",
                f"- Faixa de custo: {option['cost_band']}",
                f"- Prazo: {option['timeframe']}",
                f"- Contratação: {option['procurement_posture']}",
                f"- Resultado esperado: {option['expected_impact']}",
                "",
            ]
        )
    lines.extend(
        [
            "## 6. Próximo passo recomendado",
            "",
            draft["recommended_next_step"],
            "",
            "## 7. Objeto — somente rascunho condicionado ao ETP",
            "",
            draft["object_draft"],
            "",
            "## 8. Quantitativos, orçamento e pesquisa de preços",
            "",
            f"Status: {draft['quantity_and_budget']['status']}.",
            "",
            draft["quantity_and_budget"]["price_research"],
            "",
            *[f"- {item}" for item in draft["quantity_and_budget"]["required_inputs"]],
            "",
            "## 9. Aceite e resultados",
            "",
            *[f"- {item}" for item in draft["acceptance_and_outcomes"]],
            "",
            "## 10. Mapa preliminar de riscos",
            "",
            "| Risco | Impacto | Mitigação | Responsável |",
            "|---|---|---|---|",
            *[
                f"| {item['risk']} | {item['impact']} | {item['mitigation']} | {item['owner']} |"
                for item in draft["risk_map"]
            ],
            "",
            "## 11. Referências e validação municipal",
            "",
            *[
                f"- [{item['reference']}]({item['official_url']}): {item['application']}."
                for item in draft["legal_and_policy_references"]
            ],
            "",
            f"**Alinhamento municipal:** {draft['municipal_alignment']['status']}.",
            "",
            *[f"- [ ] {item}" for item in draft["municipal_alignment"]["checklist"]],
            "",
            "## 12. Revisões obrigatórias",
            "",
            *[f"- [ ] {item}" for item in draft["mandatory_review_gates"]],
            "",
            "## 13. Informações ainda ausentes",
            "",
            *[f"- {item}" for item in draft["missing_inputs"]],
            "",
            "---",
            "Gerado automaticamente como apoio preparatório. Revisão e aprovação humanas são obrigatórias.",
        ]
    )
    return "\n".join(lines)
