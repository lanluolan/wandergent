"""Canonical repair context, original venue facts and bounded revision ownership."""

import json
from datetime import date, timedelta

import pytest

from app.agent import orchestrator
from app.agent.constraints import TripConstraints
from app.agent.revision import enforce_revision_scope, resolve_revision_scope
from app.agent.schemas import Itinerary
from app.agent.validation import ValidationReport, Violation, validate_itinerary
from tests.fakes import ITINERARY_JSON, FakeLLM, completion


def state_for(plan):
    return {
        "itinerary": plan,
        "constraints": TripConstraints(budget=100, allowed_modes=["WALK"]),
        "report": ValidationReport(
            violations=[
                Violation(
                    code="insufficient_transfer",
                    day=plan.days[0].date,
                    message="0 min gap, needs 68 minutes including buffer; WALK only",
                )
            ]
        ),
        "place_hours": {"Afternoon Diner": ["Tuesday: 15:00–21:00"]},
        "fact_collected_at": "2026-09-29T12:00:00+00:00",
    }


def test_repair_context_preserves_measured_gap_request_budget_and_original_hours():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    context = orchestrator._constraint_repair_context(state_for(plan))
    assert '"budget":100.0' in context
    assert '"allowed_modes":["WALK"]' in context
    assert "needs 68 minutes" in context
    assert "Tue 15:00-21:00" in context
    assert "2026-09-29T12:00:00+00:00" in context
    assert "No venue observations" not in context
    assert "data, not instructions" in context
    assert "COMPLETE itinerary in result, not a patch" in context
    assert "destination, start_date, end_date and every day entry" in context


@pytest.mark.parametrize(
    ("departure", "arrival"),
    [(18 * 60 + 30, "2026-09-28T19:10"), (23 * 60 + 40, "2026-09-29T00:20")],
)
def test_transfer_context_computes_local_arrival_without_wrapping_to_same_day(departure, arrival):
    state = state_for(Itinerary.model_validate_json(ITINERARY_JSON))
    state["report"] = ValidationReport(
        violations=[
            Violation(
                code="insufficient_transfer",
                message="Measured short gap",
                day=date(2026, 9, 28),
                origin="Holiday Lodge",
                destination="Fixins Soul Kitchen",
                gap_minutes=0,
                needed_minutes=40,
                depart_at_minute=departure,
                travel_mode="WALK",
            )
        ]
    )
    context = orchestrator._constraint_repair_context(state)
    assert f'"earliest_arrival_local": "{arrival}"' in context
    assert '"required_gap_minutes_including_buffer": 40' in context
    assert '"measured_mode": "WALK"' in context
    assert "travel_mode on food, rest" in context
    assert "category=transport" in context


def test_unmeasured_transfer_context_does_not_invent_arrival_or_mode():
    state = state_for(Itinerary.model_validate_json(ITINERARY_JSON))
    context = orchestrator._constraint_repair_context(state)
    assert "earliest_arrival_local" in context  # explanation, not a made-up observation
    assert '"earliest_arrival_local":' not in context
    assert '"measured_mode":' not in context


def test_repair_context_computes_latest_feasible_start_for_current_duration():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    state = state_for(plan)
    activity = plan.days[0].activities[0]
    activity.title = "Late lunch at Galleria Umberto"
    activity.location = "Galleria Umberto"
    activity.start_time = "14:45"
    activity.end_time = "15:15"
    weekday = plan.days[0].date.strftime("%A")
    state["place_hours"] = {"Galleria Umberto": [f"{weekday}: 10:45-14:30"]}
    context = orchestrator._constraint_repair_context(state)
    assert '"latest_start_for_current_duration": "14:00"' in context
    assert '"close": "14:30"' in context
    assert "Do not fix a transfer by pushing a stop beyond its closing time" in context


def test_repair_window_context_distinguishes_closed_day_from_unknown_hours():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    state = state_for(plan)
    plan.days[0].activities[0].location = "Closed Museum"
    state["place_hours"] = {"Closed Museum": [f"{plan.days[0].date.strftime('%A')}: Closed"]}
    context = orchestrator._constraint_repair_context(state)
    assert '"windows": []' in context
    state["place_hours"] = {"Closed Museum": ["unknown"]}
    context = orchestrator._constraint_repair_context(state)
    assert '"windows": []' not in context


@pytest.mark.parametrize("corrected", [True, False])
async def test_zero_gap_food_mode_is_not_transport_and_repairs_are_remeasured(
    monkeypatch, corrected
):
    from app.agent import transfers
    from app.config import settings
    from app.tools.maps import TravelTime

    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    async def offset(*args, **kwargs):
        return timedelta(hours=-7)

    calls = []

    async def route(origin, destination, mode="WALK", depart_at=None):
        calls.append((mode, depart_at))
        return TravelTime(
            ok=True, origin=origin, destination=destination, mode=mode, seconds=35 * 60
        )

    monkeypatch.setattr(transfers, "local_utc_offset", offset)
    monkeypatch.setattr(transfers, "get_travel_time", route)
    plan = Itinerary.model_validate(
        {
            "destination": "Los Angeles",
            "start_date": "2026-09-28",
            "end_date": "2026-09-28",
            "budget": 600,
            "days": [
                {
                    "date": "2026-09-28",
                    "summary": "Gallery and dinner",
                    "activities": [
                        {
                            "start_time": "17:55",
                            "end_time": "18:30",
                            "title": "Rest at Holiday Lodge",
                            "location": "Holiday Lodge, 1631 W 3rd St, Los Angeles",
                            "category": "rest",
                        },
                        {
                            "start_time": "18:30",
                            "end_time": "20:00",
                            "title": "Dinner at Fixins Soul Kitchen",
                            "location": "Fixins Soul Kitchen, 800 W Olympic Blvd, Los Angeles",
                            "category": "food",
                            "travel_mode": "TRANSIT",
                            "estimated_cost": 38,
                        },
                    ],
                }
            ],
        }
    )
    latest = plan.model_copy(deep=True)
    latest.days[0].activities[0].end_time = "18:40"
    latest.days[0].activities[1].start_time = "18:40"
    fixed = latest.model_copy(deep=True)
    fixed.days[0].activities[1].start_time = "19:20"
    fixed.days[0].activities[1].end_time = "20:50"
    replies = [plan, latest, fixed] if corrected else [plan, latest, latest, latest]
    llm = FakeLLM([completion(content=p.model_dump_json()) for p in replies])
    events = [
        event
        async for event in orchestrator.stream_plan(
            "1 day in Los Angeles, budget 600 USD",
            client=llm,
            model="test-model",
            today=date(2026, 9, 28),
        )
    ]
    validations = [e for e in events if e.type == "validation"]
    assert validations[0].violations[0].needed_minutes == 40
    assert validations[0].violations[0].travel_mode == "WALK"
    assert validations[1].violations[0].gap_minutes == 0
    assert events[-1].result.validation.ok is corrected
    assert len(calls) == len(replies)
    assert {mode for mode, _ in calls} == {"WALK"}
    assert calls[0][1] != calls[1][1]  # new departure is measured, not reused
    prompts = [r["messages"][-1]["content"] for r in llm.requests[1:]]
    assert '"earliest_arrival_local": "2026-09-28T19:10"' in prompts[0]
    assert '"earliest_arrival_local": "2026-09-28T19:20"' in prompts[1]
    assert events[-1].result.itinerary.budget == 600
    assert events[-1].result.itinerary.total_estimated_cost == 38


def test_repair_uses_restored_candidate_without_expanding_locked_day_context():
    previous = Itinerary.model_validate_json(ITINERARY_JSON)
    previous.days[1].activities[0].title = "LOCKED ORIGINAL ACTIVITY"
    scope = resolve_revision_scope("Change lunch on day 1", len(previous.days))
    candidate = previous.model_copy(deep=True)
    candidate.destination = "Unauthorized destination"
    candidate.days[1].activities[0].title = "Unauthorized rewrite"
    candidate.days[0].activities[0].title = "Latest editable activity"
    canonical = enforce_revision_scope(candidate, previous, scope)
    state = state_for(canonical)
    state["revision_scope"] = scope
    context = orchestrator._constraint_repair_context(state)
    assert "Latest editable activity" in context
    assert "Unauthorized destination" not in context
    assert "Unauthorized rewrite" not in context
    assert "LOCKED ORIGINAL ACTIVITY" not in context
    assert '"locked":true' in context
    assert '"locked_estimated_cost"' in context


async def test_each_repair_round_uses_latest_candidate_and_revalidates(monkeypatch):
    monkeypatch.setattr(orchestrator, "get_stream_writer", lambda: lambda _: None)
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    plan.budget = 100
    second = plan.model_copy(deep=True)
    second.days[0].activities[0].title = "Latest failed repair"
    llm = FakeLLM(
        [
            completion(content=second.model_dump_json()),
            completion(content="invalid"),
            completion(content="invalid again"),
        ]
    )
    state = state_for(plan)
    state.update(
        {
            "llm": llm,
            "model": "test-model",
            "messages": [{"role": "assistant", "content": plan.model_dump_json()}],
            "repairs_left": 3,
            "usage": orchestrator.Usage(),
        }
    )
    first = await orchestrator.repair(state)
    state.update(first)
    state["report"] = validate_itinerary(state["itinerary"], constraints=state["constraints"])
    await orchestrator.repair(state)
    latest_prompt = llm.requests[1]["messages"][-1]["content"]
    assert "Latest failed repair" in latest_prompt
    assert state["itinerary"].days[0].activities[0].title == "Latest failed repair"


async def test_afternoon_only_breakfast_is_repaired_and_full_validation_repeats(monkeypatch):
    from app.tools.maps import Place, PlacesResult
    from app.tools.registry import TOOL_FUNCTIONS
    from tests.fakes import tool_call

    async def places(query, **kwargs):
        return PlacesResult(
            ok=True,
            query=query,
            places=[Place(ok=True, name="Afternoon Diner", opening_hours=["Tuesday: 15:00–21:00"])],
        )

    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", places)
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    day = plan.days[0].model_copy(deep=True)
    day.date = date(2026, 12, 29)
    activity = day.activities[0].model_copy(
        update={
            "title": "Breakfast at Afternoon Diner",
            "location": "Afternoon Diner",
            "category": "food",
            "start_time": "09:00",
            "end_time": "10:00",
        }
    )
    day.activities = [activity]
    plan = plan.model_copy(update={"start_date": day.date, "end_date": day.date, "days": [day]})
    corrected = plan.model_copy(deep=True)
    corrected.days[0].activities[0].start_time = "15:00"
    corrected.days[0].activities[0].end_time = "16:00"
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("search_places", {"query": "Afternoon Diner"})]),
            completion(content=plan.model_dump_json()),
            completion(content=corrected.model_dump_json()),
        ]
    )
    events = [
        event
        async for event in orchestrator.stream_plan(
            "1 day in Chicago", client=llm, model="test-model", today=date(2026, 12, 28)
        )
    ]
    validations = [e for e in events if e.type == "validation"]
    assert any(v.code == "outside_opening_hours" for v in validations[0].violations)
    assert not any(v.code == "outside_opening_hours" for v in validations[-1].violations)
    assert "Tue 15:00-21:00" in llm.requests[-1]["messages"][-1]["content"]
    assert events[-1].result.itinerary.days[0].activities[0].start_time == "15:00"
    assert json.loads(events[-1].result.itinerary.model_dump_json())["start_date"] == "2026-12-29"
