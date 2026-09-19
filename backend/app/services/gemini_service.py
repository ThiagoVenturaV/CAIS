from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ..config import Settings
from ..observability import Metrics


logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(self, settings: Settings, metrics: Metrics):
        self.settings = settings
        self.metrics = metrics
        self.client: Any = None
        self.init_error: str | None = None
        self._initialize()

    def _initialize(self) -> None:
        if self.settings.gemini_mode == "disabled" or not self.settings.gemini_api_key:
            return
        try:
            from google import genai

            self.client = genai.Client(api_key=self.settings.gemini_api_key)
        except Exception as exc:
            self.init_error = f"{type(exc).__name__}: {exc}"
            if self.settings.gemini_mode == "required":
                raise RuntimeError(f"Falha ao iniciar Gemini: {self.init_error}") from exc

    @property
    def available(self) -> bool:
        return self.client is not None

    async def explain_prediction(
        self, signal: dict[str, Any], prediction: dict[str, Any]
    ) -> dict[str, str]:
        fallback = self._local_explanation(signal, prediction)
        if not self.available:
            return {**fallback, "provider": "local_fallback"}

        prompt = f"""
Você é o redator operacional do CAIS, sistema de apoio à decisão da Guarda Municipal.
Converta somente os dados abaixo em linguagem clara para um gestor. Não altere números,
criticidade ou recomendação. Não use a expressão "previsão de crime" e não infira culpa,
intenção ou características pessoais. Responda JSON válido, sem markdown, com as chaves
title, summary e conversational_summary. O summary deve ter até 220 caracteres e o texto
conversacional até 550 caracteres.

SINAL: {json.dumps({k: signal.get(k) for k in ('source','local','category','description')}, ensure_ascii=False)}
RESULTADO LOCAL: {json.dumps(prediction, ensure_ascii=False)}
""".strip()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.settings.gemini_model,
                    contents=prompt,
                ),
                timeout=12,
            )
            parsed = self._parse_json(response.text)
            if not all(parsed.get(key) for key in ("title", "summary", "conversational_summary")):
                raise ValueError("Resposta do Gemini não contém os campos esperados")
            self.metrics.gemini_requests.labels("insight", "success").inc()
            return {
                "title": str(parsed["title"])[:180],
                "summary": str(parsed["summary"])[:500],
                "conversational_summary": str(parsed["conversational_summary"])[:1200],
                "provider": "gemini",
            }
        except Exception as exc:
            logger.warning("gemini_insight_fallback: %s", exc)
            self.metrics.gemini_requests.labels("insight", "fallback").inc()
            return {**fallback, "provider": "local_fallback"}

    async def answer(self, question: str, sources: list[dict[str, Any]]) -> tuple[str, str]:
        if not sources:
            return "Não encontrei evidências suficientes na base do CAIS para responder.", "local_fallback"
        if not self.available:
            return self._local_answer(question, sources), "local_fallback"

    async def write_report_summary(
        self,
        statistics: dict[str, Any],
        findings: list[dict[str, Any]],
        solution_options: list[dict[str, Any]],
    ) -> tuple[str, str]:
        fallback = self._local_report_summary(statistics, findings, solution_options)
        if not self.available:
            return fallback, "local_fallback"

        prompt = f"""
Você é o redator de um relatório gerencial do CAIS. Redija um resumo executivo em
português brasileiro com no máximo 900 caracteres. Use exclusivamente os dados fornecidos,
deixe claro que são sinais de demanda operacional (não previsão de crime), cite que há
múltiplas alternativas e preserve a decisão humana. Não invente preços, prazos, legislação,
causalidade nem resultados garantidos. Retorne somente o texto, sem título ou markdown.

ESTATÍSTICAS: {json.dumps(statistics, ensure_ascii=False)}
ACHADOS: {json.dumps(findings, ensure_ascii=False)}
ALTERNATIVAS: {json.dumps(solution_options, ensure_ascii=False)}
""".strip()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.settings.gemini_model,
                    contents=prompt,
                ),
                timeout=15,
            )
            summary = (response.text or "").strip()
            if not summary:
                raise ValueError("Resumo vazio")
            self.metrics.gemini_requests.labels("report", "success").inc()
            return summary[:1400], "gemini"
        except Exception as exc:
            logger.warning("gemini_report_fallback: %s", exc)
            self.metrics.gemini_requests.labels("report", "fallback").inc()
            return fallback, "local_fallback"

        context = "\n\n".join(
            f"[{index + 1}] {source['title']} — {source['snippet']}"
            for index, source in enumerate(sources)
        )
        prompt = f"""
Você é o agente conversacional do CAIS. Responda em português brasileiro para um gestor
operacional, com objetividade e sem inventar fatos. Use exclusivamente o contexto recuperado.
Quando citar evidência, use [1], [2] etc. Não faça previsão de crime, não atribua culpa e
não substitua a decisão humana. Se a evidência não bastar, diga isso explicitamente.

PERGUNTA: {question}

CONTEXTO:
{context}
""".strip()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.settings.gemini_model,
                    contents=prompt,
                ),
                timeout=15,
            )
            answer = (response.text or "").strip()
            if not answer:
                raise ValueError("Resposta vazia")
            self.metrics.gemini_requests.labels("chat", "success").inc()
            return answer, "gemini"
        except Exception as exc:
            logger.warning("gemini_chat_fallback: %s", exc)
            self.metrics.gemini_requests.labels("chat", "fallback").inc()
            return self._local_answer(question, sources), "local_fallback"

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
        return json.loads(cleaned)

    @staticmethod
    def _local_explanation(signal: dict[str, Any], prediction: dict[str, Any]) -> dict[str, str]:
        score = prediction["indicators"]["risk_score"]
        title = f"{signal['category'].replace('_', ' ').title()} — {signal['local']}"
        summary = (
            f"Demanda operacional estimada em {score:.0f}/100, com criticidade "
            f"{prediction['criticality'].upper()} em {signal['local']}."
        )
        factors: list[str] = []
        for driver in prediction["drivers"][:3]:
            factor = driver["factor"].replace("_", " ")
            factors.append(f"{factor}: {driver['value']}")
        factor_text = "; ".join(factors)
        conversational = (
            f"O modelo local estimou {score:.0f}% de necessidade contextual de atenção. "
            f"Os principais sinais observados foram {factor_text}. "
            f"Recomendação: {prediction['recommended_action']} A decisão final permanece com o gestor."
        )
        return {"title": title, "summary": summary, "conversational_summary": conversational}

    @staticmethod
    def _local_answer(question: str, sources: list[dict[str, Any]]) -> str:
        lines = ["Com base nas evidências mais relevantes disponíveis:"]
        for index, source in enumerate(sources[:3], start=1):
            lines.append(f"[{index}] {source['snippet']}")
        lines.append("A decisão operacional deve ser validada pelo gestor responsável.")
        return "\n\n".join(lines)

    @staticmethod
    def _local_report_summary(
        statistics: dict[str, Any],
        findings: list[dict[str, Any]],
        solution_options: list[dict[str, Any]],
    ) -> str:
        total = int(statistics.get("total_events", 0))
        high = int(statistics.get("by_criticality", {}).get("high", 0))
        top_local = statistics.get("top_locations", [{}])[0].get("local") if statistics.get("top_locations") else None
        location_text = f", com maior concentração observada em {top_local}" if top_local else ""
        return (
            f"No período analisado, o CAIS consolidou {total} sinal(is) de demanda operacional, "
            f"dos quais {high} foram classificados com criticidade alta{location_text}. "
            f"O relatório apresenta {len(findings)} achado(s) rastreável(is) e "
            f"{len(solution_options)} alternativas de resposta com custos, riscos e métricas a validar. "
            "Os dados apoiam o planejamento, mas não demonstram causalidade nem substituem a decisão "
            "do gestor, a análise técnica ou os controles de contratação pública."
        )
