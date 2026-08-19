"""Weather tool tests.

Every case runs against an injected mock transport, so the suite never touches the
network and stays deterministic. `today` is injected too, so the forecast-horizon
logic does not drift as the calendar moves.
"""

from datetime import date, timedelta

import httpx
import pytest

from app.tools.weather import (
    MAX_FORECAST_DAYS,
    WEATHER_TOOL_SCHEMA,
    get_weather_forecast,
)

TODAY = date(2026, 8, 5)

GEO_PAYLOAD = {
    "results": [
        {
            "name": "Chicago",
            "latitude": 30.66,
            "longitude": 104.06,
            "country": "Japan",
        }
    ]
}

DAILY_PAYLOAD = {
    "daily": {
        "time": ["2026-08-06", "2026-08-07"],
        "weather_code": [61, 0],
        "temperature_2m_max": [30.1, 33.4],
        "temperature_2m_min": [22.0, 23.5],
        "precipitation_sum": [5.2, 0.0],
        "precipitation_probability_max": [80, 5],
    }
}


def make_client(handler) -> httpx.AsyncClient:
    """Build an AsyncClient whose requests are served by `handler`, never the network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def happy_handler(seen: list[httpx.Request] | None = None):
    """Handler that geocodes successfully and returns a two-day forecast."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if "geocoding-api" in str(request.url):
            return httpx.Response(200, json=GEO_PAYLOAD)
        return httpx.Response(200, json=DAILY_PAYLOAD)

    return handler


async def test_forecast_parses_daily_rows() -> None:
    async with make_client(happy_handler()) as client:
        result = await get_weather_forecast(
            "Chicago", "2026-08-06", "2026-08-07", client=client, today=TODAY
        )

    assert result.ok is True
    assert result.error is None
    assert result.resolved_name == "Chicago"
    assert (result.latitude, result.longitude) == (30.66, 104.06)
    assert len(result.days) == 2

    rainy = result.days[0]
    assert rainy.date == date(2026, 8, 6)
    assert rainy.condition == "slight rain"
    assert rainy.temp_max_c == 30.1
    assert rainy.precipitation_probability_pct == 80


async def test_forecast_sends_expected_query_params() -> None:
    seen: list[httpx.Request] = []
    async with make_client(happy_handler(seen)) as client:
        await get_weather_forecast(
            "Chicago", "2026-08-06", "2026-08-07", client=client, today=TODAY
        )

    geo, forecast = seen
    assert geo.url.params["name"] == "Chicago"
    assert forecast.url.params["start_date"] == "2026-08-06"
    assert forecast.url.params["end_date"] == "2026-08-07"
    assert forecast.url.params["timezone"] == "auto"
    assert "temperature_2m_max" in forecast.url.params["daily"]


async def test_timeout_degrades_instead_of_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    async with make_client(handler) as client:
        result = await get_weather_forecast(
            "Chicago", "2026-08-06", "2026-08-07", client=client, today=TODAY
        )

    assert result.ok is False
    assert "timed out" in result.error
    assert result.days == []


async def test_upstream_error_degrades_instead_of_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    async with make_client(handler) as client:
        result = await get_weather_forecast(
            "Chicago", "2026-08-06", "2026-08-07", client=client, today=TODAY
        )

    assert result.ok is False
    assert "unavailable" in result.error


async def test_malformed_payload_degrades_instead_of_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding-api" in str(request.url):
            # Geocoding hit, but the coordinates the caller depends on are missing.
            return httpx.Response(200, json={"results": [{"name": "Chicago"}]})
        return httpx.Response(200, json=DAILY_PAYLOAD)

    async with make_client(handler) as client:
        result = await get_weather_forecast(
            "Chicago", "2026-08-06", "2026-08-07", client=client, today=TODAY
        )

    assert result.ok is False
    assert "unexpected response" in result.error


async def test_unknown_city_reports_no_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"generationtime_ms": 0.1})

    async with make_client(handler) as client:
        result = await get_weather_forecast(
            "Xyzzyville", "2026-08-06", "2026-08-07", client=client, today=TODAY
        )

    assert result.ok is False
    assert "no location found" in result.error


async def test_dates_beyond_horizon_skip_the_network() -> None:
    """A trip a month out is the common case; it must degrade without calling upstream."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=GEO_PAYLOAD)

    far_start = TODAY + timedelta(days=MAX_FORECAST_DAYS + 5)
    async with make_client(handler) as client:
        result = await get_weather_forecast(
            "Chicago",
            far_start.isoformat(),
            (far_start + timedelta(days=2)).isoformat(),
            client=client,
            today=TODAY,
        )

    assert result.ok is False
    assert "beyond the forecast horizon" in result.error
    assert calls == []


async def test_past_dates_report_out_of_range() -> None:
    async with make_client(happy_handler()) as client:
        result = await get_weather_forecast(
            "Chicago", "2026-07-01", "2026-07-03", client=client, today=TODAY
        )

    assert result.ok is False
    assert "in the past" in result.error


async def test_range_overlapping_today_is_clamped_forward() -> None:
    seen: list[httpx.Request] = []
    async with make_client(happy_handler(seen)) as client:
        result = await get_weather_forecast(
            "Chicago", "2026-08-01", "2026-08-07", client=client, today=TODAY
        )

    assert result.ok is True
    forecast = seen[1]
    assert forecast.url.params["start_date"] == TODAY.isoformat()
    assert forecast.url.params["end_date"] == "2026-08-07"


@pytest.mark.parametrize(
    ("start", "end", "fragment"),
    [
        ("not-a-date", "2026-08-07", "ISO formatted"),
        ("2026-08-07", "2026-08-06", "before start_date"),
    ],
)
async def test_invalid_input_is_rejected(start: str, end: str, fragment: str) -> None:
    async with make_client(happy_handler()) as client:
        result = await get_weather_forecast("Chicago", start, end, client=client, today=TODAY)

    assert result.ok is False
    assert fragment in result.error


def test_function_calling_schema_matches_the_implementation() -> None:
    """Guard against the schema and the callable drifting apart."""
    fn = WEATHER_TOOL_SCHEMA["function"]
    assert fn["name"] == get_weather_forecast.__name__
    assert set(fn["parameters"]["required"]) == {"city", "start_date", "end_date"}
