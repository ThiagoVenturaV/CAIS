from __future__ import annotations

import asyncio
import time
from typing import Any

from ..database import Database
from ..domain import DomainEvent
from ..event_bus import EventBus
from ..observability import Metrics
from .gemini_service import GeminiService
from .predictor import Predictor


class InsightWorkflow:
    def __init__(
        self,
        database: Database,
        bus: EventBus,
        predictor: Predictor,
        gemini: GeminiService,
        metrics: Metrics,
    ):
        self.database = database
        self.bus = bus
        self.predictor = predictor
        self.gemini = gemini
        self.metrics = metrics

    async def on_signal_received(self, domain_event: DomainEvent) -> None:
        signal = self.database.get_signal(domain_event.aggregate_id)
        if not signal:
            raise LookupError(f"Sinal {domain_event.aggregate_id} não encontrado")
        started = time.perf_counter()
        prediction = await asyncio.to_thread(self.predictor.predict, signal)
        self.metrics.prediction_duration.observe(time.perf_counter() - started)
        self.metrics.predictions.labels(prediction["criticality"]).inc()
        await self.bus.publish(
            DomainEvent(
                event_type="risk.assessed",
                aggregate_id=signal["id"],
                payload={
                    "probability": prediction["probability"],
                    "criticality": prediction["criticality"],
                    "model_version": prediction["model_version"],
                },
            )
        )
        narrative = await self.gemini.explain_prediction(signal, prediction)
        insight = {**prediction, **narrative}
        operational_event_id = self.database.create_operational_event(signal, insight)
        await self.bus.publish(
            DomainEvent(
                event_type="insight.created",
                aggregate_id=operational_event_id,
                payload={
                    "operational_event_id": operational_event_id,
                    "signal_id": signal["id"],
                    "criticality": prediction["criticality"],
                    "local": signal["local"],
                    "status": "pending_approval",
                },
            )
        )


class DispatchWorkflow:
    def __init__(self, database: Database, bus: EventBus, metrics: Metrics):
        self.database = database
        self.bus = bus
        self.metrics = metrics

    async def on_event_approved(self, domain_event: DomainEvent) -> None:
        operational_event = self.database.get_operational_event(domain_event.aggregate_id)
        if not operational_event:
            raise LookupError(f"Evento {domain_event.aggregate_id} não encontrado")
        requested_ids = domain_event.payload.get("guard_ids") or []
        available = self.database.list_guards(availability="available")
        available_ids = {guard["id"] for guard in available}
        guard_ids = [guard_id for guard_id in requested_ids if guard_id in available_ids]
        if not guard_ids:
            guard_ids = self._compatible_guards(operational_event, available)

        dispatches = self.database.create_dispatches(operational_event["id"], guard_ids)
        if not dispatches:
            await self.bus.publish(
                DomainEvent(
                    event_type="dispatch.unassigned",
                    aggregate_id=operational_event["id"],
                    payload={"reason": "Nenhum guarda disponível ou elegível"},
                )
            )
            return

        # Every recipient is notified immediately. Acknowledgement is independent
        # and never blocks delivery to the other guards.
        for dispatch in dispatches:
            self.metrics.dispatches.labels("sent").inc()
            await self.bus.publish(
                DomainEvent(
                    event_type="dispatch.sent",
                    aggregate_id=operational_event["id"],
                    payload={
                        "dispatch_id": dispatch["id"],
                        "guard_id": dispatch["guard_id"],
                        "guard_code": dispatch["guard_code"],
                        "local": operational_event["local"],
                    },
                )
            )

    @staticmethod
    def _compatible_guards(event: dict[str, Any], guards: list[dict[str, Any]]) -> list[str]:
        category = f"{event.get('category', '')} {event.get('title', '')}".lower()
        preferred: tuple[str, ...] = ()
        if any(term in category for term in ("turis", "arsenal", "recife antigo")):
            preferred = ("turística", "gto")
        elif any(term in category for term in ("patrim", "vandal", "depreda")):
            preferred = ("patrimônio", "gto")
        selected = [
            guard["id"] for guard in guards
            if not preferred or any(term in guard["specialization"].lower() for term in preferred)
        ]
        return selected or [guard["id"] for guard in guards]
