"""Only request-owned confirmation may exempt a live plan from choosing lodging."""

from app.agent import orchestrator
from app.agent.constraints import TripConstraints, resolve_constraints
from app.agent.schemas import Itinerary
from app.agent.validation import validate_itinerary
from tests.fakes import ITINERARY_JSON, FakeLLM, completion


def no_hotel():
    value = Itinerary.model_validate_json(ITINERARY_JSON)
    for day in value.days:
        day.activities = [a for a in day.activities if a.category != "accommodation"]
    value.notes = ["Lodging is already arranged; no hotel needed."]
    return value


def test_generated_lodging_note_is_not_request_confirmation():
    value = no_hotel()
    assert any(
        v.code == "missing_accommodation"
        for v in validate_itinerary(value, constraints=TripConstraints()).blocking
    )
    assert validate_itinerary(value, constraints=TripConstraints(lodging_arranged=True)).ok


def test_lodging_confirmation_inherits_only_request_owned_state_and_new_trip_resets_it():
    confirmed = resolve_constraints("2 days in Boston, lodging is already arranged")
    assert confirmed.lodging_arranged is True
    assert resolve_constraints("Change lunch", previous=confirmed).lodging_arranged is True
    assert (
        resolve_constraints("New trip: 2 days in Chicago", previous=confirmed).lodging_arranged
        is None
    )
    assert resolve_constraints("Please book a hotel", previous=confirmed).lodging_arranged is False
    assert resolve_constraints("My hotel is not booked").lodging_arranged is False
    assert (
        resolve_constraints("Do not assume my hotel is already booked").lodging_arranged is not True
    )


async def test_live_graph_repairs_invented_lodging_confirmation_and_preserves_failure():
    value = no_hotel()
    llm = FakeLLM([completion(content=value.model_dump_json()) for _ in range(4)])
    result = await orchestrator.plan_trip("2 days in Chicago", client=llm, model="test-model")
    assert not result.validation.ok
    assert any(v.code == "missing_accommodation" for v in result.validation.blocking)
    assert len(llm.requests) == 4
    assert result.constraints.lodging_arranged is None
