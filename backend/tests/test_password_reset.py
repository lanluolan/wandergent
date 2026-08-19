"""Tests for password reset.

Reset is the one flow that hands out access on the strength of an email address, so most
of what matters is what it refuses to say and what it takes away. The mailer is captured
rather than sent -- the delivery layer is `test_mail.py`'s job.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app.auth.store import CODE_LENGTH, AuthStore, Credentials
from app.main import app
from app.ratelimit import RESET_CONFIRM_PER_ADDRESS, RESET_PER_ADDRESS


class CapturingMailer:
    """Keeps what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    @property
    def last_code(self) -> str:
        body = self.sent[-1][2]
        line = next(line for line in body.splitlines() if "code is" in line)
        return line.split(":")[-1].strip()


@pytest.fixture
def store(tmp_path) -> AuthStore:
    return AuthStore(tmp_path / "auth.db")


async def registered_and_verified(store: AuthStore, **overrides) -> str:
    """Register, then prove the address -- because reset only works for proven ones."""
    session = await store.register(creds(**overrides))
    code = await store.start_verification(session.account.id, session.account.email)
    assert code is not None
    assert await store.complete_verification(session.account.id, code)
    return session.account.id


def creds(**overrides) -> Credentials:
    return Credentials(
        **{
            "username": "yuxia",
            "password": "correct horse battery",
            "email": "yu@example.com",
            **overrides,
        }
    )


# --- the store ----------------------------------------------------------------------


async def test_a_code_is_issued_for_a_known_address(store) -> None:
    await registered_and_verified(store)

    outcome = await store.start_reset("yu@example.com")

    assert outcome is not None
    account, code = outcome
    assert account.username == "yuxia"
    assert len(code) == CODE_LENGTH


async def test_the_address_is_matched_case_insensitively(store) -> None:
    """People capitalise their own address inconsistently; a reset that fails for it is
    indistinguishable from a reset that failed for a real reason."""
    await registered_and_verified(store, email="Yu@Example.COM")

    assert await store.start_reset("yu@example.com") is not None


async def test_an_unknown_address_yields_nothing(store) -> None:
    await registered_and_verified(store)

    assert await store.start_reset("stranger@example.com") is None


async def test_an_account_without_an_address_cannot_be_reset(store) -> None:
    """Blank must not match blank. Otherwise asking to reset "" would hand back whichever
    address-less account the database happened to return first."""
    await store.register(creds(email=""))

    assert await store.start_reset("") is None
    assert await store.start_reset("   ") is None


async def test_the_code_sets_the_new_password(store) -> None:
    await registered_and_verified(store)
    _, code = await store.start_reset("yu@example.com")

    assert await store.complete_reset(code, "a whole new password") is True

    assert await store.login(creds(password="a whole new password")) is not None
    assert await store.login(creds()) is None


async def test_a_code_works_once(store) -> None:
    await registered_and_verified(store)
    _, code = await store.start_reset("yu@example.com")
    await store.complete_reset(code, "a whole new password")

    assert await store.complete_reset(code, "yet another password") is False


async def test_every_session_is_revoked(store) -> None:
    """The case this feature exists for is an account someone else got into. Leaving their
    bearer token alive would make resetting the password pointless."""
    await registered_and_verified(store)
    phone = await store.login(creds())
    tablet = await store.login(creds())
    assert phone is not None and tablet is not None
    _, code = await store.start_reset("yu@example.com")

    await store.complete_reset(code, "a whole new password")

    assert await store.resolve(phone.token) is None
    assert await store.resolve(tablet.token) is None


async def test_asking_twice_leaves_only_the_newest_code(store) -> None:
    """Someone who asks again because the first mail was slow should find the newest code
    works, rather than learning later which of two they were supposed to use."""
    await registered_and_verified(store)
    _, first = await store.start_reset("yu@example.com")
    _, second = await store.start_reset("yu@example.com")

    assert await store.complete_reset(first, "a whole new password") is False
    assert await store.complete_reset(second, "a whole new password") is True


async def test_an_expired_code_is_refused(store) -> None:
    await registered_and_verified(store)
    _, code = await store.start_reset("yu@example.com")
    with store._connect() as connection:
        connection.execute("UPDATE reset_codes SET expires_at = ?", ("2020-01-01T00:00:00+00:00",))

    assert await store.complete_reset(code, "a whole new password") is False


async def test_a_code_that_was_never_issued_is_refused(store) -> None:
    await registered_and_verified(store)

    assert await store.complete_reset("AAAAAAAA", "a whole new password") is False
    assert await store.complete_reset("", "a whole new password") is False


async def test_the_code_is_stored_hashed(store) -> None:
    """A leaked database should not be a pile of live reset codes."""
    await registered_and_verified(store)
    _, code = await store.start_reset("yu@example.com")

    with store._connect() as connection:
        (stored,) = connection.execute("SELECT code_hash FROM reset_codes").fetchone()

    assert stored != code


async def test_the_code_avoids_characters_people_confuse(store) -> None:
    """It is read off a screen and typed into a phone. A code containing both O and 0 is
    a support request waiting to happen."""
    await registered_and_verified(store)

    for _ in range(30):
        _, code = await store.start_reset("yu@example.com")
        assert not set(code) & set("O0I1")


# --- an existing database gains the column ------------------------------------------


async def test_an_older_database_is_migrated_in_place(tmp_path) -> None:
    """Accounts existed before email did. The column is added by ALTER, and adding it
    twice must not fail on the second start."""
    path = tmp_path / "old.db"
    older = AuthStore(path)
    await older.register(Credentials(username="yuxia", password="correct horse battery"))

    reopened = AuthStore(path)
    session = await reopened.login(Credentials(username="yuxia", password="correct horse battery"))

    assert session is not None
    assert session.account.email == ""


# --- over HTTP ----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> tuple[TestClient, CapturingMailer]:
    from app import auth, mail, main

    store_module = importlib.import_module("app.auth.store")
    monkeypatch.setattr(store_module, "PBKDF2_ITERATIONS", 1_000)

    accounts = AuthStore(tmp_path / "http-auth.db")
    monkeypatch.setattr(main, "auth_store", accounts)
    monkeypatch.setattr(auth, "store", accounts)

    postbox = CapturingMailer()
    monkeypatch.setattr(mail, "mailer", postbox)
    return TestClient(app), postbox


BODY = {"username": "yuxia", "password": "correct horse battery", "email": "yu@example.com"}


def sign_up_verified(api: TestClient, postbox: CapturingMailer, body: dict = BODY) -> dict:
    """Register and prove the address, because reset only works for proven ones."""
    created = api.post("/auth/register", json=body)
    assert created.status_code == 201, created.text
    headers = {"Authorization": f"Bearer {created.json()['token']}"}
    # Registration mails a confirmation code as its last act.
    proved = api.post("/auth/verify", json={"code": postbox.last_code}, headers=headers)
    assert proved.status_code == 204, proved.text
    return headers


def test_the_whole_flow(client) -> None:
    api, postbox = client
    sign_up_verified(api, postbox)

    assert api.post("/auth/reset/request", json={"email": "yu@example.com"}).status_code == 204

    confirmed = api.post(
        "/auth/reset/confirm",
        json={"code": postbox.last_code, "password": "a whole new password"},
    )

    assert confirmed.status_code == 204
    assert (
        api.post(
            "/auth/login", json={"username": "yuxia", "password": "a whole new password"}
        ).status_code
        == 200
    )


def test_an_unknown_address_answers_identically(client) -> None:
    """Answering differently makes this a way to ask "does this person have an account
    here", which is the one question a reset endpoint must not answer."""
    api, postbox = client
    sign_up_verified(api, postbox)
    postbox.sent.clear()

    known = api.post("/auth/reset/request", json={"email": "yu@example.com"})
    unknown = api.post("/auth/reset/request", json={"email": "stranger@example.com"})

    assert known.status_code == unknown.status_code == 204
    assert known.text == unknown.text
    # And only the real one produced mail.
    assert len(postbox.sent) == 1


def test_the_mail_says_what_to_ignore(client) -> None:
    """It lands in the inbox of someone who may not have asked for it."""
    api, postbox = client
    sign_up_verified(api, postbox)
    postbox.sent.clear()

    api.post("/auth/reset/request", json={"email": "yu@example.com"})

    _, subject, body = postbox.sent[0]
    assert "Wandergent" in subject
    assert "did not ask for this" in body


def test_a_wrong_code_is_a_400(client) -> None:
    api, postbox = client
    sign_up_verified(api, postbox)

    refused = api.post(
        "/auth/reset/confirm", json={"code": "AAAAAAAA", "password": "a whole new password"}
    )

    assert refused.status_code == 400


def test_a_short_new_password_is_refused(client) -> None:
    """The reset path must not become a way around the password policy."""
    api, postbox = client
    sign_up_verified(api, postbox)
    api.post("/auth/reset/request", json={"email": "yu@example.com"})

    refused = api.post("/auth/reset/confirm", json={"code": postbox.last_code, "password": "short"})

    assert refused.status_code == 422


def test_requests_are_capped_per_address(client) -> None:
    """Unlimited, this endpoint is a way to have a stranger's inbox flooded using the
    service's own good name to do it."""
    api, postbox = client
    sign_up_verified(api, postbox)

    for _ in range(RESET_PER_ADDRESS.count):
        assert api.post("/auth/reset/request", json={"email": "yu@example.com"}).status_code == 204

    assert api.post("/auth/reset/request", json={"email": "yu@example.com"}).status_code == 429


def test_guesses_are_capped_per_address(client) -> None:
    """With the code's own entropy, this is what makes eight characters safe."""
    api, _ = client

    for _ in range(RESET_CONFIRM_PER_ADDRESS.count):
        api.post("/auth/reset/confirm", json={"code": "AAAAAAAA", "password": "a whole new one"})

    refused = api.post(
        "/auth/reset/confirm", json={"code": "AAAAAAAA", "password": "a whole new one"}
    )

    assert refused.status_code == 429
