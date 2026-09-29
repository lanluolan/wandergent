"""Counterexamples for request-owned limits and both repair paths."""

import json
from datetime import date

import pytest

from app.agent.constraints import TripConstraints, resolve_constraints
from app.agent.orchestrator import plan_trip
from app.agent.schemas import Itinerary
from app.agent.validation import transfer_candidates, validate_itinerary
from tests.fakes import FakeLLM, completion
from tests.test_constraint_repair import itinerary_json


@pytest.mark.parametrize("budget", [None, 10000])
def test_model_cannot_drop_or_raise_budget(budget: float | None) -> None:
    payload = json.loads(itinerary_json(500, 1000))
    payload["budget"] = budget
    plan = Itinerary.model_validate(payload)
    report = validate_itinerary(plan, constraints=TripConstraints(budget=100, currency="USD"))
    assert {v.code for v in report.blocking} >= {"over_budget", "constraint_mismatch"}


def test_explicit_fields_are_independent_of_the_plan() -> None:
    actual = resolve_constraints(
        "2 days in Los Angeles from 2026-10-20 to 2026-10-21, budget $900, 3 travelers, no driving"
    )
    assert actual == TripConstraints(
        budget=900,
        currency="USD",
        days=2,
        travelers=3,
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 21),
        allowed_modes=["WALK", "TRANSIT"],
    )
    revised = resolve_constraints("Change day 2 lunch", previous=actual)
    assert revised == actual
    assert resolve_constraints("budget 800 USD", previous=actual).budget == 800
    fresh = resolve_constraints("New trip: 1 day in Boston", previous=actual)
    assert fresh.budget is None and fresh.allowed_modes is None and fresh.days == 1


def test_structured_user_confirmation_overrides_text_and_can_clear() -> None:
    previous = TripConstraints(budget=900, travelers=2)
    updated = resolve_constraints(
        "budget 800 USD", previous=previous, confirmed=TripConstraints(budget=700, travelers=None)
    )
    assert updated.budget == 700 and updated.travelers is None


def test_duration_revision_moves_end_date_and_structured_conflicts_are_rejected() -> None:
    previous = TripConstraints(start_date=date(2026, 10, 20), end_date=date(2026, 10, 21), days=2)
    revised = resolve_constraints("Make it 3 days", previous=previous)
    assert revised.end_date == date(2026, 10, 22) and revised.days == 3
    with pytest.raises(ValueError, match="days must match"):
        TripConstraints(start_date=date(2026, 10, 20), end_date=date(2026, 10, 21), days=3)


async def test_emit_plan_is_in_repair_context_exactly_once() -> None:
    invalid = itinerary_json(5000)
    llm = FakeLLM(
        [
            completion(content="I will compose a plan."),
            completion(content=invalid),
            completion(content=itinerary_json(200)),
        ]
    )
    result = await plan_trip("1 day in Los Angeles, budget 1000 USD", client=llm)
    messages = llm.requests[-1]["messages"]
    assert sum(m.get("content") == invalid for m in messages) == 1
    assert messages[-2]["role"] == "assistant" and messages[-2]["content"] == invalid
    assert result.validation.ok
    assert result.constraints.budget == 1000


async def test_emit_format_retry_does_not_duplicate_assistant_turn() -> None:
    llm = FakeLLM(
        [
            completion(content="Ready."),
            completion(content="{bad"),
            completion(content=itinerary_json(200)),
        ]
    )
    await plan_trip("1 day in Los Angeles", client=llm)
    messages = llm.requests[-1]["messages"]
    assert sum(m.get("content") == "{bad" for m in messages) == 1


async def test_repair_cannot_authorize_a_larger_budget() -> None:
    llm = FakeLLM(
        [
            completion(content=itinerary_json(500, 100)),
            completion(content=itinerary_json(500, 1000)),
            completion(content=itinerary_json(500, 1000)),
            completion(content=itinerary_json(500, 1000)),
        ]
    )
    result = await plan_trip("1 day in Los Angeles, budget 100 USD", client=llm)
    assert result.constraints.budget == 100
    assert {v.code for v in result.validation.blocking} >= {"over_budget", "constraint_mismatch"}


def test_explicit_transport_is_not_an_exemption() -> None:
    plan = Itinerary.model_validate_json(itinerary_json(10))
    first = plan.days[0].activities[0].model_copy(update={"location": "Getty Center"})
    leg = first.model_copy(
        update={
            "title": "Take a taxi",
            "category": "transport",
            "start_time": "11:00",
            "end_time": "11:05",
            "travel_mode": "DRIVE",
        }
    )
    second = first.model_copy(
        update={"location": "Santa Monica Pier", "start_time": "11:05", "end_time": "12:00"}
    )
    plan.days[0].activities = [first, leg, second]
    report = validate_itinerary(plan, constraints=TripConstraints(allowed_modes=["WALK"]))
    assert any(v.code == "disallowed_transport" for v in report.blocking)
    candidates = transfer_candidates(plan, report)
    hop = next(v for v in candidates.violations if v.code == "transfer_unverified")
    assert hop.travel_mode == "DRIVE" and hop.gap_minutes == 5


def test_date_party_and_currency_mismatch_are_all_caught() -> None:
    plan = Itinerary.model_validate_json(itinerary_json(10))
    report = validate_itinerary(
        plan,
        constraints=TripConstraints(
            start_date=date(2026, 10, 20),
            end_date=date(2026, 10, 21),
            travelers=3,
            days=2,
            currency="EUR",
        ),
    )
    assert len([v for v in report.blocking if v.code == "constraint_mismatch"]) == 5


async def test_repair_rechecks_time_conflicts() -> None:
    repair = json.loads(itinerary_json(20, 100))
    repair["days"][0]["activities"] *= 2
    llm = FakeLLM(
        [
            completion(content=itinerary_json(500, 100)),
            completion(content=json.dumps(repair)),
            completion(content=json.dumps(repair)),
            completion(content=json.dumps(repair)),
        ]
    )
    result = await plan_trip("1 day in Los Angeles, budget 100 USD", client=llm)
    assert any(v.code == "time_conflict" for v in result.validation.blocking)
