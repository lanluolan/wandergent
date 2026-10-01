"""Observed-hours fallback must respect intent and earn feasibility with fresh routes."""

from datetime import date, timedelta

import pytest

from app.agent import orchestrator, transfers
from app.agent.constraints import TripConstraints
from app.agent.results import ToolCallRecord
from app.agent.revision import VenueRemoval, resolve_revision_scope
from app.agent.schedule_repair import hours_candidates
from app.agent.schemas import Itinerary
from app.agent.validation import validate_itinerary
from app.config import settings
from app.observability import RunTrace, _trace, route_facts
from app.tools.maps import TravelTime

DAY = date(2026, 10, 21)  # Wednesday
BAND = "PRICE_LEVEL_MODERATE"


def plan():
    return Itinerary.model_validate(
        {
            "destination": "Santa Monica",
            "start_date": str(DAY),
            "end_date": str(DAY),
            "budget": 100,
            "days": [
                {
                    "date": str(DAY),
                    "summary": "Beach and dinner",
                    "activities": [
                        {
                            "title": "Beach",
                            "category": "sightseeing",
                            "location": "Santa Monica Beach",
                            "start_time": "14:00",
                            "end_time": "16:00",
                        },
                        {
                            "title": "Dinner at Blue Daisy",
                            "category": "food",
                            "location": "Blue Daisy",
                            "start_time": "17:00",
                            "end_time": "18:00",
                            "estimated_cost": 30,
                            "highlights": ["Blue Daisy pancakes"],
                            "notes": "Try the old venue's menu",
                        },
                    ],
                }
            ],
        }
    )


def facts(**changes):
    alternative = {
        "name": "Evening Restaurant",
        "address": "10 Ocean Ave, Santa Monica",
        "types": ["restaurant"],
        "opening_hours": ["Wednesday: 16:00-22:00"],
        "price_level": BAND,
    }
    alternative.update(changes)
    return (
        {
            "Blue Daisy": ["Wednesday: 08:00-15:00"],
            alternative["name"]: alternative["opening_hours"],
        },
        {"Blue Daisy": BAND, alternative["name"]: alternative["price_level"]},
        [
            ToolCallRecord(
                name="search_places",
                ok=True,
                arguments={"query": "restaurants"},
                fact_payload={"places": [alternative]},
            )
        ],
    )


def candidates(value=None, *, request="", scope=None, **changes):
    return list(hours_candidates(value or plan(), *facts(**changes), request=request, scope=scope))


def test_dinner_uses_observed_open_restaurant_not_breakfast_hours_or_old_menu():
    previous = plan()
    choices = candidates(previous)
    assert len(choices) == 1
    dinner = choices[0].days[0].activities[1]
    assert dinner.title == "Dinner at Evening Restaurant"
    assert dinner.location == "Evening Restaurant, 10 Ocean Ave, Santa Monica"
    assert (dinner.start_time, dinner.end_time) == ("17:00", "18:00")
    assert dinner.highlights == []
    assert "estimate" in dinner.notes
    assert choices[0].total_estimated_cost == previous.total_estimated_cost
    assert previous.days[0].activities[1].title == "Dinner at Blue Daisy"


@pytest.mark.parametrize(
    "changes",
    [
        {"types": []},
        {"types": ["museum"]},
        {"opening_hours": []},
        {"opening_hours": ["Wednesday: Closed"]},
        {"opening_hours": ["Thursday: 16:00-22:00"]},
        {"price_level": "PRICE_LEVEL_EXPENSIVE"},
        {"price_level": None},
    ],
)
def test_unknown_wrong_type_closed_or_different_price_band_cannot_be_an_alternative(changes):
    assert candidates(**changes) == []


@pytest.mark.parametrize(
    "user_request,notes",
    [
        ("Dinner at Blue Daisy", None),
        ("Beach and dinner", "Reservation confirmed"),
    ],
)
def test_explicit_venue_and_booking_cannot_be_substituted(user_request, notes):
    value = plan()
    value.days[0].activities[1].notes = notes
    assert candidates(value, request=user_request) == []


def test_locked_day_cannot_change_and_duplicate_branches_are_not_guessed():
    assert candidates(scope=resolve_revision_scope("Keep day 1 unchanged", 1)) == []
    hours, prices, records = facts()
    duplicate = dict(records[0].fact_payload["places"][0], address="99 Other St")
    records[0].fact_payload["places"].append(duplicate)
    assert list(hours_candidates(plan(), hours, prices, records)) == []


@pytest.mark.parametrize("dietary", ["vegetarian only", "halal food", "nut allergy", "素食"])
def test_dietary_preferences_cannot_be_assumed_at_an_alternative(dietary):
    assert list(hours_candidates(plan(), *facts(), dietary_context=dietary)) == []
    assert candidates(request=f"Dinner, {dietary}") == []


def test_request_level_booking_protection_and_static_budget_check():
    assert candidates(request="I have a reservation for dinner") == []
    choice = candidates()[0]
    assert not validate_itinerary(choice, *facts()[:2], constraints=TripConstraints(budget=10)).ok


def test_generic_visit_can_move_but_explicit_time_cannot():
    value = plan()
    value.days[0].activities = [
        value.days[0]
        .activities[1]
        .model_copy(
            update={
                "title": "Visit Blue Daisy",
                "category": "activity",
                "start_time": "07:00",
                "end_time": "08:00",
                "notes": None,
            }
        )
    ]
    moved = candidates(value)
    assert moved[0].days[0].activities[0].start_time == "08:00"
    assert moved[0].days[0].activities[0].end_time == "09:00"
    assert candidates(value, request="Visit Blue Daisy at 07:00") == []


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(orchestrator, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    async def offset(*args, **kwargs):
        return timedelta(hours=-7)

    monkeypatch.setattr(transfers, "local_utc_offset", offset)
    hours, prices, records = facts()
    value = plan()
    return {
        "itinerary": value,
        "place_hours": hours,
        "place_prices": prices,
        "records": records,
        "constraints": TripConstraints(budget=100, allowed_modes=["WALK"]),
        "report": validate_itinerary(value, hours, prices),
        "repairs_left": 2,
        "raw_request": "Beach and dinner",
        "removals": [],
    }


@pytest.mark.parametrize("route_seconds,accepted", [(600, True), (3600, False), (None, False)])
async def test_fallback_requires_fresh_permitted_routes_and_preserves_original_when_rejected(
    state,
    monkeypatch,
    route_seconds,
    accepted,
):
    calls = []

    async def route(origin, destination, mode="WALK", depart_at=None):
        calls.append((origin, destination, mode, depart_at))
        changed = destination.startswith("Evening Restaurant")
        seconds = route_seconds if changed else 600
        return TravelTime(
            ok=seconds is not None,
            origin=origin,
            destination=destination,
            mode=mode,
            seconds=seconds,
            code="timed_out" if seconds is None else None,
        )

    monkeypatch.setattr(transfers, "get_travel_time", route)
    trace = RunTrace()
    token = _trace.set(trace)
    original = state["itinerary"].model_dump_json()
    try:
        result = await orchestrator.validate(state)
        assert result["hours_fallback_attempted"]
        assert result["report"].ok is accepted
        assert len(calls) == 2 and {call[2] for call in calls} == {"WALK"}
        assert all(call[3] is not None for call in calls)
        assert state["itinerary"].model_dump_json() == original
        if accepted:
            assert result["itinerary"].days[0].activities[1].title == "Dinner at Evening Restaurant"
            assert route_facts()[-1]["destination"].startswith("Evening Restaurant")
        else:
            assert result["itinerary"].model_dump_json() == original
            assert route_facts()[-1]["destination"] == "Blue Daisy"
            assert any(v.code == "outside_opening_hours" for v in result["report"].blocking)
        repair = next(s for s in trace.spans if s.name == "repair.schedule")
        assert repair.attributes["wandergent.repair.output_accepted"] is accepted
        assert "Blue Daisy" not in str(repair.attributes)
        state.update(result)
        await orchestrator.validate(state)
        assert len([s for s in trace.spans if s.name == "repair.schedule"]) == 1
    finally:
        _trace.reset(token)


async def test_fallback_cannot_bypass_budget_removals_or_conflicts(state, monkeypatch):
    async def route(origin, destination, mode="WALK", **kwargs):
        return TravelTime(ok=True, origin=origin, destination=destination, mode=mode, seconds=600)

    monkeypatch.setattr(transfers, "get_travel_time", route)
    state["removals"] = [VenueRemoval("Evening Restaurant", 0)]
    result = await orchestrator.validate(state)
    assert not result["report"].ok and result["itinerary"] == state["itinerary"]
    state.pop("hours_fallback_attempted", None)
    state["removals"] = []
    state["constraints"] = TripConstraints(budget=10)
    result = await orchestrator.validate(state)
    assert {v.code for v in result["report"].blocking} >= {"over_budget", "outside_opening_hours"}
    assert result["itinerary"] == state["itinerary"]


def test_retimed_candidate_cannot_be_assumed_free_of_overlap():
    value = plan()
    value.days[0].activities[1].title = "Visit Blue Daisy"
    value.days[0].activities[1].category = "activity"
    hours, prices, records = facts()
    original = value.model_dump_json()
    choices = list(hours_candidates(value, hours, prices, records))
    assert choices and all(validate_itinerary(candidate, hours, prices).ok for candidate in choices)
    assert value.model_dump_json() == original
    value.days[0].activities[0].notes = "Confirmed reservation"
    assert list(hours_candidates(value, hours, prices, records)) == []


def test_conflicting_partial_candidate_does_not_starve_joint_restaurant_substitutions():
    value = plan()
    value.days[0].activities[0].notes = "Reservation confirmed"
    value.days[0].activities[0].start_time = "13:30"
    value.days[0].activities[0].end_time = "14:30"
    lunch = (
        value.days[0]
        .activities[1]
        .model_copy(
            update={
                "title": "Late lunch at Lunch Cafe",
                "location": "Lunch Cafe",
                "start_time": "14:45",
                "end_time": "15:15",
                "notes": None,
            }
        )
    )
    value.days[0].activities.insert(1, lunch)
    hours, prices, records = facts()
    hours["Lunch Cafe"] = ["Wednesday: 10:45-14:30"]
    prices["Lunch Cafe"] = BAND
    observed = records[0].fact_payload["places"][0]
    alternatives = [
        dict(observed, name=f"All Day Restaurant {i}", opening_hours=["Wednesday: 14:00-22:00"])
        for i in range(4)
    ]
    records[0].fact_payload["places"] = alternatives
    for alternative in alternatives:
        hours[alternative["name"]] = alternative["opening_hours"]
        prices[alternative["name"]] = BAND
    choices = list(hours_candidates(value, hours, prices, records))
    assert choices and len(choices) <= 4
    assert all(validate_itinerary(choice, hours, prices).ok for choice in choices)
    assert all(choice.days[0].activities[0] == value.days[0].activities[0] for choice in choices)


def packed_lunch_plan():
    value = plan()
    value.days[0].activities = [
        value.days[0]
        .activities[0]
        .model_copy(
            update={
                "title": "Museum",
                "location": "Museum",
                "start_time": "10:00",
                "end_time": "12:00",
            }
        ),
        value.days[0]
        .activities[0]
        .model_copy(
            update={
                "title": "Walk",
                "category": "transport",
                "travel_mode": "WALK",
                "start_time": "12:00",
                "end_time": "12:17",
            }
        ),
        value.days[0]
        .activities[1]
        .model_copy(
            update={
                "title": "Lunch",
                "location": "Lunch",
                "start_time": "12:17",
                "end_time": "13:17",
                "notes": None,
            }
        ),
        value.days[0]
        .activities[0]
        .model_copy(
            update={
                "title": "Transit",
                "category": "transport",
                "travel_mode": "TRANSIT",
                "start_time": "13:17",
                "end_time": "13:47",
            }
        ),
        value.days[0]
        .activities[0]
        .model_copy(
            update={
                "title": "Gallery",
                "location": "Gallery",
                "start_time": "13:47",
                "end_time": "15:02",
            }
        ),
        value.days[0]
        .activities[0]
        .model_copy(
            update={
                "title": "Walk",
                "category": "transport",
                "travel_mode": "WALK",
                "start_time": "15:02",
                "end_time": "15:10",
            }
        ),
        value.days[0]
        .activities[1]
        .model_copy(
            update={
                "title": "Late lunch at Lunch Cafe",
                "location": "Lunch Cafe",
                "start_time": "15:10",
                "end_time": "15:30",
                "notes": None,
            }
        ),
    ]
    return value


def test_closing_time_can_backfill_only_flexible_prefix_without_changing_venues_or_costs():
    value = packed_lunch_plan()
    original = value.model_dump_json()
    hours = {"Lunch Cafe": ["Wednesday: 10:45-14:30"], "Museum": ["Wednesday: 10:00-17:00"]}
    choices = list(hours_candidates(value, hours, {}, []))
    assert len(choices) == 1
    changed = choices[0]
    assert changed.days[0].activities[-1].end_time == "14:30"
    assert changed.days[0].activities[4].start_time == "13:32"
    assert changed.days[0].activities[4].end_time == "14:02"
    assert changed.days[0].activities[2].end_time == "13:02"
    assert [a.location for a in changed.days[0].activities] == [
        a.location for a in value.days[0].activities
    ]
    assert changed.total_estimated_cost == value.total_estimated_cost
    assert not any(
        v.code in {"time_conflict", "outside_opening_hours"}
        for v in validate_itinerary(changed, hours).blocking
    )
    assert value.model_dump_json() == original


@pytest.mark.parametrize(
    "user_request,notes",
    [
        ("Lunch Cafe and Lunch at the original times", "Reservation confirmed"),
        ("Spend 2 hours at the museum", None),
        ("Visit Gallery", None),
    ],
)
def test_prefix_reflow_cannot_shorten_booked_named_or_requested_duration_stops(user_request, notes):
    value = packed_lunch_plan()
    value.days[0].activities[4].notes = notes
    hours = {"Lunch Cafe": ["Wednesday: 10:45-14:30"], "Gallery": ["Wednesday: 10:00-17:00"]}
    assert list(hours_candidates(value, hours, {}, [], request=user_request)) == []


def test_backfill_cannot_move_a_prefix_visit_before_its_observed_opening():
    value = packed_lunch_plan()
    hours = {"Lunch Cafe": ["Wednesday: 10:45-14:30"], "Gallery": ["Wednesday: 13:45-17:00"]}
    assert list(hours_candidates(value, hours, {}, [])) == []


@pytest.mark.parametrize("changed_route_ok", [True, False])
async def test_prefix_reflow_earns_acceptance_with_fresh_routes_or_preserves_original(
    state, monkeypatch, changed_route_ok
):
    state["itinerary"] = packed_lunch_plan()
    state["place_hours"] = {"Lunch Cafe": ["Wednesday: 10:45-14:30"]}
    state["place_prices"] = {}
    state["records"] = []
    state["constraints"] = TripConstraints(budget=100, allowed_modes=["WALK", "TRANSIT"])
    state["report"] = validate_itinerary(state["itinerary"], state["place_hours"])
    calls = []

    async def route(origin, destination, mode="WALK", depart_at=None):
        calls.append((mode, depart_at))
        changed_last = destination == "Lunch Cafe" and depart_at.hour == 21  # local 14:00 PDT
        usable = changed_route_ok or not changed_last
        return TravelTime(
            ok=usable,
            origin=origin,
            destination=destination,
            mode=mode,
            seconds=60 if usable else None,
            code=None if usable else "timed_out",
        )

    monkeypatch.setattr(transfers, "get_travel_time", route)
    original = state["itinerary"].model_dump_json()
    trace = RunTrace()
    token = _trace.set(trace)
    try:
        result = await orchestrator.validate(state)
        assert result["report"].ok is changed_route_ok
        assert state["itinerary"].model_dump_json() == original
        assert len(calls) == 6  # original three hops, then all candidate hops freshly measured
        assert {mode for mode, _ in calls} == {"WALK", "TRANSIT"}
        assert len({departure for _, departure in calls}) > 3
        if not changed_route_ok:
            assert result["itinerary"].model_dump_json() == original
        assert any(s.name == "repair.schedule" for s in trace.spans)
    finally:
        _trace.reset(token)


def test_explicit_specialty_request_cannot_be_replaced_by_generic_restaurant():
    assert candidates(request="change day 1 dinner to a ramen restaurant") == []


async def test_live_graph_uses_fallback_after_model_repeats_closure_and_binds_new_evidence(
    monkeypatch,
):
    from app.agent.orchestrator import plan_trip
    from app.tools.maps import Place, PlacesResult
    from app.tools.registry import TOOL_FUNCTIONS
    from tests.fakes import FakeLLM, completion, tool_call

    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    async def offset(*args, **kwargs):
        return timedelta(hours=-7)

    async def route(origin, destination, mode="WALK", **kwargs):
        return TravelTime(ok=True, origin=origin, destination=destination, mode=mode, seconds=600)

    async def places(query, **kwargs):
        alternative = facts()[2][0].fact_payload["places"][0]
        return PlacesResult(
            ok=True,
            query=query,
            places=[
                Place(
                    ok=True,
                    name="Blue Daisy",
                    opening_hours=["Wednesday: 08:00-15:00"],
                    price_level=BAND,
                    types=["restaurant"],
                ),
                Place(ok=True, **alternative),
            ],
        )

    monkeypatch.setattr(transfers, "local_utc_offset", offset)
    monkeypatch.setattr(transfers, "get_travel_time", route)
    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", places)
    broken = plan().model_dump_json()
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("search_places", {"query": "dinner restaurants"})]),
            completion(content=broken),
            completion(content=broken),
        ]
    )
    result = await plan_trip(
        "1 day in Santa Monica, budget 100 USD", client=llm, model="test-model"
    )
    assert result.validation.ok
    assert result.itinerary.days[0].activities[1].title == "Dinner at Evening Restaurant"
    assert len(llm.requests) == 3
    evidence = result.activity_evidence[1]
    assert evidence.venue_verified and evidence.hours_available
    assert evidence.route_mode == "WALK" and evidence.route_seconds == 600
