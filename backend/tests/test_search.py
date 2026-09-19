from __future__ import annotations

from fastapi.testclient import TestClient


def test_hybrid_search_and_chat_fallback(
    client: TestClient, manager_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/assistant/search",
        json={"query": "Como funciona a aprovação humana e o envio aos guardas?", "top_k": 3},
        headers=manager_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["strategy"] == "bm25+lsa"
    assert data["results"]

    response = client.post(
        "/api/v1/assistant/chat",
        json={"question": "O Gemini decide o envio do alerta?", "top_k": 3},
        headers=manager_headers,
    )
    assert response.status_code == 200, response.text
    chat = response.json()
    assert chat["provider"] == "local_fallback"
    assert chat["sources"]
    assert chat["answer"]
