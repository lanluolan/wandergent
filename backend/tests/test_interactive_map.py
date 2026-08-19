"""The interactive map page and the geocoding that feeds it.

Offline: geocoding runs against an injected mock transport, so no Google Maps quota is
spent and the suite stays deterministic. The payload shape is the one the live
Geocoding API returns.
"""

import json

import httpx
import pytest

from app.config import settings
from app.map_page import day_map_page
from app.tools.maps import GeocodedPlace, geocode_places

GEOCODE_PAYLOAD = {
    "status": "OK",
    "results": [
        {
            "formatted_address": "1-1 Ōsakajō, Chuo Ward, Chicago, 540-0002, Japan",
            "geometry": {"location": {"lat": 34.6872571, "lng": 135.5258546}},
        }
    ],
}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_places_are_resolved_to_coordinates() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["address"])
        return httpx.Response(200, json=GEOCODE_PAYLOAD)

    async with _client(handler) as client:
        points = await geocode_places(["Art Institute of Chicago", "Fulton Market"], client=client)

    assert seen == ["Art Institute of Chicago", "Fulton Market"]
    assert all(point.ok for point in points)
    assert points[0].latitude == 34.6872571
    assert points[0].formatted.startswith("1-1")


async def test_one_unresolvable_stop_does_not_sink_the_rest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "nowhere" in request.url.params["address"]:
            return httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []})
        return httpx.Response(200, json=GEOCODE_PAYLOAD)

    async with _client(handler) as client:
        points = await geocode_places(["Art Institute of Chicago", "nowhere at all"], client=client)

    assert [point.ok for point in points] == [True, False]
    assert "ZERO_RESULTS" in points[1].error


async def test_geocoding_degrades_rather_than_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with _client(handler) as client:
        points = await geocode_places(["Art Institute of Chicago"], client=client)

    # Same contract as every other tool: a dead upstream is a result, not an exception.
    assert points[0].ok is False
    assert "unavailable" in points[0].error


def test_the_maps_key_cannot_reach_the_log() -> None:
    """Geocoding takes its key in the query string, and httpx logs request URLs.

    Observed live: `GET .../geocode/json?address=...&key=AIza...` written to the server
    log at INFO on every call. Places and Routes send the key as a header, so this is
    the one caller that can leak it -- and the fix belongs at the logger, not at the
    call site, so a future query-string secret is covered too.
    """
    import logging

    import app.main  # noqa: F401  -- importing is what applies the configuration

    assert logging.getLogger("httpx").level == logging.WARNING


def _stops(page: str) -> list[dict]:
    body = page.split("var STOPS = ", 1)[1].split(";\n", 1)[0]
    return json.loads(body)


def _point(query: str, lat: float, lng: float) -> GeocodedPlace:
    return GeocodedPlace(
        ok=True, query=query, latitude=lat, longitude=lng, formatted=f"{query}, Chicago"
    )


def test_the_page_carries_the_stops_and_the_key() -> None:
    page = day_map_page([_point("Art Institute of Chicago", 34.68, 135.52)], key="KEY-123")

    assert _stops(page) == [
        {
            "label": "1",
            # The itinerary's own words lead; the formatted address is the sub-line.
            "name": "Art Institute of Chicago",
            "address": "Art Institute of Chicago, Chicago",
            "lat": 34.68,
            "lng": 135.52,
        }
    ]
    assert "key=KEY-123" in page
    # Classic bootstrap and the conservative channel: the device's WebView is old.
    assert "callback=initMap" in page
    assert "v=quarterly" in page


def test_a_stop_that_did_not_resolve_keeps_the_others_numbered_as_the_day_is() -> None:
    points = [
        _point("Art Institute of Chicago", 34.68, 135.52),
        GeocodedPlace(ok=False, query="nowhere at all", error="no location"),
        _point("Willis Tower", 34.65, 135.50),
    ]

    stops = _stops(day_map_page(points, key="k"))

    # Marker 3 is the third activity of the day. Renumbering to 1,2 would put the map
    # and the timeline into disagreement about which stop is which.
    assert [stop["label"] for stop in stops] == ["1", "3"]
    assert [stop["name"] for stop in stops] == ["Art Institute of Chicago", "Willis Tower"]


def test_debug_is_off_unless_asked_for() -> None:
    assert "var DEBUG = false" in day_map_page([_point("a", 1.0, 2.0)], key="k")
    assert "var DEBUG = true" in day_map_page([_point("a", 1.0, 2.0)], key="k", debug=True)


def test_the_debug_panel_redacts_the_key_it_would_otherwise_echo() -> None:
    """A browser error quotes the failing script's URL -- which carries the key.

    Observed live: the on-screen diagnostics printed
    `Uncaught SyntaxError ... at https://maps.googleapis.com/maps/api/js?key=AIza...`,
    putting the key in plain sight and into any screenshot of a bug report.
    """
    page = day_map_page([_point("a", 1.0, 2.0)], key="k", debug=True)

    assert 'replace(/key=[^&\\s]+/g, "key=***")' in page


def test_a_place_name_cannot_break_out_of_the_script() -> None:
    # A venue really can be called something with a quote in it, and the stops go into
    # a <script> block: `</script>` inside a string would end the block early.
    nasty = _point('</script><script>alert("x")</script>', 1.0, 2.0)

    page = day_map_page([nasty], key="k")

    assert "</script><script>alert" not in page
    assert _stops(page)[0]["name"] == '</script><script>alert("x")</script>'
