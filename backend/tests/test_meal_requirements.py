"""Explicit meal edits remain request-owned; matching text is not a verified menu."""

import pytest

from app.agent import orchestrator
from app.agent.revision import MealRequirement, resolve_meal_requirements
from app.agent.schemas import Itinerary
from app.agent.validation import validate_itinerary
from tests.fakes import ITINERARY_JSON, FakeLLM, completion

REQUEST = "change day 2 lunch to a po-boy place, leave everything else alone"


def meal_plan():
    value = Itinerary.model_validate_json(ITINERARY_JSON)
    value.days[1].activities[0].category = "food"
    value.days[1].activities[0].title = "Lunch at Observed Restaurant"
    value.days[1].activities[0].start_time = "12:00"
    value.days[1].activities[0].end_time = "13:00"
    value.days[1].activities[0].highlights = ["Salad"]
    return value


def test_literal_specialty_parser_has_day_and_meal_scope_not_a_dish_dictionary():
    assert resolve_meal_requirements(REQUEST) == [MealRequirement(1, "lunch", "po-boy")]
    assert resolve_meal_requirements("Please change day 1 dinner to a ramen restaurant.") == [
        MealRequirement(0, "dinner", "ramen")
    ]
    for ambiguous in (
        "Do not change day 2 lunch to a po-boy place",
        "change lunch",
        "change day 2 lunch to a cheaper place",
        "New trip: lunch in Boston",
    ):
        assert resolve_meal_requirements(ambiguous) == []


@pytest.mark.parametrize("spelling", ["po-boy", "po' boy", "poboy", "PO BOY"])
def test_requested_specialty_in_meal_title_satisfies_the_edit(spelling):
    value = meal_plan()
    value.days[1].activities[0].title = f"Lunch at a {spelling} restaurant (unverified menu)"
    assert validate_itinerary(value, meals=resolve_meal_requirements(REQUEST)).ok


def test_legacy_highlight_cannot_satisfy_the_visible_meal_edit():
    value = meal_plan()
    value.days[1].activities[0].highlights = ["Roast beef po-boy"]
    assert not validate_itinerary(value, meals=resolve_meal_requirements(REQUEST)).ok


@pytest.mark.parametrize("wrong_scope", ["day", "meal", "category", "notes"])
def test_another_day_meal_or_commentary_does_not_satisfy_the_requested_edit(wrong_scope):
    value = meal_plan()
    activity = value.days[1].activities[0]
    if wrong_scope == "day":
        value.days[0].activities[0].highlights = ["po-boy"]
    elif wrong_scope == "meal":
        activity.title = "Breakfast at a po-boy restaurant"
    elif wrong_scope == "category":
        activity.category = "sightseeing"
        activity.highlights = ["po-boy"]
    else:
        activity.notes = "Choose a po-boy restaurant later"
    assert any(
        v.code == "constraint_mismatch"
        for v in validate_itinerary(value, meals=resolve_meal_requirements(REQUEST)).blocking
    )


def test_nonexistent_day_is_not_a_silent_success():
    value = meal_plan()
    assert not validate_itinerary(value, meals=[MealRequirement(9, "lunch", "ramen")]).ok


@pytest.mark.parametrize("corrected", [True, False])
async def test_graph_repairs_missing_specialty_without_rewriting_the_locked_day(corrected):
    original = meal_plan()
    fixed = original.model_copy(deep=True)
    fixed.days[1].activities[0].title = "Lunch at a po-boy restaurant (unverified menu)"
    replies = [original, fixed] if corrected else [original] * 4
    llm = FakeLLM([completion(content=value.model_dump_json()) for value in replies])
    result = await orchestrator.plan_trip(
        REQUEST, previous=original, client=llm, model="test-model"
    )
    assert result.validation.ok is corrected
    expected = original.days[0].model_copy(deep=True)
    for activity in expected.activities:
        activity.highlights = []
        activity.place_summary = None
    assert result.itinerary.days[0] == expected
    assert len(llm.requests) == (2 if corrected else 4)
    assert "po-boy" in llm.requests[1]["messages"][-1]["content"]
