"""Tests for telling the model how far apart its candidates are, before it schedules.

The failure this exists to prevent, seen live: a 14-minute walk scheduled with a
**0-minute** gap. The model was not careless -- it had no distance information at the
moment it chose the times, and the only thing that knew better was a Routes measurement
that runs afterwards and costs a whole repair generation to act on.
"""

import json

from app.agent.orchestrator import harvest_place_points, plan_trip
from app.agent.proximity import WALKABLE_MINUTES, haversine_km, render, walk_minutes
from app.tools.maps import Place, PlacesResult
from app.tools.registry import TOOL_FUNCTIONS
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

# Real coordinates, so the arithmetic is checkable against a map rather than itself.
ART_INSTITUTE = (41.8796, -87.6237)
MILLENNIUM_PARK = (41.8826, -87.6226)
WRIGLEY_FIELD = (41.9484, -87.6553)


def test_the_distance_matches_the_map() -> None:
    """Art Institute to Millennium Park is a few hundred metres; to Wrigley it is miles."""
    assert 0.2 < haversine_km(ART_INSTITUTE, MILLENNIUM_PARK) < 0.5
    assert 7.5 < haversine_km(ART_INSTITUTE, WRIGLEY_FIELD) < 8.5


def test_walking_time_allows_for_streets_not_crow_flight() -> None:
    """A straight line has no corners in it, so the estimate has to be longer than one."""
    straight_only = 1.0 / 4.5 * 60
    assert walk_minutes(1.0) > straight_only


def test_a_short_hop_reads_as_walkable() -> None:
    block = render({"Art Institute": ART_INSTITUTE, "Millennium Park": MILLENNIUM_PARK})

    assert block is not None
    assert "on foot" in block
    assert "too far to walk" not in block


def test_a_long_hop_says_so_rather_than_leaving_it_to_arithmetic() -> None:
    """The number alone is not the point -- the model has to draw the conclusion, and
    "8.1 km" is exactly the sort of thing that gets scheduled back to back anyway."""
    block = render({"Art Institute": ART_INSTITUTE, "Wrigley Field": WRIGLEY_FIELD})

    assert block is not None
    assert "too far to walk" in block
    assert walk_minutes(haversine_km(ART_INSTITUTE, WRIGLEY_FIELD)) > WALKABLE_MINUTES


def test_one_place_says_nothing() -> None:
    """Distance needs two points. A block with no pairs in it is noise in the prompt."""
    assert render({"Art Institute": ART_INSTITUTE}) is None
    assert render({}) is None


def test_the_estimate_does_not_pose_as_a_measurement() -> None:
    """It knows nothing about rivers or one-way systems. Routes remains the authority,
    and a model that mistakes this for the real number will trust it over the tool."""
    block = render({"a": ART_INSTITUTE, "b": WRIGLEY_FIELD})

    assert block is not None
    assert "estimates" in block
    assert "get_travel_time" in block


def test_truncation_is_announced() -> None:
    """A silently shortened list reads as "these are all the places you found"."""
    many = {f"venue {n}": (41.88 + n * 0.01, -87.62) for n in range(20)}
    block = render(many)

    assert block is not None
    assert "not listed" in block


# --- harvesting the coordinates ----------------------------------------------------


def reply(**place) -> str:
    return json.dumps({"ok": True, "query": "museums", "places": [place]})


def test_coordinates_are_kept_off_a_search_reply() -> None:
    kept: dict[str, tuple[float, float]] = {}
    harvest_place_points(
        "search_places", reply(name="Art Institute", latitude=41.8796, longitude=-87.6237), kept
    )

    assert kept == {"Art Institute": ART_INSTITUTE}


def test_a_place_without_coordinates_contributes_nothing() -> None:
    kept: dict[str, tuple[float, float]] = {}
    harvest_place_points("search_places", reply(name="Art Institute"), kept)
    harvest_place_points("search_places", reply(name="X", latitude=1.0), kept)
    harvest_place_points("search_places", reply(latitude=1.0, longitude=2.0), kept)

    assert kept == {}


def test_harvesting_never_raises_on_anything_it_is_handed() -> None:
    kept: dict[str, tuple[float, float]] = {}
    for payload in ("", "not json", "[]", '{"places": [null, 3]}', '{"places": [{}]}'):
        harvest_place_points("search_places", payload, kept)

    assert kept == {}


# --- end to end --------------------------------------------------------------------


async def test_the_model_is_told_the_distances_before_it_writes_the_schedule(monkeypatch) -> None:
    """The whole point: the block has to arrive in the turn that composes the itinerary,
    not afterwards. Sent later it is just an expensive way to say "I told you so"."""

    async def fake_search(query: str, near: str, **_) -> PlacesResult:
        return PlacesResult(
            ok=True,
            query=query,
            places=[
                Place(ok=True, name="Art Institute", latitude=41.8796, longitude=-87.6237),
                Place(ok=True, name="Wrigley Field", latitude=41.9484, longitude=-87.6553),
            ],
        )

    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", fake_search)
    llm = FakeLLM(
        [
            completion(
                tool_calls=[tool_call("search_places", {"query": "sights", "near": "Chicago"})]
            ),
            completion(content=ITINERARY_JSON),
        ]
    )

    await plan_trip("2 days in Chicago", client=llm, model="test-model")

    composing = llm.requests[1]["messages"]
    assert any("too far to walk" in (m.get("content") or "") for m in composing)


async def test_the_distance_block_is_not_repeated_every_round(monkeypatch) -> None:
    """It is O(n^2) in the prompt. Re-sending an unchanged table each tool round pays
    for the same paragraph several times over."""

    async def fake_search(query: str, near: str, **_) -> PlacesResult:
        return PlacesResult(
            ok=True,
            query=query,
            places=[
                Place(ok=True, name="Art Institute", latitude=41.8796, longitude=-87.6237),
                Place(ok=True, name="Wrigley Field", latitude=41.9484, longitude=-87.6553),
            ],
        )

    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", fake_search)
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("search_places", {"query": "a", "near": "Chicago"})]),
            completion(tool_calls=[tool_call("search_places", {"query": "b", "near": "Chicago"})]),
            completion(content=ITINERARY_JSON),
        ]
    )

    await plan_trip("2 days in Chicago", client=llm, model="test-model")

    final = llm.requests[-1]["messages"]
    blocks = [m for m in final if "too far to walk" in (m.get("content") or "")]
    assert len(blocks) == 1
