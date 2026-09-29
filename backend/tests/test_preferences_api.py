"""Preference management must never expose or delete another account's memory."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth.store import Credentials
from app.main import app


async def account(username: str) -> tuple[str, dict[str, str]]:
    session = await main.auth_store.register(
        Credentials(username=username, password="correct horse battery")
    )
    return session.account.id, {"Authorization": f"Bearer {session.token}"}


async def test_list_includes_all_preferences_and_is_private() -> None:
    owner, headers = await account("traveller")
    other, _ = await account("someoneelse")
    texts = [f"preference {index}" for index in range(25)]
    await main.memory_store.remember(owner, texts)
    await main.memory_store.remember(other, ["private preference"])

    response = TestClient(app).get("/preferences", headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert {p["text"] for p in response.json()} == set(texts)
    assert all(len(p["id"]) == 64 and p["created_at"] for p in response.json())


async def test_delete_is_scoped_exact_and_idempotent() -> None:
    owner, headers = await account("traveller")
    other, other_headers = await account("someoneelse")
    text = "prefers quiet museums"
    await main.memory_store.remember(owner, [text, "avoids hiking"])
    client = TestClient(app)
    key = main.preference_id(text)

    assert client.delete(f"/preferences/{key}", headers=other_headers).status_code == 204
    assert len(await main.memory_store.recall(owner)) == 2
    await main.memory_store.remember(other, [text])
    assert client.delete(f"/preferences/{key}", headers=headers).status_code == 204
    assert client.delete(f"/preferences/{key}", headers=headers).status_code == 204
    assert [p.text for p in await main.memory_store.recall(owner)] == ["avoids hiking"]
    assert [p.text for p in await main.memory_store.recall(other)] == [text]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_management_requires_a_valid_session(headers: dict[str, str]) -> None:
    client = TestClient(app)
    assert client.get("/preferences", headers=headers).status_code == 401
    assert client.delete("/preferences/unknown", headers=headers).status_code == 401


async def test_storage_failure_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _, headers = await account("traveller")

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("sensitive database path")

    monkeypatch.setattr(main.memory_store, "recall", unavailable)
    client = TestClient(app)
    for response in (
        client.get("/preferences", headers=headers),
        client.delete("/preferences/unknown", headers=headers),
    ):
        assert response.status_code == 503
        assert "sensitive" not in response.text
