"""Maps tools, backed by Google Maps Platform.

Two capabilities, deliberately separate:

- `search_places` turns "a kushikatsu place in Millennium Park" into venues that **exist**,
  with an address and a rating. Until this landed, every restaurant and hotel in a plan
  was generated from model memory: sometimes right, never checked. Specificity without
  a source makes a model *confidently* wrong, which is worse than vague.
- `travel_time` gives real durations between two places, which is what the constraint
  layer needs to stop guessing at "is there time to get there".

Modes are WALK, DRIVE and TRANSIT. **The earlier note here said transit "returned no
route even with a departure time" and was wrong** -- it works everywhere we tested
except Japan, which has no Google transit coverage through this API. The original probe
happened to use Chicago, so one regional gap was recorded as a global limitation and the
constraint layer spent months measuring city hops as walks. Verified 2026-08-17: transit
routes returned for New York, San Francisco, Chicago, Boston, Seattle, New Orleans,
Austin, Seoul, Bangkok, Singapore, Lisbon, Istanbul and Paris; empty for Chicago, Boston
and New York. Callers must therefore treat an empty transit result as "not here" and fall
back, never as an error.

The key is server-side only. The Android client never talks to Google -- it receives
what this backend produces -- so nothing ships in the APK and no Google Play services
are needed on device.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

import httpx

from app.config import settings
from app.tools.base import ToolOutcome

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
        # Opening hours were the largest thing the planner could not know. Without them
        # a plan can schedule a museum on the day it is shut and nothing downstream can
        # tell -- the constraint layer checks budget, timing and routing, none of which
        # notice a locked door. `businessStatus` is the same gap one step worse: a venue
        # that closed for good still reads as a perfectly good recommendation.
        "places.regularOpeningHours",
        "places.businessStatus",
    )
)
ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters"

MAX_PLACES = 8
DEFAULT_PLACES = 4

# Fallback departure reference, used only when the caller cannot say when the traveller
# actually sets off. Far enough ahead that timetables are published, and fixed rather
# than "now" so an unanchored lookup always measures the same.
#
# A single UTC hour cannot be mid-morning everywhere: 11:00 UTC is noon in London,
# 05:00 in Chicago and 20:00 in Seoul. For the Americas that lands on empty roads and a
# thin timetable, so both numbers come back at their most flattering -- a Chicago
# crosstown hop measured 10 minutes by car, which no one would experience at a time they
# would actually travel. That is why `depart_at` exists: `transfers.py` resolves the
# destination's real UTC offset and asks about the hour on the plan. This constant is
# what is left when that resolution fails, and it is still a lower bound.
TRANSIT_REFERENCE_DAYS = 2
TRANSIT_REFERENCE_HOUR_UTC = 11

# A departure this close to now is treated as unusable: the Routes API rejects times in
# the past outright, and clock skew between us and Google makes "barely future" a coin
# flip. Anything nearer than this falls back to the reference above.
MIN_DEPARTURE_LEAD = timedelta(minutes=10)

# The language venue names and addresses come back in. English by default, matching
# the app: a plan that reads in English but names every restaurant in Japanese is worse
# than one that does neither.
#
# It stays a *parameter* rather than a constant because the model knows what language
# the traveller wrote in, and the right answer is not always the interface language --
# an address you have to show a taxi driver is more useful in the local script.
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
            # The previous wording said "use it when the gap looks tight", which is
            # circular: knowing the gap is tight is what the measurement is for, so the
            # tool was only reached for once the model had already spotted the problem.
            # Live plans then scheduled a 14-minute walk with a 0-minute gap.
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
    *,
    client: httpx.AsyncClient | None = None,
) -> PlacesResult:
    """Find venues matching `query` in `near`. Never raises; degrades to ok=False."""
    if not settings.google_maps_api_key:
        return PlacesResult(ok=False, query=query, error=MISSING_KEY)

    if client is not None:
        return await _search(client, query, near, limit, language)
    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _search(owned, query, near, limit, language)


async def _search(
    client: httpx.AsyncClient, query: str, near: str, limit: int, language: str
) -> PlacesResult:
    text_query = f"{query} in {near}".strip()
    count = max(1, min(int(limit or DEFAULT_PLACES), MAX_PLACES))

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
        return PlacesResult(ok=False, query=text_query, error="place search timed out")
    except httpx.HTTPError as exc:
        logger.warning("places search failed for %r: %s", text_query, exc)
        return PlacesResult(ok=False, query=text_query, error=f"place search unavailable: {exc}")
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("unexpected places payload for %r: %s", text_query, exc)
        return PlacesResult(
            ok=False, query=text_query, error=f"unexpected response from place search: {exc}"
        )

    if not found:
        # An empty result is a fact about the world, not a failure: "there is no such
        # place here" is exactly what the planner needs to hear, and retrying will not
        # change it.
        return PlacesResult(
            ok=True,
            query=text_query,
            places=[],
            error=f"no places matched {text_query!r}; try a broader query",
        )

    # Dropped here rather than described to the model. A permanently closed venue is
    # never the right answer, and a rule in the prompt is a request the model can
    # overlook -- filtering is a guarantee it cannot. Google keeps these in results
    # because they are still real places; for planning they are traps.
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
        )

    return PlacesResult(
        ok=True, query=text_query, places=[_to_place(item) for item in open_for_business]
    )


class StaticMap(ToolOutcome):
    """A rendered day map. `image` is PNG bytes when ok."""

    image: bytes | None = None
    places: list[str] = []


def static_map_params(places: list[str], width: int, height: int) -> list[tuple[str, str]]:
    """Query parameters for one day's map: numbered stops joined by a route line.

    A list of pairs, not a dict: `markers` repeats once per stop and a dict would keep
    only the last one.
    """
    size = f"{min(max(width, 64), MAX_MAP_EDGE)}x{min(max(height, 64), MAX_MAP_EDGE)}"
    params: list[tuple[str, str]] = [("size", size), ("scale", "2")]
    for label, place in zip(MARKER_LABELS, places, strict=False):
        params.append(("markers", f"color:0x00696e|label:{label}|{place}"))
    if len(places) > 1:
        # The line is the day's shape at a glance -- it is what turns pins into an
        # itinerary. Straight segments, not road geometry: this is orientation, not
        # navigation, and road polylines would mean a Routes call per hop.
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
    usable = [place.strip() for place in places if place and place.strip()][:MAX_MAP_PLACES]
    if not usable:
        return StaticMap(ok=False, error="no places with a location to draw")
    if not settings.google_maps_api_key:
        return StaticMap(ok=False, places=usable, error=MISSING_KEY)

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
        return StaticMap(ok=False, places=places, error="map service timed out")
    except httpx.HTTPError as exc:
        logger.warning("static map failed for %s: %s", places, exc)
        return StaticMap(ok=False, places=places, error=f"map service unavailable: {exc}")

    if not response.headers.get("content-type", "").startswith("image"):
        # Static Maps answers a bad request with a text body and a 200, so the content
        # type is the only reliable signal that an image actually came back.
        return StaticMap(
            ok=False, places=places, error=f"map service returned {response.text[:120]!r}"
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

    Static Maps geocodes marker strings for us, but the JavaScript API does not: it
    plots points. Doing it here rather than in the page keeps Geocoding on the server
    key, so the key the browser gets can be restricted to map rendering alone.

    Failures come back as `ok=False` entries in place rather than being dropped, so the
    caller can still number the stops the way the itinerary does.
    """
    usable = [place.strip() for place in places if place and place.strip()][:MAX_MAP_PLACES]
    if not usable:
        return []
    if not settings.google_maps_api_key:
        return [GeocodedPlace(ok=False, query=place, error=MISSING_KEY) for place in usable]

    if client is not None:
        return await _geocode_all(client, usable, language)
    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _geocode_all(owned, usable, language)


async def _geocode_all(
    client: httpx.AsyncClient, places: list[str], language: str
) -> list[GeocodedPlace]:
    # Concurrently: a day has up to 19 stops and doing them in series would put the
    # whole tool timeout budget on one request after another.
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
        return GeocodedPlace(ok=False, query=place, error="geocoding timed out")
    except httpx.HTTPError as exc:
        logger.warning("geocoding failed for %r: %s", place, exc)
        return GeocodedPlace(ok=False, query=place, error=f"geocoding unavailable: {exc}")

    results = payload.get("results") or []
    if not results:
        # ZERO_RESULTS is a fact, not an outage: this address does not resolve.
        return GeocodedPlace(
            ok=False, query=place, error=f"no location for {place!r} ({payload.get('status')})"
        )

    location = results[0].get("geometry", {}).get("location", {})
    latitude, longitude = location.get("lat"), location.get("lng")
    if latitude is None or longitude is None:
        return GeocodedPlace(ok=False, query=place, error="geocoding returned no coordinates")
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

    Two calls -- geocode the place, then ask the Time Zone API about that point -- which
    is why callers resolve it *once per plan* and reuse it, rather than per lookup.

    `on` is the trip date, and it is not decoration: the offset is a function of the
    instant, so a summer trip priced with a winter offset is an hour out, which is
    exactly the size of error this whole exercise is trying to remove.

    Returns None rather than a guess on every failure path. A wrong offset is worse than
    no offset: it would silently move every measurement to the wrong hour while looking
    like it had been fixed, whereas None falls back to a reference that is documented as
    optimistic.
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
            ok=False, origin=origin, destination=destination, mode=mode, error=MISSING_KEY
        )

    chosen = (mode or "WALK").upper()
    if chosen not in ("WALK", "DRIVE", "TRANSIT"):
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=chosen,
            error=f"mode must be WALK, DRIVE or TRANSIT, got {mode!r}",
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
        # Without this the API answers with free-flow times -- a Chicago crosstown hop
        # comes back as 13 minutes, which is true at 4am and nowhere near true when
        # anyone is actually travelling. The whole point of measuring is to beat the
        # model's optimism, and an optimistic measurement just launders it.
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
        )
    except httpx.HTTPError as exc:
        logger.warning("route lookup failed for %s -> %s: %s", origin, destination, exc)
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=mode,
            error=f"route service unavailable: {exc}",
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("unexpected route payload for %s -> %s: %s", origin, destination, exc)
        return TravelTime(
            ok=False,
            origin=origin,
            destination=destination,
            mode=mode,
            error=f"unexpected response from route service: {exc}",
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
