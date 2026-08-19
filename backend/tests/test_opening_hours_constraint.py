"""The opening-hours constraint, end to end through the validator.

Budget, timing and routing all pass cleanly for a plan that arrives at a locked door.
This is the check that closes that hole -- and it reads hours the run already fetched
from Google, not hours the model reported, so a plan cannot satisfy it by asserting.
"""

import json

from app.agent.orchestrator import harvest_place_hours
from app.agent.schemas import Itinerary
from app.agent.validation import validate_itinerary

# Verbatim from the live API. 2026-09-07 is a Monday, 2026-09-08 a Tuesday.
ART_INSTITUTE = [
    "Monday: 11:00 AM – 5:00 PM",
    "Tuesday: Closed",
    "Wednesday: 11:00 AM – 5:00 PM",
    "Thursday: 11:00 AM – 8:00 PM",
    "Friday: 11:00 AM – 5:00 PM",
    "Saturday: 11:00 AM – 5:00 PM",
    "Sunday: 11:00 AM – 5:00 PM",
]
HOURS = {"Art Institute of Chicago": ART_INSTITUTE}


def plan(
    start: str, end: str, title: str = "Art Institute of Chicago", day: str = "2026-09-07"
) -> Itinerary:
    return Itinerary.model_validate(
        {
            "destination": "Chicago",
            "start_date": day,
            "end_date": day,
            "budget": 500,
            "days": [
                {
                    "date": day,
                    "summary": "museum day",
                    "activities": [
                        {
                            "start_time": start,
                            "end_time": end,
                            "title": title,
                            "location": title,
                            "estimated_cost": 30.0,
                        }
                    ],
                }
            ],
        }
    )


def codes(itinerary: Itinerary, hours=HOURS) -> list[str]:
    return [v.code for v in validate_itinerary(itinerary, hours).violations]


def test_a_visit_inside_opening_hours_passes() -> None:
    assert codes(plan("14:10", "17:00")) == []


def test_the_exact_failure_this_check_exists_for() -> None:
    """09:00 at a museum that opens at 11. Every other check is happy with it."""
    itinerary = plan("09:00", "12:00")

    assert validate_itinerary(itinerary, None).ok  # nothing else objects
    assert "outside_opening_hours" in codes(itinerary)


def test_a_closed_weekday_is_caught() -> None:
    assert "outside_opening_hours" in codes(plan("14:00", "16:00", day="2026-09-08"))


def test_the_message_says_when_it_is_actually_open() -> None:
    """Fed back as a repair instruction, so it has to carry the fix, not just the fault."""
    violation = next(
        v
        for v in validate_itinerary(plan("09:00", "12:00"), HOURS).violations
        if v.code == "outside_opening_hours"
    )

    assert "11:00-17:00" in violation.message
    assert "Monday" in violation.message


def test_a_venue_the_run_never_looked_up_gets_no_opinion() -> None:
    # Asserted on the code rather than an empty list: an odd hour would trip
    # `unsociable_hours`, which is a different rule with a different opinion.
    assert "outside_opening_hours" not in codes(
        plan("09:00", "12:00", title="Some Unsearched Diner")
    )


def test_no_harvested_hours_at_all_changes_nothing() -> None:
    """No maps key, or a caller that never searched. Silence is not a closure."""
    assert "outside_opening_hours" not in codes(plan("09:00", "12:00"), hours={})


def test_a_short_venue_name_does_not_attach_its_hours_to_everything() -> None:
    """ "Bar" would close every activity containing the word; a wrong closure is worse
    than no check."""
    assert "outside_opening_hours" not in codes(
        plan("09:00", "12:00", title="Bar Crawl"), hours={"Bar": ART_INSTITUTE}
    )


def test_transport_steps_are_exempt() -> None:
    """ "Walk to the museum" is not a visit and has no hours of its own."""
    itinerary = Itinerary.model_validate(
        {
            "destination": "Chicago",
            "start_date": "2026-09-07",
            "end_date": "2026-09-07",
            "days": [
                {
                    "date": "2026-09-07",
                    "summary": "museum day",
                    "activities": [
                        {
                            "start_time": "09:00",
                            "end_time": "09:20",
                            "title": "Walk to Art Institute of Chicago",
                            "category": "transport",
                        }
                    ],
                }
            ],
        }
    )

    assert "outside_opening_hours" not in codes(itinerary)


# --- harvesting -------------------------------------------------------------------


def test_hours_are_kept_from_a_search_reply() -> None:
    reply = json.dumps(
        {
            "ok": True,
            "query": "museum in Chicago",
            "places": [
                {"name": "Art Institute of Chicago", "opening_hours": ART_INSTITUTE},
                {"name": "Millennium Park", "opening_hours": []},
            ],
        }
    )
    collected: dict[str, list[str]] = {}

    harvest_place_hours("search_places", reply, collected)

    assert collected == {"Art Institute of Chicago": ART_INSTITUTE}


def test_a_replayed_cache_hit_is_unwrapped() -> None:
    """The tool cache nests the original reply under "result"; hours must survive it."""
    inner = json.dumps(
        {"places": [{"name": "Art Institute of Chicago", "opening_hours": ART_INSTITUTE}]}
    )
    collected: dict[str, list[str]] = {}

    harvest_place_hours("search_places", json.dumps({"repeat": True, "result": inner}), collected)

    assert "Art Institute of Chicago" in collected


def test_other_tools_and_junk_contribute_nothing_and_never_raise() -> None:
    collected: dict[str, list[str]] = {}

    harvest_place_hours("get_weather_forecast", '{"days": []}', collected)
    harvest_place_hours("search_places", "not json at all", collected)
    harvest_place_hours("search_places", "[1, 2, 3]", collected)
    harvest_place_hours("search_places", '{"places": ["a string"]}', collected)

    assert collected == {}


def test_the_harvest_matches_how_the_tool_actually_serialises() -> None:
    """The seam most likely to break silently.

    `harvest_place_hours` reads the serialised tool reply, so it depends on the field
    names `PlacesResult` emits. Renaming `Place.opening_hours` would leave every test
    above passing -- they build the JSON by hand -- while the constraint quietly stopped
    having any data to check. This builds the payload from the real models instead.
    """
    from app.tools.maps import Place, PlacesResult

    reply = PlacesResult(
        ok=True,
        query="art museum in Chicago",
        places=[
            Place(ok=True, name="The Art Institute of Chicago", opening_hours=ART_INSTITUTE),
            Place(ok=True, name="Millennium Park"),
        ],
    ).model_dump_json()

    collected: dict[str, list[str]] = {}
    harvest_place_hours("search_places", reply, collected)

    assert collected == {"The Art Institute of Chicago": ART_INSTITUTE}


# --- the repair loop --------------------------------------------------------------
#
# Everything above proves the violation is raised. None of it proves anything is done
# about it, and a constraint that only ever produces a warning is barely a constraint.
# This drives the whole path -- search_places -> harvest -> validate -> repair ->
# re-validate -- through the orchestrator, with no test-only seam in the production
# signature.

from datetime import date  # noqa: E402

import pytest  # noqa: E402

from app.agent.orchestrator import plan_trip  # noqa: E402
from app.tools.maps import Place, PlacesResult  # noqa: E402
from app.tools.registry import TOOL_FUNCTIONS  # noqa: E402
from tests.fakes import FakeLLM, completion, tool_call  # noqa: E402

TODAY = date(2026, 8, 5)


@pytest.fixture
def stub_search(monkeypatch):
    """`search_places` returning one venue with real hours, as the live tool would."""

    async def fake(query: str, near: str, **kwargs) -> PlacesResult:
        return PlacesResult(
            ok=True,
            query=query,
            places=[
                Place(
                    ok=True,
                    name="Art Institute of Chicago",
                    address="111 S Michigan Ave",
                    opening_hours=ART_INSTITUTE,
                )
            ],
        )

    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", fake)


def itinerary_at(start: str, end: str) -> str:
    return json.dumps(
        {
            "destination": "Chicago",
            "start_date": "2026-09-07",
            "end_date": "2026-09-07",
            "budget": 500,
            "days": [
                {
                    "date": "2026-09-07",
                    "summary": "museum day",
                    "activities": [
                        {
                            "start_time": start,
                            "end_time": end,
                            "title": "Art Institute of Chicago",
                            "location": "Art Institute of Chicago",
                            "estimated_cost": 30.0,
                        }
                    ],
                }
            ],
        }
    )


async def test_a_closed_venue_is_repaired_and_revalidated(stub_search) -> None:
    """09:00 at a venue that opens at 11, then fixed. The loop has to close."""
    llm = FakeLLM(
        [
            completion(
                tool_calls=[tool_call("search_places", {"query": "art museum", "near": "Chicago"})]
            ),
            completion(content=itinerary_at("09:00", "12:00")),
            completion(content=itinerary_at("14:00", "17:00")),
        ]
    )

    result = await plan_trip("art museums in Chicago", client=llm, model="test-model", today=TODAY)

    # Three turns: the search, the bad plan, the repair it was sent back for.
    assert len(llm.requests) == 3
    assert result.validation is not None and result.validation.ok
    assert result.itinerary.days[0].activities[0].start_time == "14:00"


async def test_the_repair_instruction_carries_the_real_hours(stub_search) -> None:
    """The model can only fix it if told when the place is actually open."""
    llm = FakeLLM(
        [
            completion(
                tool_calls=[tool_call("search_places", {"query": "art museum", "near": "Chicago"})]
            ),
            completion(content=itinerary_at("09:00", "12:00")),
            completion(content=itinerary_at("14:00", "17:00")),
        ]
    )

    await plan_trip("art museums in Chicago", client=llm, model="test-model", today=TODAY)

    repair = llm.requests[2]["messages"][-1]["content"]
    assert "11:00-17:00" in repair


async def test_an_unrepaired_closure_ships_as_a_warning_not_a_silent_pass(
    stub_search,
) -> None:
    """One repair round is the budget. What survives it must be surfaced, not hidden."""
    llm = FakeLLM(
        [
            completion(
                tool_calls=[tool_call("search_places", {"query": "art museum", "near": "Chicago"})]
            ),
            completion(content=itinerary_at("09:00", "12:00")),
            completion(content=itinerary_at("08:00", "10:00")),  # still shut
        ]
    )

    result = await plan_trip("art museums in Chicago", client=llm, model="test-model", today=TODAY)

    assert result.validation is not None and not result.validation.ok
    assert any(
        v.code == "outside_opening_hours" and "open 11:00-17:00" in v.message
        for v in result.validation.violations
    )
