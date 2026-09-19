from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import REPOSITORY_ROOT, Settings
from backend.app.main import create_app

from .conftest import login
from .test_flow import SIGNAL, wait_for_event


def wait_for_report(
    client: TestClient, headers: dict[str, str], attempts: int = 80
) -> dict:
    for _ in range(attempts):
        response = client.get("/api/v1/reports", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        if items:
            return items[0]
        time.sleep(0.05)
    raise AssertionError("Relatório não foi gerado")


def test_report_workflow_has_multiple_options_and_review_gates(
    client: TestClient, manager_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/signals", json=SIGNAL, headers=manager_headers)
    assert response.status_code == 202
    wait_for_event(client, manager_headers, "pending_approval")

    response = client.post(
        "/api/v1/reports/generate",
        json={"lookback_days": 7, "title": "Consolidado de teste"},
        headers=manager_headers,
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "processing"

    report_summary = wait_for_report(client, manager_headers)
    report = client.get(
        f"/api/v1/reports/{report_summary['id']}", headers=manager_headers
    ).json()
    assert report["title"] == "Consolidado de teste"
    assert report["statistics"]["total_events"] == 1
    assert len(report["solution_options"]) >= 3
    assert {item["id"] for item in report["solution_options"]} >= {
        "option_operational", "option_interagency", "option_structural"
    }
    draft = report["procurement_draft"]
    assert draft["status"] == "minuta_tecnica_nao_publicavel"
    assert "não é edital" in draft["warning"].lower()
    assert any(
        "assessoria jurídica" in gate for gate in draft["mandatory_review_gates"]
    )

    markdown = client.get(
        f"/api/v1/reports/{report['id']}/draft.md", headers=manager_headers
    )
    assert markdown.status_code == 200
    assert "Minuta técnica preliminar" in markdown.text
    assert "Alternativas comparadas" in markdown.text
    assert "não é edital" in markdown.text.lower()

    search = client.post(
        "/api/v1/assistant/search",
        json={"query": "piloto estruturado capacidade tecnologia", "top_k": 10},
        headers=manager_headers,
    )
    assert search.status_code == 200, search.text
    assert any(
        item["source"] == "relatorio_operacional"
        for item in search.json()["results"]
    )


def test_guard_cannot_access_reports(
    client: TestClient, manager_headers: dict[str, str]
) -> None:
    guard_headers = {
        "Authorization": f"Bearer {login(client, 'guarda12@cais.recife.br')}"
    }
    assert client.get("/api/v1/reports", headers=guard_headers).status_code == 403


def test_scheduler_generates_startup_report(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "scheduled-cais.db",
        model_path=REPOSITORY_ROOT / "motor_preditivo_seops.pkl",
        feature_path=REPOSITORY_ROOT / "features_modelo.pkl",
        model_metrics_path=REPOSITORY_ROOT / "modelo_metricas.json",
        seed_demo_data=False,
        gemini_mode="disabled",
        gemini_api_key=None,
        report_schedule_enabled=True,
        report_run_on_startup=True,
        report_startup_delay_seconds=0.01,
        report_interval_seconds=3600,
    )
    with TestClient(create_app(settings)) as scheduled_client:
        headers = {
            "Authorization": f"Bearer {login(scheduled_client, 'gestora@cais.recife.br')}"
        }
        report = wait_for_report(scheduled_client, headers)
        assert report["generation_reason"] == "startup"
        assert len(report["solution_options"]) == 3
        schedule = scheduled_client.get(
            "/api/v1/reports/schedule", headers=headers
        ).json()
        assert schedule["running"] is True
        assert schedule["next_run_at"] is not None
