"""Tests for accounts and bearer tokens.

This is the module that turns `user_id` from a claim into proof, so the tests are mostly
about the ways it could fail to do that: a password that is not really checked, a token
that outlives its session, an error message that leaks which accounts exist.
"""

import pathlib

import pytest
from fastapi.testclient import TestClient

from app.auth import bearer_token
from app.auth.store import AuthStore, Credentials, UsernameTaken, _hash_token
from app.main import app


@pytest.fixture
def store(tmp_path) -> AuthStore:
    return AuthStore(tmp_path / "auth.db")


def creds(username: str = "yuxia", password: str = "correct horse battery", **extra) -> Credentials:
    return Credentials(username=username, password=password, **extra)


def stored_bytes(store: AuthStore) -> bytes:
    """Everything actually on disk, including the write-ahead log.

    Reading only the `.db` file finds a stale snapshot: with `journal_mode=WAL` a fresh
    row lives in `-wal` until a checkpoint moves it. A "the secret is not in the file"
    assertion against the wrong file passes for the wrong reason, which is worse than
    having no assertion.

    Kept sync and called from async tests on purpose -- opening a file with `open` inside
    a coroutine blocks the loop, and the linter is right to say so.
    """
    path = pathlib.Path(store._path)
    return b"".join(
        candidate.read_bytes()
        for candidate in (path, path.with_name(path.name + "-wal"))
        if candidate.exists()
    )


# --- registration ------------------------------------------------------------------


async def test_registering_signs_you_in(store) -> None:
    """A register that leaves the client to make a second call has a window where the
    account exists and nobody can use it."""
    session = await store.register(creds())

    assert session.token
    assert session.account.username == "yuxia"
    assert await store.resolve(session.token) is not None


async def test_the_username_is_taken_case_insensitively(store) -> None:
    """ "Yuxia" and "yuxia" being different accounts is a phishing surface, not a feature."""
    await store.register(creds(username="yuxia"))

    with pytest.raises(UsernameTaken):
        await store.register(creds(username="YuXia"))


async def test_the_display_name_falls_back_to_the_username(store) -> None:
    session = await store.register(creds(display_name="   "))

    assert session.account.display_name == "yuxia"


async def test_a_short_password_is_rejected_before_it_reaches_the_store() -> None:
    with pytest.raises(ValueError):
        creds(password="short")


async def test_a_username_cannot_smuggle_punctuation() -> None:
    """It ends up in a byline other people read."""
    for bad in ("yu xia", "yu/xia", "<script>", "yu\nxia"):
        with pytest.raises(ValueError):
            creds(username=bad)


# --- passwords ---------------------------------------------------------------------


async def test_the_password_is_never_stored_in_the_clear(store) -> None:
    password = "correct horse battery"
    await store.register(creds(password=password))

    assert password.encode() not in stored_bytes(store)


async def test_the_stored_hash_is_salted(store) -> None:
    """Two people with the same password must not have the same row, or one crack is two
    accounts and a rainbow table works."""
    first = await store.register(creds(username="yuxia"))
    second = await store.register(creds(username="meilin"))

    def digest(account_id: str) -> tuple[str, str]:
        with store._connect() as connection:
            return connection.execute(
                "SELECT password_salt, password_hash FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()

    salt_a, hash_a = digest(first.account.id)
    salt_b, hash_b = digest(second.account.id)

    assert salt_a != salt_b
    assert hash_a != hash_b


async def test_the_iteration_count_is_recorded_not_assumed(store) -> None:
    """So the cost can be raised for new accounts without locking existing ones out."""
    session = await store.register(creds())

    with store._connect() as connection:
        (iterations,) = connection.execute(
            "SELECT iterations FROM accounts WHERE id = ?", (session.account.id,)
        ).fetchone()

    assert iterations >= 600_000


# --- login -------------------------------------------------------------------------


async def test_the_right_password_opens_a_second_session(store) -> None:
    """Signing in on a phone must not sign you out on a tablet."""
    first = await store.register(creds())
    second = await store.login(creds())

    assert second is not None
    assert second.token != first.token
    assert await store.resolve(first.token) is not None
    assert await store.resolve(second.token) is not None


async def test_the_wrong_password_is_refused(store) -> None:
    await store.register(creds())

    assert await store.login(creds(password="wrong horse battery")) is None


async def test_an_unknown_user_and_a_wrong_password_are_indistinguishable(store) -> None:
    """Telling them apart hands out a list of which accounts exist."""
    await store.register(creds(username="yuxia"))

    assert await store.login(creds(username="nobody")) is None
    assert await store.login(creds(username="yuxia", password="wrong horse battery")) is None


# --- tokens ------------------------------------------------------------------------


async def test_only_the_token_hash_is_stored(store) -> None:
    """A leaked database should not hand over live sessions."""
    session = await store.register(creds())

    raw = stored_bytes(store)

    assert session.token.encode() not in raw
    assert _hash_token(session.token).encode() in raw


async def test_logging_out_kills_the_token(store) -> None:
    session = await store.register(creds())

    await store.logout(session.token)

    assert await store.resolve(session.token) is None


async def test_logging_out_leaves_other_sessions_alone(store) -> None:
    phone = await store.register(creds())
    tablet = await store.login(creds())
    assert tablet is not None

    await store.logout(phone.token)

    assert await store.resolve(phone.token) is None
    assert await store.resolve(tablet.token) is not None


async def test_an_expired_token_stops_working_before_it_is_swept(store) -> None:
    """Expiry is checked on every resolve, not only pruned on login -- otherwise a lapsed
    token keeps working until something else happens to tidy up."""
    session = await store.register(creds())

    with store._connect() as connection:
        connection.execute(
            "UPDATE tokens SET expires_at = ? WHERE token_hash = ?",
            ("2020-01-01T00:00:00+00:00", _hash_token(session.token)),
        )

    assert await store.resolve(session.token) is None


async def test_nonsense_and_absence_both_resolve_to_nobody(store) -> None:
    assert await store.resolve("") is None
    assert await store.resolve("not-a-token") is None


async def test_logging_out_an_unknown_token_is_not_an_error(store) -> None:
    await store.logout("not-a-token")
    await store.logout("")


# --- the header ---------------------------------------------------------------------


def test_the_bearer_scheme_is_required_but_the_spelling_is_not() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer abc") == "abc"
    assert bearer_token("BEARER  abc  ") == "abc"


def test_anything_that_is_not_bearer_is_no_token() -> None:
    """A bare token with no scheme is a misconfigured client, not a session."""
    assert bearer_token("abc") == ""
    assert bearer_token("Basic abc") == ""
    assert bearer_token(None) == ""
    assert bearer_token("") == ""


# --- over HTTP ---------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    from app import auth, main

    accounts = AuthStore(tmp_path / "http-auth.db")
    monkeypatch.setattr(main, "auth_store", accounts)
    monkeypatch.setattr(auth, "store", accounts)
    return TestClient(app)


BODY = {"username": "yuxia", "password": "correct horse battery"}


def test_register_login_me_logout(client) -> None:
    created = client.post("/auth/register", json=BODY)
    assert created.status_code == 201
    token = created.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/auth/me", headers=headers).json()["username"] == "yuxia"

    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401

    assert client.post("/auth/login", json=BODY).status_code == 200


def test_a_taken_username_is_a_409_not_a_500(client) -> None:
    client.post("/auth/register", json=BODY)

    clash = client.post("/auth/register", json=BODY)

    assert clash.status_code == 409
    assert "taken" in clash.json()["detail"]


def test_a_failed_login_is_a_401(client) -> None:
    client.post("/auth/register", json=BODY)

    refused = client.post("/auth/login", json={**BODY, "password": "wrong horse battery"})

    assert refused.status_code == 401


def test_me_without_a_token_says_how_to_authenticate(client) -> None:
    refused = client.get("/auth/me")

    assert refused.status_code == 401
    assert refused.headers.get("WWW-Authenticate") == "Bearer"


def test_logging_out_an_unknown_token_over_http_is_still_204(client) -> None:
    """A 404 here would confirm which tokens are real."""
    assert client.post("/auth/logout", headers={"Authorization": "Bearer nope"}).status_code == 204


def test_the_response_never_carries_anything_password_shaped(client) -> None:
    body = client.post("/auth/register", json=BODY).text

    for leak in ("password", "salt", "hash", "iterations"):
        assert leak not in body, body
