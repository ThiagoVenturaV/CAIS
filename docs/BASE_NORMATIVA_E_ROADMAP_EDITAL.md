# Base normativa e roadmap para documentos de contratação

**Data da verificação:** 19 de setembro de 2026
**Escopo:** relatórios gerenciais do CAIS e uso desses relatórios como insumo de planejamento de contratação pública.

## Decisão de produto

O CAIS implementa uma **minuta técnica preparatória**, não um edital publicável. A minuta organiza evidências, necessidade pública, alternativas, requisitos, resultados esperados, riscos e pendências. Ela não define modalidade, fornecedor, preço, dotação, quantitativos finais, critério de julgamento ou autorização.

Essa separação é um controle do sistema: informação operacional pode iniciar planejamento, mas não substitui competência administrativa, segregação de funções, pesquisa de mercado, pareceres e aprovação da autoridade.

## Base oficial verificada

| Fonte | Uso no CAIS |
|---|---|
| [Lei Federal nº 14.133/2021](https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm), art. 5º | princípios de legalidade, planejamento, transparência, segregação de funções, motivação, economicidade e outros |
| Lei 14.133/2021, art. 6º, XX e XXIII | estrutura conceitual de estudo técnico preliminar e termo de referência |
| Lei 14.133/2021, art. 18 | fase preparatória, necessidade, definição do objeto, condições e orçamento estimado |
| Lei 14.133/2021, art. 23 | pesquisa e estimativa de preços; o CAIS deixa esse bloco pendente |
| Lei 14.133/2021, arts. 54 e 174 | publicidade e Portal Nacional de Contratações Públicas |
| [Portal Nacional de Contratações Públicas](https://www.gov.br/pncp/pt-br) | fonte oficial para PCA, editais, avisos, atas, contratos, dados abertos e modelos disponíveis |

## Gate específico do Município do Recife

Antes de qualquer documento instruir um processo real, a equipe municipal deve consultar as versões vigentes no [Portal da Transparência do Recife](https://transparencia.recife.pe.gov.br/), no [Diário Oficial do Município](https://dome.recife.pe.gov.br/) e no sistema oficial de compras indicado pelo órgão.

O protótipo não declara conformidade automática com um decreto ou modelo municipal específico. A busca pública não permitiu confirmar, em uma única fonte oficial estável, todo o conjunto vigente de decretos, instruções e modelos aplicável ao possível objeto. Portanto, o backend marca `municipal_alignment.status=pending_de_validacao_formal` e exige:

- identificação da unidade requisitante e da autoridade competente;
- inventário dos decretos, portarias, manuais e modelos municipais vigentes;
- confirmação do Plano de Contratações Anual e da dotação;
- uso do modelo oficial atual de ETP, termo de referência e edital;
- revisão de compras/licitações, orçamento, proteção de dados, controle interno e jurídico;
- publicidade no Diário Oficial e PNCP apenas quando cabível e por usuário autorizado.

## Conteúdo gerado pelo MVP

O relatório persiste os IDs dos eventos usados como evidência e contém:

1. estatísticas do período e aviso sobre a natureza do protótipo;
2. achados descritivos, sem alegação automática de causalidade;
3. três alternativas: ajuste interno, ação intersecretarial e piloto estrutural;
4. custo apenas qualitativo, sem preço inventado;
5. requisitos, riscos, pré-condições e métricas de resultado;
6. minuta técnica em JSON e exportação Markdown;
7. campos ausentes e checklist de aprovações.

O Gemini pode redigir o resumo executivo, mas a estrutura da minuta, as evidências e os gates são determinísticos. Em falha ou ausência da chave, o fallback local mantém o fluxo funcional.

## Roadmap para chegar a um edital real

### Fase 1 — catálogo normativo versionado

- coletar normas e modelos apenas de fontes oficiais;
- armazenar URL, órgão emissor, vigência, hash e data de consulta;
- bloquear geração quando uma referência obrigatória estiver vencida ou sem validação;
- submeter o catálogo inicial à assessoria jurídica municipal.

### Fase 2 — ETP e termo de referência assistidos

- mapear cada campo do modelo oficial para evidência, entrada humana ou cálculo;
- impedir conclusão enquanto preço, quantitativo, PCA, dotação e responsáveis estiverem vazios;
- comparar manutenção interna, cooperação, contratação e não contratação;
- manter histórico de versões, comentários, aprovadores e justificativas.

### Fase 3 — pesquisa de mercado e controles

- integrar fontes permitidas do PNCP e bases adotadas pelo Município;
- guardar data/hora, memória de cálculo, documentos e critérios de exclusão;
- detectar preços atípicos sem selecionar fornecedor automaticamente;
- registrar segregação entre requisitante, pesquisa, julgamento e autorização.

### Fase 4 — composição do edital

- usar exclusivamente o template municipal vigente para o tipo de objeto;
- preencher apenas campos sustentados pelo ETP/TR aprovados;
- deixar modalidade e critério de julgamento para a autoridade competente;
- executar validações jurídicas e de consistência antes de permitir exportação;
- aplicar assinatura e publicação somente nos sistemas oficiais e com autorização explícita.

## Critérios de aceite para a evolução

- nenhuma norma sem fonte, versão e vigência;
- nenhuma publicação automática por modelo generativo;
- nenhum preço, dotação ou fornecedor inferido pelo LLM;
- trilha completa entre evidência operacional, versão do documento e aprovadores;
- revisão jurídica obrigatória testada como regra de autorização;
- teste com casos de não contratação e alternativas internas;
- avaliação LGPD e segurança antes de usar dados não agregados.

## Aviso

Este documento é uma especificação técnica e não constitui parecer jurídico. A conformidade de um processo concreto depende do objeto, da fonte de recursos, do órgão responsável e das normas vigentes na data da contratação.
