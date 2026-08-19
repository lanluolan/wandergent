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


def test_health_says_what_the_server_can_lend(monkeypatch) -> None:
    """The app labels its own form from this, so it has to be accurate both ways.

    Whether the model and endpoint are required is a fact about the server. Guessing it
    makes the form wrong in one direction or the other: a deployment carrying no account
    needs both filled in, one pointed at a provider without a key of its own does not.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "openai_model", "")

    llm = client.get("/health").json()["llm"]

    assert llm == {"key": False, "endpoint": True, "model": False}


def test_health_never_reveals_the_values_themselves(monkeypatch) -> None:
    # It answers "must I ask for this?", not "what is it?". A key especially.
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "sk-the-operators-own-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://internal.example.com/v1")
    monkeypatch.setattr(settings, "openai_model", "some-private-model")

    body = client.get("/health").text

    assert "sk-the-operators-own-key" not in body
    assert "internal.example.com" not in body
    assert "some-private-model" not in body
