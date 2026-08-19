"""Tests for rate limiting.

The window is driven by an injected clock rather than by sleeping. A limiter tested with
real `sleep` makes the suite slower every time a limit is added, and the thing being
tested -- "does the window actually slide" -- is about arithmetic, not about time passing.
"""

import pytest
from fastapi.testclient import TestClient

from app.auth.store import AuthStore
from app.main import app
from app.ratelimit import (
    ALL_LIMITS,
    LOGIN_FAILURES_PER_USER,
    LOGIN_PER_ADDRESS,
    PUBLISH,
    REGISTER_PER_ADDRESS,
    Limit,
    RateLimiter,
    limiter,
    plan_daily_global,
)


class Clock:
    """A hand-cranked monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def limits(clock) -> RateLimiter:
    return RateLimiter(clock=clock)


THREE_PER_MINUTE = Limit(count=3, window=60)


# --- the window ---------------------------------------------------------------------


def test_the_allowance_is_spent_then_refused(limits) -> None:
    for _ in range(3):
        assert limits.check("k", THREE_PER_MINUTE) is None

    assert limits.check("k", THREE_PER_MINUTE) is not None


def test_the_wait_is_until_the_oldest_hit_ages_out(limits, clock) -> None:
    """A caller told to wait 60 s when 5 s would do will either wait too long or ignore
    the number. Either way the header stops being useful."""
    for _ in range(3):
        limits.check("k", THREE_PER_MINUTE)
    clock.advance(55)

    assert limits.check("k", THREE_PER_MINUTE) == pytest.approx(5.0)


def test_the_window_slides_rather_than_resetting(limits, clock) -> None:
    """A fixed window would hand back the whole allowance at the boundary, letting a
    caller spend twice the limit across it. Hits have to age out one at a time, so they
    are spaced here -- three at the same instant would all expire together and the test
    would pass against a fixed window too."""
    limits.check("k", THREE_PER_MINUTE)
    clock.advance(30)
    limits.check("k", THREE_PER_MINUTE)
    clock.advance(20)
    limits.check("k", THREE_PER_MINUTE)
    assert limits.check("k", THREE_PER_MINUTE) is not None

    # Past the first hit's window, but not the other two.
    clock.advance(11)
    assert limits.check("k", THREE_PER_MINUTE) is None
    assert limits.check("k", THREE_PER_MINUTE) is not None


def test_a_refusal_does_not_extend_the_wait(limits, clock) -> None:
    """Recording refused attempts would mean a client polling every second never gets
    back in -- a limit that punishes rather than a ceiling on work done."""
    for _ in range(3):
        limits.check("k", THREE_PER_MINUTE)
    for _ in range(20):
        limits.check("k", THREE_PER_MINUTE)

    clock.advance(61)
    assert limits.check("k", THREE_PER_MINUTE) is None


def test_keys_are_independent(limits) -> None:
    for _ in range(3):
        limits.check("a", THREE_PER_MINUTE)

    assert limits.check("b", THREE_PER_MINUTE) is None


def test_checking_without_recording(limits) -> None:
    """Login needs this: check before verifying the password, record only on failure."""
    for _ in range(10):
        assert limits.blocked("k", THREE_PER_MINUTE) is None

    assert limits.check("k", THREE_PER_MINUTE) is None


def test_forgetting_a_key_clears_it(limits) -> None:
    for _ in range(3):
        limits.check("k", THREE_PER_MINUTE)

    limits.forget("k")

    assert limits.check("k", THREE_PER_MINUTE) is None


def test_stale_keys_are_pruned(limits, clock) -> None:
    """The key space is attacker-controlled -- one entry per address or username ever
    seen -- so without pruning this is a slow memory leak.

    The advance is derived from the longest window rather than written as a number: a
    hardcoded one silently stops testing anything the day a longer limit is added, which
    is exactly what the 24-hour global ceiling did to it.
    """
    longest = max(limit.window for limit in (*ALL_LIMITS, plan_daily_global()))
    limits.check("old", THREE_PER_MINUTE)
    clock.advance(longest + 1)
    limits.check("fresh", THREE_PER_MINUTE)

    assert limits.prune() == 1
    assert limits.check("fresh", THREE_PER_MINUTE) is None


# --- over HTTP ----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    import importlib

    from app import auth, main

    # These tests spend the *whole* login and registration allowance on purpose, which is
    # fifty-odd password hashes. At the production 600k iterations that alone doubled the
    # suite's runtime. The cost of PBKDF2 is what `test_auth.py` is for; here it is pure
    # overhead, so it is turned down rather than paid for over and over.
    # `app.auth` re-exports `store` as the singleton, which shadows the submodule of the
    # same name -- so `import app.auth.store as m` resolves to the *instance*, not the
    # module. importlib returns what is actually in sys.modules.
    store_module = importlib.import_module("app.auth.store")
    monkeypatch.setattr(store_module, "PBKDF2_ITERATIONS", 1_000)

    accounts = AuthStore(tmp_path / "rl-auth.db")
    monkeypatch.setattr(main, "auth_store", accounts)
    monkeypatch.setattr(auth, "store", accounts)
    return TestClient(app)


def account(index: int) -> dict:
    return {"username": f"traveller{index}", "password": "correct horse battery"}


def test_registration_is_capped_per_address(client) -> None:
    """Free and instant accounts make every per-account limit meaningless."""
    for index in range(REGISTER_PER_ADDRESS.count):
        assert client.post("/auth/register", json=account(index)).status_code == 201

    refused = client.post("/auth/register", json=account(99))

    assert refused.status_code == 429
    assert refused.headers.get("Retry-After")


def test_the_refusal_says_how_long_to_wait(client) -> None:
    for index in range(REGISTER_PER_ADDRESS.count):
        client.post("/auth/register", json=account(index))

    refused = client.post("/auth/register", json=account(99))

    assert int(refused.headers["Retry-After"]) > 0


def test_failed_sign_ins_are_capped_per_account(client) -> None:
    client.post("/auth/register", json=account(0))
    wrong = {**account(0), "password": "wrong horse battery"}

    for _ in range(LOGIN_FAILURES_PER_USER.count):
        assert client.post("/auth/login", json=wrong).status_code == 401

    assert client.post("/auth/login", json=wrong).status_code == 429


def test_a_successful_sign_in_clears_the_failure_count(client) -> None:
    """Someone who eventually remembers their password should not stay penalised."""
    client.post("/auth/register", json=account(0))
    wrong = {**account(0), "password": "wrong horse battery"}
    for _ in range(LOGIN_FAILURES_PER_USER.count - 1):
        client.post("/auth/login", json=wrong)

    assert client.post("/auth/login", json=account(0)).status_code == 200

    # The allowance is back, rather than one failure away from a lockout.
    for _ in range(LOGIN_FAILURES_PER_USER.count):
        assert client.post("/auth/login", json=wrong).status_code == 401


def test_one_account_cannot_be_locked_out_by_a_stranger(client) -> None:
    """Counting *attempts* rather than failures would trade a brute-force hole for a
    denial-of-service one: anyone could lock anyone out by guessing wrong on purpose."""
    client.post("/auth/register", json=account(0))
    wrong = {**account(0), "password": "wrong horse battery"}
    for _ in range(LOGIN_FAILURES_PER_USER.count):
        client.post("/auth/login", json=wrong)
    assert client.post("/auth/login", json=wrong).status_code == 429

    # The real owner is locked out too -- which is why the *window* is short and the
    # count is generous. This test pins the trade-off rather than pretending it is free.
    assert client.post("/auth/login", json=account(0)).status_code == 429


def test_password_spraying_is_capped_per_address(client) -> None:
    """One guess each against many accounts: no single account is hit twice, so the
    per-user counter never sees it."""
    limiter.reset()
    for index in range(LOGIN_PER_ADDRESS.count):
        client.post(
            "/auth/login", json={"username": f"victim{index}", "password": "guess guess guess"}
        )

    refused = client.post("/auth/login", json={"username": "victim999", "password": "guess guess"})

    assert refused.status_code == 429


def test_publishing_is_capped_per_account(client) -> None:
    registered = client.post("/auth/register", json=account(0)).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}
    body = {
        "itinerary": {
            "destination": "Chicago",
            "start_date": "2026-09-07",
            "end_date": "2026-09-07",
            "days": [],
        }
    }

    for _ in range(PUBLISH.count):
        assert client.post("/community/plans", json=body, headers=headers).status_code == 201

    assert client.post("/community/plans", json=body, headers=headers).status_code == 429


def test_reading_the_feed_is_never_rate_limited(client) -> None:
    """Reads are cheap and anonymous; limiting them would break browsing for everyone
    sharing an address without protecting anything worth protecting."""
    for _ in range(50):
        assert client.get("/community/plans").status_code == 200
