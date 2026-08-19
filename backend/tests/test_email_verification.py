"""Tests for proving an email address.

Verification is only worth building if it *blocks* something, and here it blocks password
reset. That rule is what closes the takeover this feature exists for: register with a
stranger's address by typo or on purpose, and without the rule the stranger can reset
their way into the account.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app.auth.store import AuthStore, Credentials
from app.main import app


class CapturingMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    @property
    def last_code(self) -> str:
        line = next(line for line in self.sent[-1][2].splitlines() if "code is" in line)
        return line.split(":")[-1].strip()


@pytest.fixture
def store(tmp_path) -> AuthStore:
    return AuthStore(tmp_path / "auth.db")


def creds(**overrides) -> Credentials:
    return Credentials(
        **{
            "username": "yuxia",
            "password": "correct horse battery",
            "email": "yu@example.com",
            **overrides,
        }
    )


# --- the rule that makes it matter ---------------------------------------------------


async def test_an_unverified_address_cannot_reset(store) -> None:
    """The takeover this closes: someone types a stranger's address, and without this rule
    the stranger can reset their way into the account."""
    await store.register(creds())

    assert await store.start_reset("yu@example.com") is None


async def test_a_verified_address_can(store) -> None:
    session = await store.register(creds())
    code = await store.start_verification(session.account.id, "yu@example.com")

    assert await store.complete_verification(session.account.id, code) is True
    assert await store.start_reset("yu@example.com") is not None


# --- proving it ----------------------------------------------------------------------


async def test_a_new_account_starts_unproven(store) -> None:
    session = await store.register(creds())

    assert session.account.email == "yu@example.com"
    assert session.account.email_verified is False


async def test_the_flag_survives_a_new_session(store) -> None:
    session = await store.register(creds())
    code = await store.start_verification(session.account.id, "yu@example.com")
    await store.complete_verification(session.account.id, code)

    signed_in = await store.login(creds())

    assert signed_in is not None
    assert signed_in.account.email_verified is True
    assert (await store.resolve(signed_in.token)).email_verified is True


async def test_a_code_belongs_to_one_account(store) -> None:
    """Scoped to the account presenting it. Checking the code alone would make it a bearer
    credential for somebody else's address."""
    mine = await store.register(creds())
    theirs = await store.register(creds(username="meilin", email="mei@example.com"))
    code = await store.start_verification(mine.account.id, "yu@example.com")

    assert await store.complete_verification(theirs.account.id, code) is False
    assert await store.complete_verification(mine.account.id, code) is True


async def test_changing_the_address_after_asking_invalidates_the_code(store) -> None:
    """The address is taken from the *code*, not the account row. Otherwise a code mailed
    to one address could prove a different one that was swapped in afterwards -- which is
    the takeover, rebuilt out of the fix for it."""
    session = await store.register(creds())
    code = await store.start_verification(session.account.id, "yu@example.com")
    await store.set_email(session.account.id, "someone-else@example.com")

    assert await store.complete_verification(session.account.id, code) is False


async def test_an_expired_code_is_refused(store) -> None:
    session = await store.register(creds())
    code = await store.start_verification(session.account.id, "yu@example.com")
    with store._connect() as connection:
        connection.execute("UPDATE verify_codes SET expires_at = ?", ("2020-01-01T00:00:00+00:00",))

    assert await store.complete_verification(session.account.id, code) is False


async def test_nothing_to_verify_yields_no_code(store) -> None:
    session = await store.register(creds(email=""))

    assert await store.start_verification(session.account.id, "") is None


# --- changing the address -------------------------------------------------------------


async def test_a_changed_address_starts_unproven_again(store) -> None:
    """An address is proven only for as long as it is the one that was proven. Keeping the
    flag across a change would let anyone with a session move a verified account onto an
    address they do not own."""
    session = await store.register(creds())
    code = await store.start_verification(session.account.id, "yu@example.com")
    await store.complete_verification(session.account.id, code)

    assert await store.set_email(session.account.id, "new@example.com") is True

    signed_in = await store.login(creds())
    assert signed_in is not None
    assert signed_in.account.email == "new@example.com"
    assert signed_in.account.email_verified is False
    # And the old address stops being a way in.
    assert await store.start_reset("yu@example.com") is None


async def test_two_accounts_cannot_share_an_address(store) -> None:
    """One reset request with two possible answers is not a thing the flow can express."""
    await store.register(creds())
    other = await store.register(creds(username="meilin", email="mei@example.com"))

    assert await store.set_email(other.account.id, "yu@example.com") is False


async def test_an_address_can_be_cleared(store) -> None:
    """Opting out of being recoverable is allowed, and must not collide with every other
    address-less account."""
    first = await store.register(creds())
    second = await store.register(creds(username="meilin", email="mei@example.com"))

    assert await store.set_email(first.account.id, "") is True
    assert await store.set_email(second.account.id, "") is True


# --- over HTTP -------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> tuple[TestClient, CapturingMailer]:
    from app import auth, mail, main

    monkeypatch.setattr(importlib.import_module("app.auth.store"), "PBKDF2_ITERATIONS", 1_000)
    accounts = AuthStore(tmp_path / "http-auth.db")
    monkeypatch.setattr(main, "auth_store", accounts)
    monkeypatch.setattr(auth, "store", accounts)
    postbox = CapturingMailer()
    monkeypatch.setattr(mail, "mailer", postbox)
    return TestClient(app), postbox


BODY = {"username": "yuxia", "password": "correct horse battery", "email": "yu@example.com"}


def test_registering_mails_a_code(client) -> None:
    api, postbox = client

    api.post("/auth/register", json=BODY)

    assert len(postbox.sent) == 1
    to, subject, body = postbox.sent[0]
    assert to == "yu@example.com"
    assert "Confirm" in subject
    assert "did not create an account" in body


def test_registering_without_an_address_mails_nothing(client) -> None:
    api, postbox = client

    api.post("/auth/register", json={"username": "yuxia", "password": "correct horse battery"})

    assert postbox.sent == []


def test_me_reports_the_status(client) -> None:
    api, postbox = client
    token = api.post("/auth/register", json=BODY).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert api.get("/auth/me", headers=headers).json()["email_verified"] is False

    api.post("/auth/verify", json={"code": postbox.last_code}, headers=headers)

    assert api.get("/auth/me", headers=headers).json()["email_verified"] is True


def test_verifying_needs_a_session(client) -> None:
    """A code is proof of reaching an inbox, not of being signed in. Both are required."""
    api, postbox = client
    api.post("/auth/register", json=BODY)

    assert api.post("/auth/verify", json={"code": postbox.last_code}).status_code == 401


def test_a_wrong_code_is_a_400(client) -> None:
    api, _ = client
    token = api.post("/auth/register", json=BODY).json()["token"]

    refused = api.post(
        "/auth/verify",
        json={"code": "AAAAAAAA"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert refused.status_code == 400


def test_a_code_can_be_resent(client) -> None:
    api, postbox = client
    token = api.post("/auth/register", json=BODY).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert api.post("/auth/verify/request", headers=headers).status_code == 204
    assert len(postbox.sent) == 2
    # The newest is the one that works.
    assert (
        api.post("/auth/verify", json={"code": postbox.last_code}, headers=headers).status_code
        == 204
    )


def test_resending_needs_a_session_so_it_cannot_enumerate(client) -> None:
    """Taking an address here would rebuild the oracle the reset endpoint avoids."""
    api, _ = client

    assert api.post("/auth/verify/request").status_code == 401


def test_changing_the_address_over_http(client) -> None:
    api, postbox = client
    token = api.post("/auth/register", json=BODY).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    api.post("/auth/verify", json={"code": postbox.last_code}, headers=headers)

    changed = api.post("/auth/email", json={"email": "new@example.com"}, headers=headers)

    assert changed.status_code == 200
    assert changed.json()["email"] == "new@example.com"
    assert changed.json()["email_verified"] is False
    # And a code went to the new address, not the old one.
    assert postbox.sent[-1][0] == "new@example.com"


def test_a_taken_address_is_a_409(client) -> None:
    api, postbox = client
    api.post("/auth/register", json=BODY)
    other = api.post(
        "/auth/register",
        json={"username": "meilin", "password": "another good one", "email": "mei@example.com"},
    ).json()

    clash = api.post(
        "/auth/email",
        json={"email": "yu@example.com"},
        headers={"Authorization": f"Bearer {other['token']}"},
    )

    assert clash.status_code == 409


def test_an_unverified_account_gets_no_reset_code(client) -> None:
    """End to end: the whole reason the flag exists."""
    api, postbox = client
    api.post("/auth/register", json=BODY)
    postbox.sent.clear()

    assert api.post("/auth/reset/request", json={"email": "yu@example.com"}).status_code == 204
    assert postbox.sent == []
