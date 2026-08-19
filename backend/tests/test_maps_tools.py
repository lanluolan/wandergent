"""Maps tool tests.

Offline throughout: every case runs against an injected mock transport, so the suite
stays deterministic and spends no Google Maps quota. The payload shapes are the ones
observed while probing the live APIs with a real key, not invented.
"""

import json

import httpx
import pytest

from app.config import settings
from app.tools.maps import (
    PLACES_TOOL_SCHEMA,
    TRAVEL_TOOL_SCHEMA,
    get_travel_time,
    search_places,
)

PLACES_PAYLOAD = {
    "places": [
        {
            "displayName": {"text": "Kushikatsu Daruma", "languageCode": "en"},
            "formattedAddress": "2-3-9 Ebisuhigashi, Naniwa Ward, Chicago",
            "location": {"latitude": 34.6524, "longitude": 135.5063},
            "rating": 4.1,
            "userRatingCount": 5200,
            "priceLevel": "PRICE_LEVEL_MODERATE",
        },
        {
            "displayName": {"text": "Yaekatsu", "languageCode": "en"},
            "formattedAddress": "2-4-2 Ebisuhigashi, Naniwa Ward, Chicago",
            "location": {"latitude": 34.6521, "longitude": 135.5061},
            "rating": 3.9,
            "userRatingCount": 900,
        },
    ]
}

ROUTE_PAYLOAD = {"routes": [{"duration": "730s", "distanceMeters": 7570}]}


@pytest.fixture(autouse=True)
def _with_key(monkeypatch):
    """Most cases assume a configured server. The unconfigured case sets its own."""
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- search_places -----------------------------------------------------------------


async def test_finds_real_venues_with_the_detail_a_plan_needs() -> None:
    async with make_client(lambda _: httpx.Response(200, json=PLACES_PAYLOAD)) as client:
        result = await search_places("kushikatsu", "Chicago", client=client)

    assert result.ok
    first = result.places[0]
    assert first.name == "Kushikatsu Daruma"
    assert first.address.startswith("2-3-9")
    assert first.rating == 4.1
    assert first.rating_count == 5200
    assert (first.latitude, first.longitude) == (34.6524, 135.5063)


async def test_the_query_and_area_are_sent_as_one_text_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PLACES_PAYLOAD)

    async with make_client(handler) as client:
        await search_places("ramen", "Boston", client=client)

    assert b"ramen in Boston" in seen[0].content
    # The key travels in a header, never in the URL, so it cannot leak into a log line.
    assert seen[0].headers["X-Goog-Api-Key"] == "test-key"
    assert "test-key" not in str(seen[0].url)


async def test_limit_is_clamped_rather_than_trusted() -> None:
    """The model picks this number; a 500 would be an expensive way to say no."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PLACES_PAYLOAD)

    async with make_client(handler) as client:
        await search_places("sushi", "New York", limit=999, client=client)
        await search_places("sushi", "New York", limit=0, client=client)

    assert json.loads(seen[0].content)["maxResultCount"] == 8
    assert json.loads(seen[1].content)["maxResultCount"] == 4


async def test_language_is_sent_so_names_come_back_usable() -> None:
    """Without a language code a Chengdu search returns romanised names and addresses
    ("Xumei Tasty Shashlik" at "Luomashi, Jin Jiang Qu") that nobody can use on the
    ground. Observed live before this was added."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PLACES_PAYLOAD)

    async with make_client(handler) as client:
        await search_places("kushikatsu", "Chicago", client=client)
        await search_places("ramen", "Boston", language="ja", client=client)

    assert json.loads(seen[0].content)["languageCode"] == "en"
    assert json.loads(seen[1].content)["languageCode"] == "ja"


async def test_no_matches_is_a_fact_not_a_failure() -> None:
    """ "There is no such place here" is what the planner needs to hear; retrying will
    not change it, so `ok` stays true and the reason rides along."""
    async with make_client(lambda _: httpx.Response(200, json={})) as client:
        result = await search_places("igloo hotel", "Chicago", client=client)

    assert result.ok
    assert result.places == []
    assert "no places matched" in result.error


async def test_place_search_degrades_on_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    async with make_client(handler) as client:
        result = await search_places("cafe", "Boston", client=client)

    assert not result.ok
    assert "timed out" in result.error


async def test_place_search_degrades_on_http_error() -> None:
    async with make_client(lambda _: httpx.Response(403, text="denied")) as client:
        result = await search_places("cafe", "Boston", client=client)

    assert not result.ok
    assert "unavailable" in result.error


async def test_without_a_key_the_tool_says_so_and_makes_no_call(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_maps_api_key", "")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the network without a key")

    async with make_client(handler) as client:
        result = await search_places("cafe", "Boston", client=client)

    assert not result.ok
    assert "GOOGLE_MAPS_API_KEY" in result.error


# --- get_travel_time ---------------------------------------------------------------


async def test_travel_time_returns_seconds_and_metres() -> None:
    async with make_client(lambda _: httpx.Response(200, json=ROUTE_PAYLOAD)) as client:
        result = await get_travel_time("Chicago Station", "Willis Tower", "DRIVE", client=client)

    assert result.ok
    assert result.seconds == 730
    assert result.meters == 7570
    assert result.mode == "DRIVE"


async def test_mode_defaults_to_walking_and_is_case_insensitive() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ROUTE_PAYLOAD)

    async with make_client(handler) as client:
        await get_travel_time("A", "B", client=client)
        await get_travel_time("A", "B", "drive", client=client)

    assert json.loads(seen[0].content)["travelMode"] == "WALK"
    assert json.loads(seen[1].content)["travelMode"] == "DRIVE"


async def test_transit_is_supported_and_sends_a_departure_time() -> None:
    """Transit was refused for years on a wrong conclusion; see the module docstring.

    The departure time is not optional garnish: without one the API answers for "now",
    which is the wrong question for a future trip and returns nothing overnight.
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"routes": [{"duration": "930s", "distanceMeters": 4200}]})

    async with make_client(handler) as client:
        result = await get_travel_time("A", "B", "TRANSIT", client=client)

    assert result.ok
    assert result.seconds == 930
    assert seen[0]["travelMode"] == "TRANSIT"
    assert seen[0]["departureTime"].endswith("Z")


async def test_only_transit_carries_a_departure_time() -> None:
    """Walking is not timetabled, so asking for a moment in time buys nothing."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"routes": [{"duration": "600s"}]})

    async with make_client(handler) as client:
        await get_travel_time("A", "B", "WALK", client=client)

    assert "departureTime" not in seen[0]


async def test_a_country_without_transit_data_degrades_rather_than_erroring() -> None:
    """Japan answers 200 with no routes. That is "not here", not a failure.

    Pinned because the whole fallback depends on it: if this ever became `ok=False`
    with an error, `confirm_transfers` would treat a normal regional gap as an outage.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with make_client(handler) as client:
        result = await get_travel_time(
            "Art Institute of Chicago", "Willis Tower", "TRANSIT", client=client
        )

    assert not result.ok
    assert result.seconds is None


async def test_an_empty_route_list_degrades_instead_of_inventing_a_number() -> None:
    """Observed live: HTTP 200 with an empty body when no route of that mode exists."""
    async with make_client(lambda _: httpx.Response(200, json={})) as client:
        result = await get_travel_time("Chicago", "Honolulu", "DRIVE", client=client)

    assert not result.ok
    assert result.seconds is None
    assert "no drive route" in result.error


async def test_travel_time_degrades_on_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    async with make_client(handler) as client:
        result = await get_travel_time("A", "B", client=client)

    assert not result.ok
    assert "timed out" in result.error


# --- schemas ------------------------------------------------------------------------


def test_schemas_declare_what_the_registry_dispatches_on() -> None:
    assert PLACES_TOOL_SCHEMA["function"]["name"] == "search_places"
    assert TRAVEL_TOOL_SCHEMA["function"]["name"] == "get_travel_time"
    assert TRAVEL_TOOL_SCHEMA["function"]["parameters"]["properties"]["mode"]["enum"] == [
        "WALK",
        "DRIVE",
        "TRANSIT",
    ]


async def test_permanently_closed_venues_never_reach_the_planner() -> None:
    """Filtered in code, not asked for in the prompt.

    Google keeps closed places in results because they are still real places. For
    planning they are traps, and a rule in the prompt is a request the model can
    overlook -- dropping them is a guarantee it cannot.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "places": [
                    {
                        "displayName": {"text": "Gone For Good Diner"},
                        "businessStatus": "CLOSED_PERMANENTLY",
                    },
                    {
                        "displayName": {"text": "Still Open Diner"},
                        "businessStatus": "OPERATIONAL",
                    },
                ]
            },
        )

    async with make_client(handler) as client:
        result = await search_places("diner", "Chicago", client=client)

    assert [place.name for place in result.places] == ["Still Open Diner"]


async def test_all_matches_closed_is_a_fact_not_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "places": [
                    {"displayName": {"text": "Gone"}, "businessStatus": "CLOSED_PERMANENTLY"}
                ]
            },
        )

    async with make_client(handler) as client:
        result = await search_places("diner", "Chicago", client=client)

    # Same shape as "no matches": the planner needs to hear it and retrying will not help.
    assert result.ok
    assert result.places == []
    assert "closed permanently" in result.error


async def test_opening_hours_reach_the_planner() -> None:
    """The largest thing the agent could not previously know."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "places": [
                    {
                        "displayName": {"text": "Art Institute of Chicago"},
                        "businessStatus": "OPERATIONAL",
                        "regularOpeningHours": {
                            "weekdayDescriptions": [
                                "Monday: Closed",
                                "Tuesday: 11:00 AM - 5:00 PM",
                            ]
                        },
                    }
                ]
            },
        )

    async with make_client(handler) as client:
        result = await search_places("museum", "Chicago", client=client)

    assert result.places[0].opening_hours == [
        "Monday: Closed",
        "Tuesday: 11:00 AM - 5:00 PM",
    ]


async def test_a_place_with_no_published_hours_is_still_usable() -> None:
    """Parks and viewpoints often have none; that must not blank the whole result."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [{"displayName": {"text": "Millennium Park"}}]})

    async with make_client(handler) as client:
        result = await search_places("park", "Chicago", client=client)

    assert result.places[0].name == "Millennium Park"
    assert result.places[0].opening_hours == []


# --- which instant a route is priced for ------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from app.tools.maps import TRANSIT_REFERENCE_HOUR_UTC, _departure_iso, _route_body  # noqa: E402


def test_a_caller_supplied_departure_is_the_one_used() -> None:
    wanted = datetime.now(UTC) + timedelta(days=30)
    assert _departure_iso(wanted).startswith(wanted.strftime("%Y-%m-%dT%H:%M"))


def test_a_past_departure_falls_back_instead_of_being_sent() -> None:
    """The Routes API errors on a past time for driving and returns nothing for transit,
    which would read as an unroutable pair rather than as the bad input it is."""
    stale = datetime.now(UTC) - timedelta(days=1)
    used = datetime.fromisoformat(_departure_iso(stale).replace("Z", "+00:00"))

    assert used > datetime.now(UTC)
    assert used.hour == TRANSIT_REFERENCE_HOUR_UTC


def test_a_naive_departure_is_read_as_utc_not_rejected() -> None:
    wanted = (datetime.now(UTC) + timedelta(days=30)).replace(tzinfo=None)
    assert _departure_iso(wanted).startswith(wanted.strftime("%Y-%m-%dT%H:%M"))


def test_walking_is_never_given_a_departure_time() -> None:
    """Pedestrians do not sit in traffic, and the field is meaningless for WALK."""
    assert "departureTime" not in _route_body("A", "B", "WALK")
    assert "departureTime" in _route_body("A", "B", "TRANSIT")
    assert "departureTime" in _route_body("A", "B", "DRIVE")
