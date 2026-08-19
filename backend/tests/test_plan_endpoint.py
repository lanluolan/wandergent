"""`POST /plan` tests: request validation, response shape, and error mapping.

`plan_trip` is patched out here -- the loop itself is covered in test_orchestrator --
so these only assert that the HTTP layer wires it up and translates failures into
status codes the Android client can act on.
"""

import pytest
from fastapi.testclient import TestClient

from app.agent.orchestrator import (
    PlanningConfigError,
    PlanningError,
    PlanningTimeout,
    PlanResult,
    ToolCallRecord,
)
from app.agent.schemas import Itinerary
from app.config import settings
from app.main import app

client = TestClient(app)


def sample_result() -> PlanResult:
    itinerary = Itinerary.model_validate(
        {
            "destination": "Chicago",
            "start_date": "2026-08-06",
            "end_date": "2026-08-06",
            "days": [
                {
                    "date": "2026-08-06",
                    "summary": "Day one",
                    "activities": [
                        {
                            "start_time": "09:00",
                            "end_time": "11:00",
                            "title": "Art Institute of Chicago",
                            "estimated_cost": 50.0,
                        }
                    ],
                }
            ],
        }
    )
    return PlanResult(
        itinerary=itinerary,
        tool_calls=[ToolCallRecord(name="get_weather_forecast", arguments={}, ok=True)],
    )


def patch_plan_trip(monkeypatch, result=None, error: Exception | None = None) -> list[str]:
    """Swap the orchestrator for a stub and record the requests it received."""
    seen: list[str] = []

    async def fake(message: str, **kwargs) -> PlanResult:
        seen.append(message)
        if error is not None:
            raise error
        return result

    monkeypatch.setattr("app.main.plan_trip", fake)
    return seen


def test_plan_returns_itinerary_with_derived_totals(monkeypatch) -> None:
    seen = patch_plan_trip(monkeypatch, result=sample_result())

    resp = client.post("/plan", json={"message": "1 day in Chicago"})

    assert resp.status_code == 200
    assert seen == ["1 day in Chicago"]
    body = resp.json()
    assert body["itinerary"]["destination"] == "Chicago"
    assert body["itinerary"]["total_estimated_cost"] == 50.0
    assert body["tool_calls"][0]["name"] == "get_weather_forecast"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (PlanningConfigError("OPENAI_API_KEY is not set"), 500),
        (PlanningTimeout("the language model timed out"), 504),
        (PlanningError("the language model is unavailable"), 502),
    ],
)
def test_upstream_failures_map_to_status_codes(monkeypatch, error: Exception, status: int) -> None:
    patch_plan_trip(monkeypatch, error=error)

    resp = client.post("/plan", json={"message": "1 day in Chicago"})

    assert resp.status_code == status
    assert resp.json()["detail"] == str(error)


@pytest.mark.parametrize("payload", [{}, {"message": ""}, {"message": "x" * 2001}])
def test_invalid_requests_are_rejected(payload: dict) -> None:
    assert client.post("/plan", json=payload).status_code == 422


# --- identity comes from the token, not the body -----------------------------------
#
# The endpoint used to take `user_id` in the request body, which meant a caller could
# aim a memory write at somebody else's account. The field is gone; these pin what
# replaced it.


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    from app import auth, main
    from app.auth.store import AuthStore

    store = AuthStore(tmp_path / "plan-auth.db")
    monkeypatch.setattr(main, "auth_store", store)
    monkeypatch.setattr(auth, "store", store)
    return store


def captured_user_id(monkeypatch) -> list[str]:
    """Record whatever the endpoint decides the caller's id is."""
    seen: list[str] = []

    async def fake(message: str, **kwargs) -> PlanResult:
        seen.append(kwargs.get("user_id", "<absent>"))
        return sample_result()

    monkeypatch.setattr("app.main.plan_trip", fake)
    return seen


def test_a_token_decides_whose_preferences_are_used(accounts, monkeypatch) -> None:
    seen = captured_user_id(monkeypatch)
    registered = client.post(
        "/auth/register", json={"username": "yuxia", "password": "correct horse battery"}
    ).json()

    client.post(
        "/plan",
        json={"message": "3 days in Chicago"},
        headers={"Authorization": f"Bearer {registered['token']}"},
    )

    assert seen == [registered["account"]["id"]]


def test_planning_without_a_token_is_allowed_and_anonymous(accounts, monkeypatch) -> None:
    """Signing in was always optional here. An anonymous run recalls nothing and writes
    nothing back, which is what the empty id has always meant downstream."""
    seen = captured_user_id(monkeypatch)

    answered = client.post("/plan", json={"message": "3 days in Chicago"})

    assert answered.status_code == 200
    assert seen == [""]


def test_a_user_id_in_the_body_cannot_borrow_an_identity(accounts, monkeypatch) -> None:
    """The dangerous case, and the reason the field had to be deleted rather than merely
    checked: an old or hostile client sending one must not be treated as that person."""
    seen = captured_user_id(monkeypatch)

    client.post("/plan", json={"message": "3 days in Chicago", "user_id": "somebody-else"})

    assert seen == [""]


def test_a_lapsed_token_is_anonymous_not_an_error(accounts, monkeypatch) -> None:
    """Planning is the one thing that should keep working when a session expires -- a
    traveller mid-trip should get their plan, just without their saved preferences."""
    seen = captured_user_id(monkeypatch)

    answered = client.post(
        "/plan",
        json={"message": "3 days in Chicago"},
        headers={"Authorization": "Bearer long-since-expired"},
    )

    assert answered.status_code == 200
    assert seen == [""]


# --- the expensive endpoint has a ceiling ------------------------------------------


def test_planning_is_rate_limited_per_account(accounts, monkeypatch) -> None:
    """`/plan` is the only endpoint here that spends money on every call -- several LLM
    round trips plus Places and Routes quota. It was the last one left uncapped."""
    from app.ratelimit import PLAN

    captured_user_id(monkeypatch)
    registered = client.post(
        "/auth/register", json={"username": "yuxia", "password": "correct horse battery"}
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}

    for _ in range(PLAN.count):
        assert client.post("/plan", json={"message": "x"}, headers=headers).status_code == 200

    refused = client.post("/plan", json={"message": "x"}, headers=headers)

    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0


def test_the_stream_shares_the_same_allowance(accounts, monkeypatch) -> None:
    """Two endpoints, one budget. Alternating between them must not buy twice the runs."""
    from app.ratelimit import PLAN

    captured_user_id(monkeypatch)
    registered = client.post(
        "/auth/register", json={"username": "yuxia", "password": "correct horse battery"}
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}

    for _ in range(PLAN.count):
        client.post("/plan", json={"message": "x"}, headers=headers)

    assert client.post("/plan/stream", json={"message": "x"}, headers=headers).status_code == 429


def test_two_accounts_do_not_share_a_budget(accounts, monkeypatch) -> None:
    """Keyed on the account, so a shared address does not make one traveller's runs count
    against another's."""
    from app.ratelimit import PLAN

    captured_user_id(monkeypatch)
    first = client.post(
        "/auth/register", json={"username": "yuxia", "password": "correct horse battery"}
    ).json()
    second = client.post(
        "/auth/register", json={"username": "meilin", "password": "another good one"}
    ).json()

    for _ in range(PLAN.count):
        client.post(
            "/plan", json={"message": "x"}, headers={"Authorization": f"Bearer {first['token']}"}
        )

    answered = client.post(
        "/plan", json={"message": "x"}, headers={"Authorization": f"Bearer {second['token']}"}
    )

    assert answered.status_code == 200


# --- the ceiling that actually bounds the bill --------------------------------------


def test_the_service_has_a_daily_ceiling_across_everyone(accounts, monkeypatch) -> None:
    """Per-account limits do not bound the bill: accounts are free and instant, so twenty
    runs an hour each times an unbounded number of accounts is unbounded."""
    monkeypatch.setattr(settings, "max_plans_per_day", 3)
    captured_user_id(monkeypatch)

    for index in range(3):
        registered = client.post(
            "/auth/register",
            json={"username": f"traveller{index}", "password": "correct horse battery"},
        ).json()
        answered = client.post(
            "/plan",
            json={"message": "x"},
            headers={"Authorization": f"Bearer {registered['token']}"},
        )
        assert answered.status_code == 200

    fresh = client.post(
        "/auth/register", json={"username": "latecomer", "password": "correct horse battery"}
    ).json()
    refused = client.post(
        "/plan", json={"message": "x"}, headers={"Authorization": f"Bearer {fresh['token']}"}
    )

    assert refused.status_code == 503
    assert int(refused.headers["Retry-After"]) > 0


def test_the_service_ceiling_is_not_the_callers_fault(accounts, monkeypatch) -> None:
    """503, not 429. "Too many requests" blames someone who did nothing wrong and sends
    them tapping retry over something they cannot influence."""
    monkeypatch.setattr(settings, "max_plans_per_day", 1)
    captured_user_id(monkeypatch)
    registered = client.post(
        "/auth/register", json={"username": "yuxia", "password": "correct horse battery"}
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}
    client.post("/plan", json={"message": "x"}, headers=headers)

    refused = client.post("/plan", json={"message": "x"}, headers=headers)

    assert refused.status_code == 503
    assert "Nothing is wrong with your request" in refused.json()["detail"]


def test_anonymous_runs_count_against_the_ceiling_too(accounts, monkeypatch) -> None:
    """They cost exactly as much. Excluding them would leave the whole budget reachable
    without an account."""
    monkeypatch.setattr(settings, "max_plans_per_day", 2)
    captured_user_id(monkeypatch)

    assert client.post("/plan", json={"message": "x"}).status_code == 200
    assert client.post("/plan", json={"message": "x"}).status_code == 200

    assert client.post("/plan", json={"message": "x"}).status_code == 503


def test_a_run_refused_by_the_personal_limit_does_not_spend_the_global_budget(
    accounts, monkeypatch
) -> None:
    """The reason both ceilings are checked before either is recorded. Otherwise a caller
    bouncing off their own hourly limit quietly eats the day's budget without a single
    plan being produced."""
    from app.ratelimit import PLAN

    monkeypatch.setattr(settings, "max_plans_per_day", PLAN.count + 5)
    captured_user_id(monkeypatch)
    first = client.post(
        "/auth/register", json={"username": "yuxia", "password": "correct horse battery"}
    ).json()
    headers = {"Authorization": f"Bearer {first['token']}"}

    for _ in range(PLAN.count):
        client.post("/plan", json={"message": "x"}, headers=headers)
    # Twenty more that all bounce off the personal limit.
    for _ in range(20):
        assert client.post("/plan", json={"message": "x"}, headers=headers).status_code == 429

    second = client.post(
        "/auth/register", json={"username": "meilin", "password": "another good one"}
    ).json()
    answered = client.post(
        "/plan", json={"message": "x"}, headers={"Authorization": f"Bearer {second['token']}"}
    )

    assert answered.status_code == 200
