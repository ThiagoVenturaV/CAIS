from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import REPOSITORY_ROOT, Settings
from backend.app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_path=tmp_path / "test-cais.db",
        model_path=REPOSITORY_ROOT / "motor_preditivo_seops.pkl",
        feature_path=REPOSITORY_ROOT / "features_modelo.pkl",
        model_metrics_path=REPOSITORY_ROOT / "modelo_metricas.json",
        cors_origins=["http://localhost:5500"],
        seed_demo_data=False,
        gemini_mode="disabled",
        gemini_api_key=None,
        report_schedule_enabled=False,
        report_run_on_startup=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def login(client: TestClient, email: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "cais2026"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def manager_headers(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, 'gestora@cais.recife.br')}"}
