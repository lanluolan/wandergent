"""Itinerary model tests.

These guard the parts the LLM is most likely to get wrong: malformed times, backwards
ranges, invented categories, and cost totals it tried to add up itself.
"""

import pytest
from pydantic import ValidationError

from app.agent.schemas import Activity, DayPlan, Itinerary


def activity(**overrides) -> dict:
    base = {
        "start_time": "09:00",
        "end_time": "11:00",
        "title": "Art Institute of Chicago",
        "category": "sightseeing",
        "estimated_cost": 50.0,
    }
    return base | overrides


def test_costs_are_derived_not_trusted() -> None:
    """A model claiming a bogus total must not be able to fake fitting the budget."""
    day = DayPlan.model_validate(
        {
            "date": "2026-08-06",
            "summary": "Day one",
            "estimated_cost": 1.0,  # the model's own arithmetic, ignored
            "activities": [activity(estimated_cost=100.0), activity(estimated_cost=80.5)],
        }
    )
    assert day.estimated_cost == 180.5

    trip = Itinerary.model_validate(
        {
            "destination": "Chicago",
            "start_date": "2026-08-06",
            "end_date": "2026-08-06",
            "days": [day.model_dump()],
        }
    )
    assert trip.total_estimated_cost == 180.5


@pytest.mark.parametrize("bad_time", ["9:00", "24:00", "09:60", "morning", "09-00"])
def test_bad_times_are_rejected(bad_time: str) -> None:
    with pytest.raises(ValidationError, match="24-hour"):
        Activity.model_validate(activity(start_time=bad_time))


def test_end_time_must_follow_start_time() -> None:
    with pytest.raises(ValidationError, match="must be after"):
        Activity.model_validate(activity(start_time="14:00", end_time="13:00"))


def test_unknown_category_falls_back_instead_of_failing() -> None:
    """One invented label should not throw away an otherwise usable plan."""
    assert Activity.model_validate(activity(category="nonsense-category")).category == "other"


def test_negative_cost_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Activity.model_validate(activity(estimated_cost=-10))


def test_backwards_trip_dates_are_rejected() -> None:
    with pytest.raises(ValidationError, match="end_date"):
        Itinerary.model_validate(
            {"destination": "Chicago", "start_date": "2026-08-09", "end_date": "2026-08-06"}
        )


def test_minimal_itinerary_has_sane_defaults() -> None:
    trip = Itinerary.model_validate(
        {"destination": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-08"}
    )
    assert trip.travelers == 1
    assert trip.currency == "CNY"
    assert trip.budget is None
    assert trip.total_estimated_cost == 0.0
