"""Regression test for the health-check endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "Wandergent"


def test_health_never_reveals_the_config(monkeypatch) -> None:
    """A liveness probe is unauthenticated, so it must say nothing about the account.

    The endpoint, the model name and above all the key are operational detail; anyone
    who can reach the port can read this body.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "sk-the-operators-own-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://internal.example.com/v1")
    monkeypatch.setattr(settings, "openai_model", "some-private-model")

    body = client.get("/health").text

    assert "sk-the-operators-own-key" not in body
    assert "internal.example.com" not in body
    assert "some-private-model" not in body
