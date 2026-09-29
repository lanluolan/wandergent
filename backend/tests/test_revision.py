"""Revising an itinerary instead of replacing it.

The point of the feature is not that the model can edit text -- it is that an edit goes
through the same constraint machinery as a fresh plan. These tests pin both halves: the
prompt actually carries the plan being changed, and the revised result is still
validated and repaired.
"""

import json
from datetime import date

import pytest

from app.agent.orchestrator import REVISION_RULE, plan_trip
from app.agent.schemas import Itinerary
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion

TODAY = date(2026, 8, 5)

CURRENT = Itinerary.model_validate_json(ITINERARY_JSON)


@pytest.fixture(autouse=True)
def stub_weather(monkeypatch):
    async def fake(city: str, start_date: str, end_date: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=True, city=city, resolved_name=city, days=[])

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)


def _system(llm: FakeLLM) -> str:
    return llm.requests[0]["messages"][0]["content"]


def _user(llm: FakeLLM) -> str:
    return llm.requests[0]["messages"][1]["content"]


async def test_the_plan_being_changed_is_in_the_prompt() -> None:
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    await plan_trip(
        "change day 2 lunch to ramen",
        previous=CURRENT,
        client=llm,
        model="test-model",
        today=TODAY,
    )

    user = _user(llm)
    assert "change day 2 lunch to ramen" in user
    # Only day 2 is editable. Day 1 stays at its list position as a compact placeholder;
    # the server restores its exact content after every model turn.
    assert "Shedd Aquarium" in user
    assert "Art Institute of Chicago" not in user
    assert "LOCKED: preserved server-side" in user
    assert "day(s) 1 are locked" in _system(llm)
    assert REVISION_RULE in _system(llm)


async def test_a_plain_request_is_untouched_by_the_revision_path() -> None:
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    # No previous plan means no revision rule and no wrapper -- byte-identical to what
    # every caller got before this existed.
    assert _user(llm) == "2 days in Chicago"
    assert REVISION_RULE not in _system(llm)


async def test_unrequested_day_and_trip_frame_are_restored_server_side() -> None:
    changed = json.loads(ITINERARY_JSON)
    changed["destination"] = "Boston"
    changed["budget"] = 9000
    changed["days"][0]["activities"][0]["title"] = "Unauthorized rewrite"
    changed["days"][1]["activities"][0]["title"] = "Requested ramen lunch"
    llm = FakeLLM([completion(content=json.dumps(changed))])

    result = await plan_trip(
        "Change lunch on day 2 to ramen. Keep day 1 exactly as it is.",
        previous=CURRENT,
        client=llm,
        model="test-model",
        today=TODAY,
    )

    assert result.itinerary is not None
    assert result.itinerary.destination == CURRENT.destination
    assert result.itinerary.budget == CURRENT.budget
    assert result.itinerary.days[0] == CURRENT.days[0]
    assert result.itinerary.days[1].activities[0].title == "Requested ramen lunch"


async def test_repair_cannot_drift_a_locked_day() -> None:
    over_budget = json.loads(ITINERARY_JSON)
    over_budget["days"][0]["activities"][1]["start_time"] = "10:00"
    over_budget["days"][1]["activities"][0]["title"] = "First unauthorized rewrite"
    fixed = json.loads(json.dumps(over_budget))
    fixed["days"][0]["activities"][1]["start_time"] = "12:00"
    fixed["days"][1]["activities"][0]["title"] = "Second unauthorized rewrite"
    llm = FakeLLM(
        [
            completion(content=json.dumps(over_budget)),
            completion(content=json.dumps(fixed)),
        ]
    )

    result = await plan_trip(
        "Reduce costs on day 1. Leave day 2 unchanged.",
        previous=CURRENT,
        client=llm,
        model="test-model",
        today=TODAY,
    )

    assert result.itinerary is not None
    assert result.itinerary.days[1] == CURRENT.days[1]
    assert len(llm.requests) == 2
    assert result.validation is not None and result.validation.ok


async def test_the_totals_the_client_sent_back_are_recomputed_not_trusted() -> None:
    """A revision round-trips the plan through the client, so totals arrive as input.

    They are computed fields, so they are ignored on the way in and derived again on
    the way out -- a client that tampered with `total_estimated_cost` cannot make the
    budget check pass by sending a smaller number.
    """
    tampered = json.loads(CURRENT.model_dump_json())
    tampered["total_estimated_cost"] = 1.0
    tampered["days"][0]["estimated_cost"] = 1.0

    reparsed = Itinerary.model_validate(tampered)

    assert reparsed.total_estimated_cost == CURRENT.total_estimated_cost
    assert reparsed.days[0].estimated_cost == CURRENT.days[0].estimated_cost


async def test_a_revision_is_validated_and_repaired_like_any_other_plan() -> None:
    """The whole reason to do this server-side rather than in a chat window."""
    over_budget = json.loads(ITINERARY_JSON)
    over_budget["days"][0]["activities"][1]["start_time"] = "10:00"
    fixed = json.loads(json.dumps(over_budget))
    fixed["days"][0]["activities"][1]["start_time"] = "12:00"

    llm = FakeLLM(
        [
            completion(content=json.dumps(over_budget, ensure_ascii=False)),
            completion(content=json.dumps(fixed, ensure_ascii=False)),
        ]
    )

    result = await plan_trip(
        "add a Michelin dinner",
        previous=CURRENT,
        client=llm,
        model="test-model",
        today=TODAY,
    )

    # Two calls: the revision, then the constraint repair it triggered. An edit that
    # introduced an overlap was caught and sent back, not shipped.
    assert len(llm.requests) == 2
    assert result.validation is not None and result.validation.ok
    assert result.itinerary is not None
    assert result.itinerary.budget == CURRENT.budget
