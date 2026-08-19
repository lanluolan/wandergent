"""Check that every Google Maps API this project needs is enabled and reachable.

One minimal request per API, so a disabled API, a billing problem or a network block
surfaces in seconds instead of after a day of debugging tool code. Run it after
rotating the key or moving to a new machine:

    cd backend && uv run python -m scripts.probe_maps

Costs a handful of requests. The key is never printed.
"""

import asyncio

import httpx

from app.config import settings
from app.tools.maps import (
    PLACES_FIELD_MASK,
    PLACES_SEARCH_URL,
    ROUTES_FIELD_MASK,
    ROUTES_URL,
)

STATIC_MAP_URL = "https://maps.googleapis.com/maps/api/staticmap"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

KEY = settings.google_maps_api_key


def report(name: str, ok: bool, detail: str) -> bool:
    print(f"{'OK  ' if ok else 'FAIL'}  {name:<20} {detail}")
    return ok


def _headers(field_mask: str) -> dict[str, str]:
    return {"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": field_mask}


async def geocoding(client: httpx.AsyncClient) -> bool:
    body = (await client.get(GEOCODE_URL, params={"address": "Boston, Japan", "key": KEY})).json()
    if body.get("status") != "OK":
        return report("Geocoding API", False, f"{body.get('status')}: {body.get('error_message')}")
    point = body["results"][0]["geometry"]["location"]
    return report("Geocoding API", True, f"Boston -> {point['lat']:.4f},{point['lng']:.4f}")


async def places(client: httpx.AsyncClient) -> bool:
    response = await client.post(
        PLACES_SEARCH_URL,
        headers=_headers(PLACES_FIELD_MASK),
        json={"textQuery": "kushikatsu restaurant in Millennium Park Chicago", "maxResultCount": 3},
    )
    if response.status_code != 200:
        return report(
            "Places API (New)", False, f"HTTP {response.status_code}: {response.text[:140]}"
        )
    found = response.json().get("places") or []
    if not found:
        return report("Places API (New)", False, "200 but no results")
    name = (found[0].get("displayName") or {}).get("text", "?")
    return report("Places API (New)", True, f"{len(found)} hits, e.g. {name}")


async def routes(client: httpx.AsyncClient) -> bool:
    """WALK and DRIVE only -- TRANSIT returns 200 with no route, see app/tools/maps.py."""
    ok = True
    for mode in ("WALK", "DRIVE"):
        response = await client.post(
            ROUTES_URL,
            headers=_headers(ROUTES_FIELD_MASK),
            json={
                "origin": {"address": "Chicago Station, Chicago, IL"},
                "destination": {"address": "Willis Tower, Chicago, IL"},
                "travelMode": mode,
            },
        )
        if response.status_code != 200:
            ok = report(f"Routes API [{mode}]", False, f"HTTP {response.status_code}") and ok
            continue
        found = response.json().get("routes") or []
        if not found:
            ok = report(f"Routes API [{mode}]", False, "200 but no route") and ok
            continue
        ok = (
            report(
                f"Routes API [{mode}]",
                True,
                f"{found[0].get('duration')} / {found[0].get('distanceMeters')} m",
            )
            and ok
        )
    return ok


async def static_map(client: httpx.AsyncClient) -> bool:
    response = await client.get(
        STATIC_MAP_URL,
        params={"size": "600x400", "markers": "color:red|Willis Tower,Chicago", "key": KEY},
    )
    content_type = response.headers.get("content-type", "")
    if response.status_code == 200 and content_type.startswith("image"):
        return report("Maps Static API", True, f"{content_type}, {len(response.content)} bytes")
    return report("Maps Static API", False, f"HTTP {response.status_code}: {response.text[:140]}")


async def main() -> int:
    if not KEY:
        print("GOOGLE_MAPS_API_KEY is empty -- nothing to probe. See backend/.env.example.")
        return 1
    print(f"probing with a key of length {len(KEY)} (not printed)\n")
    healthy = True
    async with httpx.AsyncClient(timeout=15) as client:
        for probe in (geocoding, places, routes, static_map):
            try:
                healthy = await probe(client) and healthy
            except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
                healthy = report(probe.__name__, False, f"{type(exc).__name__}: {exc}") and healthy
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
