"""Feedback is owner-scoped, bounded, and contains no user or model prose."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent.constraints import TripConstraints
from app.agent.results import PlanResult, ToolCallRecord
from app.agent.schemas import Itinerary
from app.agent.validation import ValidationReport, Violation
from app.auth.store import Credentials
from app.feedback import FeedbackRequest, diagnostic_snapshot
from app.main import app


def result() -> PlanResult:
    plan = Itinerary.model_validate(
        {
            "destination": "Private destination",
            "start_date": "2026-10-20",
            "end_date": "2026-10-20",
            "budget": 100,
            "days": [
                {
                    "date": "2026-10-20",
                    "summary": "Private summary",
                    "activities": [
                        {
                            "start_time": "09:00",
                            "end_time": "10:00",
                            "title": "Private venue",
                            "location": "Private address",
                            "category": "sightseeing",
                            "estimated_cost": 120,
                        }
                    ],
                }
            ],
        }
    )
    return PlanResult(
        itinerary=plan,
        constraints=TripConstraints(budget=100, currency="USD"),
        tool_calls=[
            ToolCallRecord(
                name="search_places",
                arguments={"query": "private request text"},
                ok=False,
                error="private upstream response",
            )
        ],
        validation=ValidationReport(
            violations=[Violation(code="over_budget", message="private diagnostic prose")]
        ),
        raw_reply="private model reply",
    )


async def account(username: str) -> tuple[str, dict[str, str]]:
    session = await main.auth_store.register(
        Credentials(username=username, password="correct horse battery")
    )
    return session.account.id, {"Authorization": f"Bearer {session.token}"}


def test_snapshot_is_allowlisted_and_text_free() -> None:
    encoded = json.dumps(diagnostic_snapshot(result()))
    for private in (
        "Private destination",
        "Private summary",
        "Private venue",
        "Private address",
        "private request text",
        "private upstream response",
        "private diagnostic prose",
        "private model reply",
    ):
        assert private not in encoded
    assert '"over_budget"' in encoded
    assert '"tool_failures": 1' in encoded


async def test_plan_and_activity_feedback_are_saved_and_owner_scoped() -> None:
    owner, headers = await account("traveller")
    _, other_headers = await account("someoneelse")
    run = result()
    await main.feedback_store.record(owner, run)
    client = TestClient(app)

    plan_feedback = {
        "run_id": run.run_id,
        "helpful": False,
        "category": "validator_miss",
    }
    assert client.post("/feedback", json=plan_feedback, headers=other_headers).status_code == 404
    assert client.post("/feedback", json=plan_feedback, headers=headers).status_code == 204
    activity_feedback = {
        **plan_feedback,
        "day_index": 0,
        "activity_index": 0,
        "category": "stale_data",
    }
    assert client.post("/feedback", json=activity_feedback, headers=headers).status_code == 204

    exported = await main.feedback_store.export()
    assert {(item["target"], item["category"]) for item in exported} == {
        ("plan", "validator_miss"),
        ("0:0", "stale_data"),
    }
    assert all(item["review_status"] == "unreviewed" for item in exported)


async def test_only_signed_in_plan_results_offer_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def planned(*args: object, **kwargs: object) -> PlanResult:
        return result()

    monkeypatch.setattr(main, "plan_trip", planned)
    owner, headers = await account("traveller")
    client = TestClient(app)

    signed_in = client.post("/plan", json={"message": "1 day in Los Angeles"}, headers=headers)
    anonymous = client.post("/plan", json={"message": "1 day in Los Angeles"})

    assert signed_in.status_code == 200 and signed_in.json()["feedback_available"] is True
    assert anonymous.status_code == 200 and anonymous.json()["feedback_available"] is False
    assert (
        await main.feedback_store.submit(
            owner,
            FeedbackRequest(run_id=signed_in.json()["run_id"], helpful=True),
        )
        is True
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"run_id": "a" * 32, "helpful": True, "day_index": 0},
        {"run_id": "a" * 32, "helpful": True, "activity_index": 0},
        {"run_id": "a" * 32, "helpful": True, "category": "invented"},
    ],
)
def test_invalid_feedback_is_rejected(payload: dict) -> None:
    assert TestClient(app).post("/feedback", json=payload).status_code == 401
    with pytest.raises(ValueError):
        FeedbackRequest.model_validate(payload)


async def test_invalid_activity_index_and_storage_failure_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, headers = await account("traveller")
    run = result()
    await main.feedback_store.record(owner, run)
    client = TestClient(app)
    payload = {
        "run_id": run.run_id,
        "helpful": True,
        "day_index": 9,
        "activity_index": 9,
    }
    assert client.post("/feedback", json=payload, headers=headers).status_code == 404

    async def unavailable(*args: object, **kwargs: object) -> bool:
        raise sqlite3.OperationalError("private database path")

    monkeypatch.setattr(main.feedback_store, "submit", unavailable)
    response = client.post(
        "/feedback",
        json={"run_id": run.run_id, "helpful": True},
        headers=headers,
    )
    assert response.status_code == 503
    assert "private" not in response.text


async def test_retention_prune_removes_snapshot_and_feedback() -> None:
    owner, _ = await account("traveller")
    run = result()
    await main.feedback_store.record(owner, run)
    assert await main.feedback_store.submit(owner, FeedbackRequest(run_id=run.run_id, helpful=True))
    connection = sqlite3.connect(main.feedback_store.path)
    try:
        connection.execute("UPDATE runs SET created = 0 WHERE id = ?", (run.run_id,))
        connection.commit()
    finally:
        connection.close()

    assert await main.feedback_store.prune() == 1
    assert await main.feedback_store.export() == []


def test_feedback_requires_authentication() -> None:
    response = TestClient(app).post("/feedback", json={"run_id": "a" * 32, "helpful": True})
    assert response.status_code == 401
