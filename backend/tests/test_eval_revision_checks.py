"""The revision checks must be able to fail.

A check that passes on a good run proves nothing on its own -- `kept_most_activities`
returning None against a real revision is equally consistent with it always returning
None. These pin the red case for each one, offline, so the eval keeps its teeth without
spending tokens to find out it lost them.
"""

import json

import pytest

from app.agent.results import PlanResult
from app.agent.schemas import Itinerary
from app.agent.validation import ValidationReport
from evals.checks import (
    after,
    feasible,
    kept_most_activities,
    now_schedules,
    trip_frame_unchanged,
)

BASE = {
    "destination": "Chicago",
    "start_date": "2026-09-01",
    "end_date": "2026-09-02",
    "travelers": 1,
    "currency": "USD",
    "budget": 400.0,
    "days": [
        {
            "date": "2026-09-01",
            "summary": "Day one",
            "activities": [
                {
                    "start_time": "09:00",
                    "end_time": "11:00",
                    "title": "Art Institute of Chicago",
                    "category": "sightseeing",
                    "estimated_cost": 12.0,
                },
                {
                    "start_time": "12:00",
                    "end_time": "13:00",
                    "title": "Portillo's lunch",
                    "category": "food",
                    "estimated_cost": 40.0,
                },
                {
                    "start_time": "15:00",
                    "end_time": "17:00",
                    "title": "Willis Tower",
                    "category": "sightseeing",
                    "estimated_cost": 10.0,
                },
                {
                    "start_time": "21:00",
                    "end_time": "22:00",
                    "title": "Check in at The Blackstone",
                    "category": "accommodation",
                    "estimated_cost": 90.0,
                },
            ],
        },
        {
            "date": "2026-09-02",
            "summary": "Day two",
            "activities": [
                {
                    "start_time": "10:00",
                    "end_time": "12:00",
                    "title": "Shedd Aquarium",
                    "category": "sightseeing",
                    "estimated_cost": 20.0,
                },
                {
                    "start_time": "12:30",
                    "end_time": "13:30",
                    "title": "Navy Pier lunch",
                    "category": "food",
                    "estimated_cost": 25.0,
                },
            ],
        },
    ],
    "notes": [],
}


def _result(plan: dict) -> PlanResult:
    return PlanResult(itinerary=Itinerary.model_validate(plan))


def _mutate(**changes) -> dict:
    plan = json.loads(json.dumps(BASE))
    plan.update(changes)
    return plan


@pytest.fixture
def before() -> PlanResult:
    return _result(BASE)


def test_a_swapped_lunch_passes_but_a_rewritten_trip_does_not(before) -> None:
    swapped = json.loads(json.dumps(BASE))
    swapped["days"][1]["activities"][1] = {
        "start_time": "12:30",
        "end_time": "13:30",
        "title": "Lou Malnati's Pizzeria",
        "category": "food",
        "estimated_cost": 18.0,
    }
    # 5 of 6 survive.
    assert kept_most_activities(0.6)(before, _result(swapped)) is None

    rewritten = json.loads(json.dumps(BASE))
    for day in rewritten["days"]:
        for index, activity in enumerate(day["activities"]):
            activity["title"] = f"Something else {index}"
    reason = kept_most_activities(0.6)(before, _result(rewritten))
    assert reason is not None and "survived" in reason


def test_an_edit_that_did_not_happen_is_caught(before) -> None:
    # The model returned the plan unchanged. Every other check still passes -- this is
    # the only one that notices nothing was done.
    assert now_schedules("deep dish", "pizza")(before, before) is not None

    edited = json.loads(json.dumps(BASE))
    edited["days"][1]["activities"][1]["title"] = "Lou Malnati's deep dish pizza"
    assert now_schedules("deep dish", "pizza")(before, _result(edited)) is None


def test_a_widened_budget_is_caught(before) -> None:
    """Otherwise `after(within_budget())` would pass for the wrong reason."""
    reason = trip_frame_unchanged()(before, _result(_mutate(budget=900.0)))
    assert reason is not None and "budget" in reason

    assert trip_frame_unchanged()(before, _result(_mutate(destination="Boston"))) is not None
    assert trip_frame_unchanged()(before, _result(BASE)) is None


def test_after_applies_an_ordinary_check_to_the_revised_plan_only(before) -> None:
    """The adapter must grade the edit, not the original -- the whole point."""
    broken = PlanResult(itinerary=Itinerary.model_validate(BASE), validation=None)

    # `feasible()` fails on a result with no validation report. Applied through
    # `after`, it must read the second argument.
    assert after(feasible())(broken, broken) is not None
    assert after(feasible())(broken, before) is not None  # `before` also has no report

    checked = PlanResult(
        itinerary=Itinerary.model_validate(BASE),
        validation=ValidationReport(violations=[]),
    )
    assert after(feasible())(broken, checked) is None


def test_a_missing_itinerary_is_reported_rather_than_crashing(before) -> None:
    empty = PlanResult(itinerary=None)

    assert kept_most_activities()(empty, before) is not None
    assert trip_frame_unchanged()(before, empty) is not None
    assert now_schedules("拉面")(before, empty) is not None
