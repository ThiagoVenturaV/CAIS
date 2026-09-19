from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class Settings:
    app_name: str = "CAIS — Central de Alerta e Inteligência para Segurança"
    environment: str = field(default_factory=lambda: os.getenv("CAIS_ENV", "development"))
    api_prefix: str = "/api/v1"
    database_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("CAIS_DATABASE_PATH", str(REPOSITORY_ROOT / "data" / "cais.db"))
        )
    )
    model_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("CAIS_MODEL_PATH", str(REPOSITORY_ROOT / "motor_preditivo_seops.pkl"))
        )
    )
    feature_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("CAIS_FEATURE_PATH", str(REPOSITORY_ROOT / "features_modelo.pkl"))
        )
    )
    model_metrics_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("CAIS_MODEL_METRICS_PATH", str(REPOSITORY_ROOT / "modelo_metricas.json"))
        )
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _csv(
            os.getenv("CAIS_CORS_ORIGINS"),
            ["http://localhost:3000", "http://localhost:5500", "http://127.0.0.1:5500"],
        )
    )
    ingest_api_key: str | None = field(default_factory=lambda: os.getenv("CAIS_INGEST_API_KEY"))
    session_hours: int = field(default_factory=lambda: int(os.getenv("CAIS_SESSION_HOURS", "12")))
    seed_demo_data: bool = field(
        default_factory=lambda: _as_bool(os.getenv("CAIS_SEED_DEMO_DATA"), True)
    )
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    gemini_mode: str = field(default_factory=lambda: os.getenv("GEMINI_MODE", "auto").lower())
    risk_medium_threshold: float = field(
        default_factory=lambda: float(os.getenv("CAIS_RISK_MEDIUM_THRESHOLD", "0.35"))
    )
    risk_high_threshold: float = field(
        default_factory=lambda: float(os.getenv("CAIS_RISK_HIGH_THRESHOLD", "0.65"))
    )
    report_schedule_enabled: bool = field(
        default_factory=lambda: _as_bool(os.getenv("CAIS_REPORT_SCHEDULE_ENABLED"), True)
    )
    report_interval_seconds: int = field(
        default_factory=lambda: max(60, int(os.getenv("CAIS_REPORT_INTERVAL_SECONDS", "86400")))
    )
    report_lookback_days: int = field(
        default_factory=lambda: max(1, int(os.getenv("CAIS_REPORT_LOOKBACK_DAYS", "7")))
    )
    report_run_on_startup: bool = field(
        default_factory=lambda: _as_bool(os.getenv("CAIS_REPORT_RUN_ON_STARTUP"), True)
    )
    report_startup_delay_seconds: float = field(
        default_factory=lambda: max(
            0.0, float(os.getenv("CAIS_REPORT_STARTUP_DELAY_SECONDS", "2"))
        )
    )

    @property
    def allow_all_origins(self) -> bool:
        return "*" in self.cors_origins


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
