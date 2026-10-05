"""Removal intent is scoped, server-checked, and cannot be satisfied by hiding a visit."""

from dataclasses import asdict

import pytest

from app.agent.orchestrator import plan_trip
from app.agent.revision import (
    VenueRemoval,
    prune_removed_recommendations,
    resolve_removals,
)
from app.agent.schemas import Itinerary
from app.agent.validation import validate_itinerary
from evals.checks import avoids
from tests.fakes import ITINERARY_JSON, FakeLLM, completion


def base():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    for day in plan.days:
        day.activities[0].highlights = ["Coffee", "Walk to Grand Central Market"]
    return plan


@pytest.mark.parametrize("verb", ["Remove", "Delete", "Drop"])
def test_explicit_day_removal_is_not_widened_by_locked_day_clause(verb):
    found = resolve_removals(f"{verb} Grand Central Market from day 1. Leave day 2 unchanged.")
    assert [asdict(r) for r in found] == [{"venue": "Grand Central Market", "day_index": 0}]
    assert resolve_removals("Do not remove Grand Central Market from day 1.") == []
    assert resolve_removals("Add Grand Central Market on day 1.") == []


def test_global_removal_and_case_whitespace_normalization():
    assert resolve_removals('Please remove "Grand Central Market".') == [
        VenueRemoval("Grand Central Market")
    ]
    plan = base()
    plan.days[0].activities[0].highlights = ["walk to GRAND   CENTRAL market"]
    assert not validate_itinerary(plan, removals=[VenueRemoval("Grand Central Market", 0)]).ok


@pytest.mark.parametrize("field", ["title", "location", "highlights"])
def test_positive_references_in_any_schedule_field_block(field):
    plan = base()
    activity = plan.days[0].activities[0]
    activity.highlights = []
    setattr(
        activity,
        field,
        ["Try Grand Central Market"] if field == "highlights" else "Grand Central Market",
    )
    report = validate_itinerary(plan, removals=[VenueRemoval("Grand Central Market", 0)])
    assert any(
        v.code == "constraint_mismatch" and v.day == plan.days[0].date for v in report.blocking
    )


def test_pruning_cannot_rename_a_scheduled_visit_or_modify_locked_days():
    plan = base()
    plan.days[0].activities[0].location = "Grand Central Market"
    original = plan.model_dump_json()
    pruned = prune_removed_recommendations(plan, [VenueRemoval("Grand Central Market", 0)])
    assert pruned.days[0].activities[0].highlights == ["Coffee"]
    assert pruned.days[1] == plan.days[1]
    assert plan.model_dump_json() == original
    assert pruned.days[0].activities[0].location == "Grand Central Market"
    assert not validate_itinerary(pruned, removals=[VenueRemoval("Grand Central Market", 0)]).ok


def test_notes_are_commentary_not_a_scheduled_recommendation_and_names_have_boundaries():
    plan = base()
    plan.days[0].activities[0].highlights = ["Grand Central Marketplace is different"]
    plan.days[0].activities[0].notes = "Grand Central Market was removed"
    assert validate_itinerary(plan, removals=[VenueRemoval("Grand Central Market", 0)]).ok


def test_global_removal_cannot_override_explicit_lock_and_missing_day_cannot_pass():
    plan = base()
    pruned = prune_removed_recommendations(
        plan, [VenueRemoval("Grand Central Market")], locked_days=frozenset({1})
    )
    assert pruned.days[1] == plan.days[1]
    assert not validate_itinerary(pruned, removals=[VenueRemoval("Grand Central Market")]).ok
    for index in (-1, 2):
        assert not validate_itinerary(
            plan, removals=[VenueRemoval("Grand Central Market", index)]
        ).ok


def test_eval_scope_matches_request_without_weakening_global_or_day_one_checks():
    from app.agent.results import PlanResult

    plan = base()
    plan.days[0].activities[0].highlights = ["Coffee"]
    result = PlanResult(itinerary=plan)
    assert avoids("Grand Central Market", day_indexes=(0,))(result) is None
    assert avoids("Grand Central Market")(result) is not None
    assert avoids("Grand Central Market", day_indexes=(2,))(result) is not None
    assert avoids("Grand Central Market", day_indexes=())(result) is not None
    plan.days[0].activities[0].highlights.append("Walk to Grand Central Market")
    assert avoids("Grand Central Market", day_indexes=(0,))(result) is not None


async def test_highlight_removal_is_deterministic_without_rewriting_other_day():
    previous = base()
    llm = FakeLLM([completion(content=previous.model_dump_json())])
    result = await plan_trip(
        "Remove Grand Central Market from day 1. Leave day 2 unchanged.",
        previous=previous,
        client=llm,
        model="test-model",
    )
    assert result.validation.ok
    assert result.itinerary.days[0].activities[0].highlights == []
    expected = previous.days[1].model_copy(deep=True)
    for activity in expected.activities:
        activity.highlights = []
        activity.place_summary = None
    assert result.itinerary.days[1] == expected
    assert len(llm.requests) == 1


async def test_stubborn_actual_visit_is_rejected_after_bounded_repair():
    previous = base()
    previous.days[0].activities[0].location = "Grand Central Market"
    llm = FakeLLM([completion(content=previous.model_dump_json()) for _ in range(4)])
    result = await plan_trip(
        "Remove Grand Central Market from day 1. Leave day 2 unchanged.",
        previous=previous,
        client=llm,
        model="test-model",
    )
    assert not result.validation.ok
    assert any(v.code == "constraint_mismatch" for v in result.validation.blocking)
    expected = previous.days[1].model_copy(deep=True)
    for activity in expected.activities:
        activity.highlights = []
        activity.place_summary = None
    assert result.itinerary.days[1] == expected
    assert len(llm.requests) == 4
    assert "every remaining reference" in llm.requests[1]["messages"][-1]["content"]
