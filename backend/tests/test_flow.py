from __future__ import annotations

import time

from fastapi.testclient import TestClient

from .conftest import login


SIGNAL = {
    "source": "COP",
    "local": "Praça do Arsenal",
    "category": "aglomeracao",
    "description": "Chamados correlacionados durante evento no entorno.",
    "dia_semana": 5,
    "hora_dia": 21,
    "eventos_proximos": 1,
    "historico_ocorrencias_7d": 12,
    "iluminacao_ativa_pct": 48,
    "iluminacao_fonte": "real",
    "densidade_pessoas": 88,
    "latitude": -8.0611,
    "longitude": -34.8711,
}


def wait_for_event(client: TestClient, headers: dict[str, str], status: str | None = None) -> dict:
    for _ in range(60):
        response = client.get("/api/v1/events", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        if items and (status is None or items[0]["status"] == status):
            return items[0]
        time.sleep(0.05)
    raise AssertionError(f"Evento não alcançou o estado esperado: {status}")


def test_complete_flow_with_non_blocking_acknowledgement(
    client: TestClient, manager_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/signals", json=SIGNAL, headers=manager_headers)
    assert response.status_code == 202
    assert response.json()["status"] == "processing"

    event = wait_for_event(client, manager_headers, "pending_approval")
    assert event["probability"] >= 0
    assert event["provider"] == "local_fallback"

    response = client.post(
        f"/api/v1/events/{event['id']}/approve",
        json={"guard_ids": ["grd_12", "grd_07"]},
        headers=manager_headers,
    )
    assert response.status_code == 202
    distributed = wait_for_event(client, manager_headers, "distributed")
    detail = client.get(f"/api/v1/events/{distributed['id']}", headers=manager_headers).json()
    assert {item["status"] for item in detail["dispatches"]} == {"sent"}
    assert len(detail["dispatches"]) == 2

    guard12_headers = {"Authorization": f"Bearer {login(client, 'guarda12@cais.recife.br')}"}
    guard07_headers = {"Authorization": f"Bearer {login(client, 'guarda07@cais.recife.br')}"}
    guard12_dispatch = client.get("/api/v1/guards/me/dispatches", headers=guard12_headers).json()["items"][0]
    guard07_dispatch = client.get("/api/v1/guards/me/dispatches", headers=guard07_headers).json()["items"][0]

    response = client.post(
        f"/api/v1/dispatches/{guard12_dispatch['id']}/respond",
        json={"action": "acknowledge"},
        headers=guard12_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"

    # Receipt by one guard never blocks or changes delivery to another.
    guard07_after = client.get("/api/v1/guards/me/dispatches", headers=guard07_headers).json()["items"][0]
    assert guard07_after["id"] == guard07_dispatch["id"]
    assert guard07_after["status"] == "sent"

    metrics = client.get("/api/v1/metrics")
    assert metrics.status_code == 200
    assert "cais_dispatch_ack_duration_seconds" in metrics.text
    assert "cais_pending_approval_events" in metrics.text


def test_authorization_and_validation(client: TestClient, manager_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/events").status_code == 401
    invalid = {**SIGNAL, "densidade_pessoas": 101}
    assert client.post("/api/v1/signals", json=invalid, headers=manager_headers).status_code == 422

    guard_headers = {"Authorization": f"Bearer {login(client, 'guarda12@cais.recife.br')}"}
    assert client.get("/api/v1/events", headers=guard_headers).status_code == 403


def test_model_accepts_unknown_category_values(client: TestClient, manager_headers: dict[str, str]) -> None:
    payload = {
        key: SIGNAL[key]
        for key in (
            "local", "dia_semana", "hora_dia", "eventos_proximos",
            "historico_ocorrencias_7d", "iluminacao_ativa_pct",
            "iluminacao_fonte", "densidade_pessoas",
        )
    }
    payload["local"] = "Local ainda não visto no treinamento"
    response = client.post("/api/v1/predicao-risco", json=payload, headers=manager_headers)
    assert response.status_code == 200, response.text
    assert 0 <= response.json()["probabilidade_risco"] <= 1
