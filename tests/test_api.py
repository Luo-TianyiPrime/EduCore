from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert Path(body["data_dir"]).is_dir()


def test_empty_config_is_not_an_error(client: TestClient) -> None:
    response = client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert body["endpoints"] == []
    assert body["defaults"]["llm"] is None
    assert body["secret_backend"] in {"keyring", "local_file"}


def test_models_crud_keeps_keys_out_of_settings(client: TestClient, data_dir: Path) -> None:
    # Generated at runtime so no credential literal exists in the repository.
    fake_key = f"test-{uuid.uuid4().hex}"
    payload = {
        "endpoints": [
            {
                "id": "llm1",
                "name": "测试 LLM",
                "kind": "llm",
                "provider": "openai_compat",
                "base_url": "https://api.example.com/v1",
                "model": "example-model",
                "api_key": fake_key,
            }
        ],
        "defaults": {"llm": "llm1"},
    }
    response = client.put("/api/models", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    endpoint = body["endpoints"][0]
    assert endpoint["has_api_key"] is True
    assert "api_key" not in endpoint
    assert body["defaults"]["llm"] == "llm1"

    settings_text = (data_dir / "config" / "settings.json").read_text(encoding="utf-8")
    assert fake_key not in settings_text

    # Updating without a key must preserve the stored secret.
    payload["endpoints"][0].pop("api_key")
    response = client.put("/api/models", json=payload)
    assert response.status_code == 200
    assert response.json()["endpoints"][0]["has_api_key"] is True

    # Explicit clear removes it.
    payload["endpoints"][0]["clear_api_key"] = True
    response = client.put("/api/models", json=payload)
    assert response.json()["endpoints"][0]["has_api_key"] is False


def test_default_must_match_kind(client: TestClient) -> None:
    payload = {
        "endpoints": [
            {
                "id": "emb1",
                "kind": "embedding",
                "provider": "openai_compat",
                "base_url": "https://api.example.com/v1",
                "model": "embedding-model",
            }
        ],
        "defaults": {"llm": "emb1"},
    }
    response = client.put("/api/models", json=payload)
    assert response.status_code == 400


def test_connection_test_reports_failure_without_faking_success(client: TestClient) -> None:
    payload = {
        "endpoints": [
            {
                "id": "offline",
                "kind": "llm",
                "provider": "openai_compat",
                "base_url": "http://127.0.0.1:1/v1",
                "model": "no-such-model",
                "timeout_s": 0.5,
            }
        ]
    }
    assert client.put("/api/models", json=payload).status_code == 200

    response = client.post("/api/models/test", json={"endpoint_id": "offline"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "model_unavailable" in body["detail"] or "超时" in body["detail"]


def test_settings_roundtrip(client: TestClient) -> None:
    response = client.put(
        "/api/settings",
        json={"theme": "dark", "retrieval": {"final_top": 12}, "max_task_steps": 42},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["theme"] == "dark"
    assert body["retrieval"]["final_top"] == 12
    assert body["max_task_steps"] == 42

    assert client.get("/api/settings").json()["theme"] == "dark"


def test_cross_origin_write_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/models/test",
        json={"endpoint_id": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_usage_endpoint(client: TestClient) -> None:
    response = client.get("/api/system/usage")
    assert response.status_code == 200
    assert response.json()["items"] == []