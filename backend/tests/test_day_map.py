"""Tests for the day-map endpoint and the URL it builds.

Offline: the Google call is served by a mock transport, so no quota is spent and the
assertions are about *what we ask for*, which is the part that breaks silently.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.tools import maps
from app.tools.maps import MAX_MAP_PLACES, render_day_map, static_map_params

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture(autouse=True)
def _with_key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_every_stop_gets_its_own_numbered_marker() -> None:
    params = static_map_params(["A", "B", "C"], 640, 400)
    markers = [value for key, value in params if key == "markers"]

    assert len(markers) == 3
    assert markers[0].endswith("|A") and "label:1" in markers[0]
    assert markers[2].endswith("|C") and "label:3" in markers[2]


def test_the_stops_are_joined_into_one_route_line() -> None:
    params = dict(static_map_params(["A", "B"], 640, 400))

    assert "A|B" in params["path"]


def test_a_single_stop_draws_no_line() -> None:
    """A path through one point is not a route, it is a pin with extra parameters."""
    assert "path" not in dict(static_map_params(["A"], 640, 400))


def test_size_is_clamped_to_what_the_api_will_serve() -> None:
    assert dict(static_map_params(["A"], 9000, 9000))["size"] == f"{640}x{640}"
    assert dict(static_map_params(["A"], 1, 1))["size"] == "64x64"


async def test_renders_a_png() -> None:
    async with make_client(
        lambda _: httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    ) as client:
        result = await render_day_map(["Willis Tower, Chicago"], client=client)

    assert result.ok
    assert result.image == PNG


async def test_a_text_body_is_not_an_image_even_with_a_200() -> None:
    """Static Maps answers a bad request with 200 and a text explanation, so the status
    code alone would let an error page through as a picture."""
    async with make_client(
        lambda _: httpx.Response(200, text="Invalid request", headers={"content-type": "text/html"})
    ) as client:
        result = await render_day_map(["nowhere"], client=client)

    assert not result.ok
    assert "Invalid request" in result.error


async def test_more_stops_than_labels_are_dropped_rather_than_mislabelled() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    async with make_client(handler) as client:
        await render_day_map([f"stop {i}" for i in range(MAX_MAP_PLACES + 5)], client=client)

    assert str(seen[0].url).count("markers=") == MAX_MAP_PLACES


async def test_blank_places_are_ignored_and_an_empty_day_is_refused() -> None:
    result = await render_day_map(["   ", ""])

    assert not result.ok
    assert "no places" in result.error


async def test_without_a_key_it_says_so_instead_of_calling(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_maps_api_key", "")

    result = await render_day_map(["Willis Tower, Chicago"])

    assert not result.ok
    assert "GOOGLE_MAPS_API_KEY" in result.error


# --- the endpoint -------------------------------------------------------------------


def test_the_endpoint_returns_an_image_the_client_can_cache(monkeypatch) -> None:
    async def _render(places, width=640, height=400, **_):
        return maps.StaticMap(ok=True, places=places, image=PNG)

    monkeypatch.setattr("app.main.render_day_map", _render)

    with TestClient(app) as client:
        response = client.get("/day-map", params={"place": ["A", "B"]})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "max-age" in response.headers["cache-control"]
    assert response.content == PNG


def test_a_failed_render_is_a_502_not_a_500(monkeypatch) -> None:
    """The plan is fine; the picture of it is not. That is an upstream problem."""

    async def _render(places, width=640, height=400, **_):
        return maps.StaticMap(ok=False, places=places, error="map service timed out")

    monkeypatch.setattr("app.main.render_day_map", _render)

    with TestClient(app) as client:
        response = client.get("/day-map", params={"place": ["A"]})

    assert response.status_code == 502
    assert "timed out" in response.json()["detail"]


def test_the_endpoint_requires_at_least_one_place() -> None:
    with TestClient(app) as client:
        assert client.get("/day-map").status_code == 422
