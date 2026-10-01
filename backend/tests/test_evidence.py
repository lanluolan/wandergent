from app.agent.evidence import activity_evidence
from app.agent.results import ToolCallRecord
from app.agent.schemas import Itinerary
from tests.fakes import ITINERARY_JSON


def test_venue_match_preserves_original_time_and_never_verifies_price():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    location = plan.days[0].activities[0].location
    record = ToolCallRecord(
        name="search_places",
        arguments={},
        ok=True,
        collected_at="2026-09-29T10:00:00+00:00",
        cache_status="shared",
        fact_payload={
            "places": [
                {
                    "name": location,
                    "opening_hours": ["Monday: 09:00–17:00"],
                    "price_level": "PRICE_LEVEL_MODERATE",
                }
            ]
        },
    )
    evidence = activity_evidence(plan, [record])
    assert evidence[0].venue_verified
    assert evidence[0].collected_at == record.collected_at
    assert evidence[0].hours_available
    assert evidence[0].price_confidence == "estimate"
    assert evidence[0].recheck_before_departure
    plan.days[0].activities[0].location = f"Near {location}"
    assert not activity_evidence(plan, [record])[0].venue_verified
    assert "fact_payload" not in record.model_dump()


def test_failures_and_unknown_places_are_unverified():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    record = ToolCallRecord(
        name="search_places",
        arguments={},
        ok=False,
        fact_payload={"places": [{"name": plan.days[0].activities[0].location}]},
    )
    assert not any(e.venue_verified for e in activity_evidence(plan, [record]))
    assert activity_evidence(None, [record]) == []


def test_ambiguous_names_do_not_verify_the_wrong_branch():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    name = plan.days[0].activities[0].location
    record = ToolCallRecord(
        name="search_places",
        arguments={},
        ok=True,
        fact_payload={
            "places": [{"name": name, "address": "Branch A"}, {"name": name, "address": "Branch B"}]
        },
    )
    assert not activity_evidence(plan, [record])[0].venue_verified


def test_route_evidence_matches_final_day_and_crosses_transport_activity():
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    activities = plan.days[0].activities
    first, second = activities[0], activities[1]
    transport = first.model_copy(
        update={
            "category": "transport",
            "start_time": first.end_time,
            "end_time": second.start_time,
        }
    )
    activities.insert(1, transport)
    route = {
        "origin": first.location,
        "destination": second.location,
        "mode": "WALK",
        "seconds": 600,
        "collected_at": "2026-09-30T10:00:00+00:00",
        "departure": "2026-10-07T15:00:00+00:00",
        "day": str(plan.days[0].date),
    }
    evidence = activity_evidence(plan, [], [route])
    assert evidence[1].route_seconds is None
    matched = evidence[2]
    assert matched.route_seconds == 600
    assert matched.route_source == "Google Routes"
    route["day"] = "2099-01-01"
    assert activity_evidence(plan, [], [route])[2].route_seconds is None
