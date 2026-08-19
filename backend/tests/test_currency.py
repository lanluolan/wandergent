"""The traveller's settlement currency reaches the model, and only when it is set."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agent.orchestrator import plan_trip
from app.agent.results import PlanResult
from app.main import app
from tests.fakes import ITINERARY_JSON, FakeLLM, completion

TODAY = date(2026, 8, 5)


def system_prompt_of(llm: FakeLLM) -> str:
    return llm.requests[0]["messages"][0]["content"]


async def test_the_currency_reaches_the_system_prompt() -> None:
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    await plan_trip(
        "3 days in Chicago", client=llm, model="test-model", today=TODAY, currency="USD"
    )

    prompt = system_prompt_of(llm)
    assert "USD" in prompt
    assert "currency field to USD" in prompt


async def test_no_currency_leaves_the_choice_to_the_model() -> None:
    """The old behaviour, and what any caller that does not set it gets. The rule is
    also prompt tokens on every call, so it must not ship when unused."""
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    await plan_trip("3 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert "settles up in" not in system_prompt_of(llm)


@pytest.mark.parametrize("sent,expected", [("usd", "USD"), ("Jpy", "JPY")])
def test_the_endpoint_normalises_the_code(monkeypatch, sent: str, expected: str) -> None:
    seen: dict = {}

    async def _plan(message, **kwargs):
        seen.update(kwargs)
        return PlanResult(itinerary=None, warnings=[])

    monkeypatch.setattr("app.main.plan_trip", _plan)

    with TestClient(app) as client:
        client.post("/plan", json={"message": "3 days in Chicago", "currency": sent})

    assert seen["currency"] == expected


def test_a_bad_currency_code_is_rejected_at_the_edge() -> None:
    """Three letters or nothing. The model should never see 'dollars' or '$'."""
    with TestClient(app) as client:
        assert client.post("/plan", json={"message": "x", "currency": "dollars"}).status_code == 422
        assert client.post("/plan", json={"message": "x", "currency": "$"}).status_code == 422
