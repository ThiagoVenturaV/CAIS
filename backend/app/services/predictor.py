from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from ..config import Settings


MODEL_INPUT_COLUMNS = [
    "local",
    "dia_semana",
    "hora_dia",
    "eventos_proximos",
    "historico_ocorrencias_7d",
    "iluminacao_ativa_pct",
    "iluminacao_fonte",
    "densidade_pessoas",
]


class Predictor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model: Any = None
        self.features: list[str] = []
        self.version = "unavailable"
        self.load_error: str | None = None
        self._load()

    @property
    def ready(self) -> bool:
        return self.model is not None

    @property
    def uses_pipeline(self) -> bool:
        return bool(self.model is not None and hasattr(self.model, "named_steps"))

    def _load(self) -> None:
        try:
            self.model = joblib.load(self.settings.model_path)
            if self.settings.feature_path.exists():
                self.features = list(joblib.load(self.settings.feature_path))
            stat = self.settings.model_path.stat()
            self.version = f"rf-{stat.st_mtime_ns:x}"
            self.load_error = None
        except Exception as exc:
            self.model = None
            self.load_error = f"{type(exc).__name__}: {exc}"

    def _frame(self, payload: dict[str, Any]) -> pd.DataFrame:
        return pd.DataFrame([{column: payload[column] for column in MODEL_INPUT_COLUMNS}])

    def predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError(f"Modelo indisponível: {self.load_error}")

        raw_frame = self._frame(payload)
        if self.uses_pipeline:
            model_frame = raw_frame
        else:
            # Compatibility with the repository's original classifier.
            # drop_first=False is essential for one-row inference: otherwise
            # the category observed in that row disappears entirely.
            encoded = pd.get_dummies(
                raw_frame, columns=["local", "iluminacao_fonte"], drop_first=False
            )
            model_frame = encoded.reindex(columns=self.features, fill_value=0)

        probability = float(self.model.predict_proba(model_frame)[0][1])
        requires_action = probability >= 0.5
        if probability >= self.settings.risk_high_threshold:
            criticality = "high"
        elif probability >= self.settings.risk_medium_threshold:
            criticality = "medium"
        else:
            criticality = "low"

        drivers: list[dict[str, Any]] = []
        if payload["densidade_pessoas"] >= 70:
            drivers.append({"factor": "densidade_pessoas", "value": payload["densidade_pessoas"], "direction": "increase"})
        if payload["eventos_proximos"]:
            drivers.append({"factor": "evento_proximo", "value": True, "direction": "increase"})
        if payload["historico_ocorrencias_7d"] >= 8:
            drivers.append({"factor": "historico_7d", "value": payload["historico_ocorrencias_7d"], "direction": "increase"})
        if payload["iluminacao_ativa_pct"] < 60:
            drivers.append({"factor": "iluminacao_ativa", "value": payload["iluminacao_ativa_pct"], "direction": "increase"})
        if not drivers:
            drivers.append({"factor": "contexto_sem_sinal_dominante", "value": True, "direction": "neutral"})

        recommendation = self._recommend(payload, criticality, requires_action)
        return {
            "probability": round(probability, 4),
            "requires_preventive_action": requires_action,
            "criticality": criticality,
            "drivers": drivers,
            "indicators": {
                "risk_score": round(probability * 100, 1),
                "density_index": payload["densidade_pessoas"],
                "recent_occurrences": payload["historico_ocorrencias_7d"],
                "active_lighting_pct": payload["iluminacao_ativa_pct"],
                "nearby_event": bool(payload["eventos_proximos"]),
            },
            "recommended_action": recommendation,
            "model_version": self.version,
        }

    @staticmethod
    def _recommend(payload: dict[str, Any], criticality: str, requires_action: bool) -> str:
        if criticality == "high":
            return "Revisar o contexto imediatamente e, se confirmado pelo gestor, reforçar a presença preventiva na área."
        if requires_action or criticality == "medium":
            return "Validar as fontes disponíveis e considerar ajuste preventivo do patrulhamento."
        return "Manter o monitoramento e a ronda ordinária, sem alteração automática de efetivo."

    def metrics(self) -> dict[str, Any]:
        path: Path = self.settings.model_metrics_path
        if not path.exists():
            return {
                "available": False,
                "message": "Execute python treinamento.py para gerar as métricas completas.",
                "model_version": self.version,
            }
        with path.open("r", encoding="utf-8") as file:
            metrics = json.load(file)
        metrics["available"] = True
        metrics["model_version"] = self.version
        return metrics
