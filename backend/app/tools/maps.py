"""Maps tools, backed by Google Maps Platform.

Two capabilities, deliberately separate:

- `search_places` turns "a deep-dish place near Millennium Park" into venues that
  **exist**, with an address and a rating. Without it every venue comes from model memory:
  sometimes right, never checked, and specificity without a source is confidently wrong.
- `travel_time` gives real durations, which is what the constraint layer needs to stop
  guessing at "is there time to get there".

Modes are WALK, DRIVE and TRANSIT. **An empty transit result means "no coverage here",
never an error** -- Google carries transit only where it has the local operator's data, so
callers fall back to the other modes. Reading one region's gap as a global limitation is
what made the constraint layer measure city hops as walks for months (docs/decisions.md,
2026-08-17).

The key is server-side only: the Android client never talks to Google, so nothing ships in
the APK and no Play services are needed on device.
"""

import asyncio
import logging
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

import httpx

from app.config import settings
from app.tools.base import (
    BAD_REQUEST,
    NO_COVERAGE,
    NO_MATCH,
    NOT_CONFIGURED,
    TIMED_OUT,
    UNAVAILABLE,
    ToolOutcome,
    http_failure_code,
)

logger = logging.getLogger(__name__)

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
STATIC_MAP_URL = "https://maps.googleapis.com/maps/api/staticmap"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
TIMEZONE_URL = "https://maps.googleapis.com/maps/api/timezone/json"

# Static Maps geocodes marker strings itself, so a day can be drawn from the place
# names already in the itinerary -- no coordinates to store and no extra Geocoding
# calls. Labels are one character each, hence 1-9 then letters.
MARKER_LABELS = "123456789ABCDEFGHIJ"
MAX_MAP_PLACES = len(MARKER_LABELS)
MAX_MAP_EDGE = 640
ROUTE_COLOUR = "0x00696ecc"

# Every field costs: the Places API bills by which tier of fields you ask for. This is
# the smallest set that lets the agent name a real venue and judge whether it fits.
PLACES_FIELD_MASK = ",".join(
    (
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.priceLevel",
        # Without hours a plan can schedule a museum on the day it is shut and nothing
        # downstream notices -- budget, timing and routing checks all pass at a locked
        # door. `businessStatus` is the same gap worse: a venue closed for good still
        # reads as a fine recommendation.
        "places.regularOpeningHours",
        "places.businessStatus",
    )
)
ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters"

MAX_PLACES = 8
DEFAULT_PLACES = 4

# Fallback departure reference, used only when the caller cannot say when the traveller
# sets off. Far enough ahead that timetables are published, and fixed rather than "now" so
# an unanchored lookup always measures the same.
#
# One UTC hour cannot be mid-morning everywhere -- 11:00 UTC is noon in London and 05:00 in
# Chicago, which lands on empty roads and a thin timetable, flattering both numbers. Hence
# `depart_at`: `transfers.py` resolves the destination's real offset and asks about the
# hour on the plan. This constant is the lower bound left when that fails.
TRANSIT_REFERENCE_DAYS = 2
TRANSIT_REFERENCE_HOUR_UTC = 11

# A departure this close to now is treated as unusable: the Routes API rejects times in
# the past outright, and clock skew between us and Google makes "barely future" a coin
# flip. Anything nearer than this falls back to the reference above.
MIN_DEPARTURE_LEAD = timedelta(minutes=10)

# The language venue names and addresses come back in. English by default, matching the
# app. A *parameter* rather than a constant: the model knows what language the traveller
# wrote in, and an address to show a taxi driver is more useful in the local script.
DEFAULT_LANGUAGE = "en"

TravelMode = Literal["WALK", "DRIVE", "TRANSIT"]

MISSING_KEY = (
    "maps are not configured on this server (no GOOGLE_MAPS_API_KEY); "
    "plan without verified venues and say so in the notes"
)


class Place(ToolOutcome):
    """One venue that actually exists, as far as Google knows."""

    name: str
    address: str | None = None
    #: Per-weekday opening text as Google renders it, e.g. "Monday: Closed". Empty when
    #: Google has no hours for the place, which is common for parks and viewpoints.
    opening_hours: list[str] = []
    latitude: float | None = None
    longitude: float | None = None
    rating: float | None = None
    rating_count: int | None = None
    price_level: str | None = None


class PlacesResult(ToolOutcome):
    """Search result. `places` may be empty even when `ok` is True."""

    query: str
    places: list[Place] = []


class TravelTime(ToolOutcome):
    """How long it actually takes to get from one place to another."""

    origin: str
    destination: str
    mode: str
    seconds: int | None = None
    meters: int | None = None


PLACES_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_places",
        "description": (
            "Find real, existing venues -- restaurants, hotels, museums, shops -- with their "
            "address and rating. Use this before naming any specific place in the itinerary, "
            "so the plan cites a place that exists rather than one that sounds plausible. "
            "Also use it to pick between options: the rating and review count say which is "
            "worth the trip."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for, e.g. 'kushikatsu restaurant', 'hotel near the "
                        "station', 'museum'."
                    ),
                },
                "near": {
                    "type": "string",
                    "description": "City or area to search in, e.g. 'Chicago' or 'Chicago Loop'.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many results to return, 1-{MAX_PLACES}.",
                },
                "language": {
                    "type": "string",
                    "description": (
                        "BCP-47 code for the returned names and addresses, e.g. 'en', "
                        f"'ja', 'zh-CN'. Use the language you are writing the plan in. "
                        f"Defaults to '{DEFAULT_LANGUAGE}'."
                    ),
                },
                "purpose": {
                    "type": "string",
                    "enum": ["required", "optional"],
                    "description": (
                        "required when results will supply a venue intended for the "
                        "itinerary; optional only for extra alternatives. Optional searches "
                        "are dropped first when the research budget is tight."
                    ),
                },
            },
            "required": ["query", "near"],
            "additionalProperties": False,
        },
    },
}

TRAVEL_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_travel_time",
        "description": (
            "Real travel time between two places, at the hour people actually travel. "
            # Not "use it when the gap looks tight", which is circular -- the measurement
            # is how you learn the gap is tight. That wording produced live plans with a
            # 14-minute walk scheduled into a 0-minute gap.
            "Call it before you commit to a schedule, for any two consecutive activities "
            "in different places -- not only when a gap already looks wrong. The straight-"
            "line distances you were given are estimates; this is the real number. "
            "TRANSIT is how people actually cross a city and is the best default there; "
            "WALK for short hops, DRIVE for longer ones or where transit is thin. Transit "
            "data is unavailable in some countries (notably Japan) and comes back with no "
            "route -- use WALK or DRIVE there."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "Where you start, as an address or place name.",
                },
                "destination": {
                    "type": "string",
                    "description": "Where you are going, as an address or place name.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["WALK", "DRIVE", "TRANSIT"],
                    "description": "Travel mode. Defaults to WALK.",
                },
            },
            "required": ["origin", "destination"],
            "additionalProperties": False,
        },
    },
}


def _headers(field_mask: str) -> dict[str, str]:
    return {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": field_mask,
        "Content-Type": "application/json",
    }


def _to_place(raw: dict[str, Any]) -> Place:
    location = raw.get("location") or {}
    hours = (raw.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
    return Place(
        name=(raw.get("displayName") or {}).get("text") or "unknown",
        address=raw.get("formattedAddress"),
        opening_hours=list(hours),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        rating=raw.get("rating"),
        rating_count=raw.get("userRatingCount"),
        price_level=raw.get("priceLevel"),
    )


def _parse_duration(value: str | None) -> int | None:
    """Routes returns an ISO-ish duration string like '730s'."""
    if not value or not value.endswith("s"):
        return None
    try:
        return int(float(value[:-1]))
    except ValueError:
        return None


async def search_places(
    query: str,
    near: str,
    limit: int = DEFAULT_PLACES,
    language: str = DEFAULT_LANGUAGE,
    purpose: str = "required",
    *,
    client: httpx.AsyncClient | None = None,
) -> PlacesResult:
    """Find venues matching `query` in `near`. Never raises; degrades to ok=False."""
    # Planning metadata, not part of Google's query. The orchestrator uses it to keep
    # itinerary facts ahead of extra alternatives when the call budget is tight.
    if purpose not in ("required", "optional"):
        return PlacesResult(
            ok=False,
            query=query,
            error="purpose must be required or optional",
            code=BAD_REQUEST,
        )
    if not settings.google_maps_api_key:
        return PlacesResult(ok=False, query=query, error=MISSING_KEY, code=NOT_CONFIGURED)

    if client is not None:
        return await _search(client, query, near, limit, language)
    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _search(owned, query, near, limit, language)


async def _search(
    client: httpx.AsyncClient, query: str, near: str, limit: int, language: str
) -> PlacesResult:
    text_query = f"{query} in {near}".strip()
    try:
        count = max(1, min(int(limit or DEFAULT_PLACES), MAX_PLACES))
    except (TypeError, ValueError):
        return PlacesResult(
            ok=False,
            query=text_query,
            error=f"limit must be an integer from 1 to {MAX_PLACES}",
            code=BAD_REQUEST,
        )

    try:
        response = await client.post(
            PLACES_SEARCH_URL,
            headers=_headers(PLACES_FIELD_MASK),
            json={
                "textQuery": text_query,
                "maxResultCount": count,
                "languageCode": language or DEFAULT_LANGUAGE,
            },
        )
        response.raise_for_status()
        found = response.json().get("places") or []
    except httpx.TimeoutException:
        logger.warning("places search timed out for %r", text_query)
        return PlacesResult(
            ok=False, query=text_query, error="place search timed out", code=TIMED_OUT
        )
    except httpx.HTTPError as exc:
        logger.warning("places search failed for %r: %s", text_query, exc)
        return PlacesResult(
            ok=False,
            query=text_query,
            error=f"place search unavailable: {exc}",
            code=http_failure_code(exc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("unexpected places payload for %r: %s", text_query, exc)
        return PlacesResult(
            ok=False,
            query=text_query,
            error=f"unexpected response from place search: {exc}",
            code=UNAVAILABLE,
        )

    if not found:
        # A fact about the world, not a failure: "there is no such place here" is what the
        # planner needs to hear, and retrying will not change it.
        return PlacesResult(
            ok=True,
            query=text_query,
            places=[],
            error=f"no places matched {text_query!r}; try a broader query",
            code=NO_MATCH,
        )

    # Dropped here rather than described to the model: a permanently closed venue is never
    # the right answer, and a prompt rule is a request the model can overlook. Google keeps
    # these because they are still real places; for planning they are traps.
    open_for_business = [
        item for item in found if item.get("businessStatus") != "CLOSED_PERMANENTLY"
    ]
    dropped = len(found) - len(open_for_business)
    if dropped:
        logger.info("dropped %s permanently closed venue(s) from %r", dropped, text_query)
    if not open_for_business:
        return PlacesResult(
            ok=True,
            query=text_query,
            places=[],
            error=f"every match for {text_query!r} has closed permanently; try another query",
            code=NO_MATCH,
        )

    return PlacesResult(
        ok=True, query=text_query, places=[_to_place(item) for item in open_for_business]
    )


class StaticMap(ToolOutcome):
    """A rendered day map. `image` is PNG bytes when ok."""

    image: bytes | None = None
    places: list[str] = []


def _for_comparison(place: str) -> str:
    """A place string reduced to what two spellings of one address have in common."""
    return " ".join(re.sub(r"[^\w\s]", " ", place.casefold()).split())


def map_stops(places: Iterable[str]) -> list[str]:
    """The route to draw: trimmed, blanks dropped, repeats-in-a-row collapsed, capped.

    A day naming the same place twice in a row -- "lunch here, then coffee here" -- would
    otherwise put two markers on one point, the second hidden under the first with its
    number lost.

    Stops also collapse when one spelling contains the other, since a plan writes the same
    place two ways in one breath: `30 Rockefeller Plaza, New York, NY 10112` then
    `Rockefeller Plaza`. The longer spelling wins -- it is the one Google can put on a
    doorstep.

    Only *consecutive* stops, and only by name: pins that merely land near each other
    would need every stop geocoded here to tell apart.
    """
    stops: list[str] = []
    for place in places:
        cleaned = place.strip() if place else ""
        if not cleaned:
            continue
        if stops:
            previous, current = _for_comparison(stops[-1]), _for_comparison(cleaned)
            if previous in current or current in previous:
                # Keep whichever spelling says more about where the door is.
                if len(cleaned) > len(stops[-1]):
                    stops[-1] = cleaned
                continue
        stops.append(cleaned)
    return stops[:MAX_MAP_PLACES]


def static_map_params(places: list[str], width: int, height: int) -> list[tuple[str, str]]:
    """Query parameters for one day's map: numbered stops joined by a route line.

    A list of pairs, not a dict: `markers` repeats once per stop and a dict would keep
    only the last one.

    One marker per *distinct* place, but the line follows the day as given: a hotel at
    both ends of the day is one pin and a loop, not two pins in the same spot.
    """
    size = f"{min(max(width, 64), MAX_MAP_EDGE)}x{min(max(height, 64), MAX_MAP_EDGE)}"
    params: list[tuple[str, str]] = [("size", size), ("scale", "2")]
    distinct = list(dict.fromkeys(place.casefold() for place in places))
    for label, folded in zip(MARKER_LABELS, distinct, strict=False):
        place = next(item for item in places if item.casefold() == folded)
        params.append(("markers", f"color:0x00696e|label:{label}|{place}"))
    if len(places) > 1:
        # The line is what turns pins into an itinerary. Straight segments, not road
        # geometry: this is orientation, and polylines would cost a Routes call per hop.
        params.append(
            ("path", "|".join([f"color:{ROUTE_COLOUR}", "weight:4", *places[:MAX_MAP_PLACES]]))
        )
    return params


async def render_day_map(
    places: list[str],
    width: int = 640,
    height: int = 400,
    *,
    client: httpx.AsyncClient | None = None,
) -> StaticMap:
    """Draw a day's stops as a PNG. Never raises; degrades to ok=False.

    The key stays here: the Android client asks this backend for the image, so nothing
    ships in the APK and the app works on devices with no Google Play services.
    """
    usable = map_stops(places)
    if not usable:
        return StaticMap(ok=False, error="no places with a location to draw", code=NO_MATCH)
    if not settings.google_maps_api_key:
        return StaticMap(ok=False, places=usable, error=MISSING_KEY, code=NOT_CONFIGURED)

    if client is not None:
        return await _render(client, usable, width, height)
    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _render(owned, usable, width, height)


async def _render(
    client: httpx.AsyncClient, places: list[str], width: int, height: int
) -> StaticMap:
    params = [*static_map_params(places, width, height), ("key", settings.google_maps_api_key)]
    try:
        response = await client.get(STATIC_MAP_URL, params=params)
        response.raise_for_status()
    except httpx.TimeoutException:
        return StaticMap(ok=False, places=places, error="map service timed out", code=TIMED_OUT)
    except httpx.HTTPError as exc:
        logger.warning("static map failed for %s: %s", places, exc)
        return StaticMap(
            ok=False,
            places=places,
            error=f"map service unavailable: {exc}",
            code=http_failure_code(exc),
        )

    if not response.headers.get("content-type", "").startswith("image"):
        # Static Maps answers a bad request with a text body and a 200, so the content
        # type is the only reliable signal that an image actually came back.
        return StaticMap(
            ok=False,
            places=places,
            error=f"map service returned {response.text[:120]!r}",
            code=UNAVAILABLE,
        )
    return StaticMap(ok=True, places=places, image=response.content)


class GeocodedPlace(ToolOutcome):
    """A place string resolved to a point on the earth."""

    query: str
    latitude: float | None = None
    longitude: float | None = None
    formatted: str | None = None


async def geocode_places(
    places: list[str],
    language: str = DEFAULT_LANGUAGE,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[GeocodedPlace]:
    """Resolve place strings to coordinates, in order. Never raises.

    Static Maps geocodes marker strings for us; the JavaScript API only plots points.
    Doing it here keeps Geocoding on the server key, so the browser's key can be
    restricted to map rendering alone.

    Failures come back as `ok=False` entries rather than being dropped, so the caller can
    still number the stops the way the itinerary does.
    """
    usable = map_stops(places)
    if not usable:
        return []
    if not settings.google_maps_api_key:
        return [
            GeocodedPlace(ok=False, query=place, error=MISSING_KEY, code=NOT_CONFIGURED)
            for place in usable
        ]

    if client is not None:
        return await _geocode_all(client, usable, language)
    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _geocode_all(owned, usable, language)


async def _geocode_all(
    client: httpx.AsyncClient, places: list[str], language: str
) -> list[GeocodedPlace]:
    # Concurrently: a day has up to 19 stops, and in series they would spend the whole
    # tool timeout budget one request at a time.
    return list(await asyncio.gather(*(_geocode_one(client, place, language) for place in places)))


async def _geocode_one(client: httpx.AsyncClient, place: str, language: str) -> GeocodedPlace:
    try:
        response = await client.get(
            GEOCODE_URL,
            params={
                "address": place,
                "language": language or DEFAULT_LANGUAGE,
                "key": settings.google_maps_api_key,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException:
        return GeocodedPlace(ok=False, query=place, error="geocoding timed out", code=TIMED_OUT)
    except httpx.HTTPError as exc:
        logger.warning("geocoding failed for %r: %s", place, exc)
        return GeocodedPlace(
            ok=False, query=place, error=f"geocoding unavailable: {exc}", code=UNAVAILABLE
        )

    results = payload.get("results") or []
    if not results:
        # ZERO_RESULTS is a fact, not an outage: this address does not resolve.
        return GeocodedPlace(
            ok=False,
            query=place,
            error=f"no location for {place!r} ({payload.get('status')})",
            code=NO_MATCH,
        )

    location = results[0].get("geometry", {}).get("location", {})
    latitude, longitude = location.get("lat"), location.get("lng")
    if latitude is None or longitude is None:
        return GeocodedPlace(
            ok=False, query=place, error="geocoding returned no coordinates", code=NO_MATCH
        )
    return GeocodedPlace(
        ok=True,
        query=place,
        latitude=float(latitude),
        longitude=float(longitude),
        formatted=results[0].get("formatted_address"),
    )


async def local_utc_offset(
    place: str,
    on: date | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> timedelta | None:
    """The destination's offset from UTC, or None if it cannot be established.

    Two calls -- geocode the place, then ask the Time Zone API about that point -- so
    callers resolve it *once per plan* and reuse it.

    `on` is the trip date, and it matters: the offset is a function of the instant, so a
    summer trip measured with a winter offset is an hour out.

    None rather than a guess on every failure path. A wrong offset silently moves every
    measurement to the wrong hour while looking fixed; None falls back to a reference
    documented as optimistic.
    """
    if not settings.google_maps_api_key:
        return None

    if client is not None:
        return await _timezone_for(client, place, on)
    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _timezone_for(owned, place, on)


async def _timezone_for(client: httpx.AsyncClient, place: str, on: date | None) -> timedelta | None:
    located = await _geocode_one(client, place, DEFAULT_LANGUAGE)
    if not located.ok or located.latitude is None or located.longitude is None:
        logger.info("no timezone for %r: %s", place, located.error)
        return None

    when = datetime.combine(on, time(12, 0), tzinfo=UTC) if on else datetime.now(UTC)
    try:
        response = await client.get(
            TIMEZONE_URL,
            params={
                "location": f"{located.latitude},{located.longitude}",
                "timestamp": int(when.timestamp()),
                "key": settings.google_maps_api_key,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("timezone lookup failed for %r: %s", place, exc)
        return None

    if payload.get("status") != "OK":
        logger.info("timezone lookup for %r returned %s", place, payload.get("status"))
        return None

    raw, dst = payload.get("rawOffset"), payload.get("dstOffset")
    if raw is None or dst is None:
        return None
    return timedelta(seconds=int(raw) + int(dst))


async def get_travel_time(
    origin: str,
    destination: str,
    mode: str = "WALK",
    *,
    depart_at: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> TravelTime:
    """Real travel time between two places. Never raises; degrades to ok=False.

    `depart_at` is the instant the traveller sets off, in UTC. It changes the answer a
    lot for transit and driving -- rush hour against a Sunday morning -- so callers that
    know the hour on the plan should say so. Omitting it measures a generic future
    weekday instead, which flatters the plan.
    """
    if not settings.google_maps_api_key:
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=mode,
            error=MISSING_KEY,
            code=NOT_CONFIGURED,
        )

    chosen = (mode or "WALK").upper()
    if chosen not in ("WALK", "DRIVE", "TRANSIT"):
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=chosen,
            error=f"mode must be WALK, DRIVE or TRANSIT, got {mode!r}",
            code=BAD_REQUEST,
        )

    if client is not None:
        return await _route(client, origin, destination, chosen, depart_at)
    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _route(owned, origin, destination, chosen, depart_at)


def _route_body(
    origin: str, destination: str, mode: str, depart_at: datetime | None = None
) -> dict[str, Any]:
    """Request body for one route lookup.

    Transit and traffic-aware driving both need a departure time -- without one the API
    answers for "now", which is the wrong answer for a trip and, for transit, is empty
    overnight when nothing is running. See `_departure_iso` for which instant is used.
    """
    body: dict[str, Any] = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": mode,
    }
    if mode == "DRIVE":
        # Without this the API answers with free-flow times -- a crosstown hop at 13
        # minutes, true at 4am and nowhere near true when anyone travels. Measuring
        # exists to beat the model's optimism, not to launder it.
        body["routingPreference"] = "TRAFFIC_AWARE"
    if mode in ("TRANSIT", "DRIVE"):
        body["departureTime"] = _departure_iso(depart_at)
    return body


def _departure_iso(depart_at: datetime | None) -> str:
    """The instant to measure for, as the API wants it written.

    Prefers what the caller asked for. Rejects anything in the past or nearly so, rather
    than passing it on: the API answers a past `departureTime` with an error for driving
    and with nothing at all for transit, and a failed lookup reads exactly like an
    unreachable destination.
    """
    now = datetime.now(UTC)
    if depart_at is not None:
        moment = depart_at if depart_at.tzinfo else depart_at.replace(tzinfo=UTC)
        if moment - now >= MIN_DEPARTURE_LEAD:
            return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")

    reference = now + timedelta(days=TRANSIT_REFERENCE_DAYS)
    while reference.weekday() >= 5:  # Saturday or Sunday: thinner timetables.
        reference += timedelta(days=1)
    departure = reference.replace(
        hour=TRANSIT_REFERENCE_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    return departure.isoformat().replace("+00:00", "Z")


async def _route(
    client: httpx.AsyncClient,
    origin: str,
    destination: str,
    mode: str,
    depart_at: datetime | None = None,
) -> TravelTime:
    try:
        response = await client.post(
            ROUTES_URL,
            headers=_headers(ROUTES_FIELD_MASK),
            json=_route_body(origin, destination, mode, depart_at),
        )
        response.raise_for_status()
        routes = response.json().get("routes") or []
    except httpx.TimeoutException:
        logger.warning("route lookup timed out for %s -> %s", origin, destination)
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=mode,
            error="route service timed out",
            code=TIMED_OUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("route lookup failed for %s -> %s: %s", origin, destination, exc)
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=mode,
            error=f"route service unavailable: {exc}",
            code=http_failure_code(exc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("unexpected route payload for %s -> %s: %s", origin, destination, exc)
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=mode,
            error=f"unexpected response from route service: {exc}",
            code=UNAVAILABLE,
        )

    if not routes:
        # Seen in probing: a 200 with an empty body when no route of that mode exists
        # between the two points. Degrade rather than invent a number.
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=mode,
            error=f"no {mode.lower()} route found between these places",
            code=NO_COVERAGE,
        )

    route = routes[0]
    return TravelTime(
        ok=True,
        origin=origin,
        destination=destination,
        mode=mode,
        seconds=_parse_duration(route.get("duration")),
        meters=route.get("distanceMeters"),
    )
