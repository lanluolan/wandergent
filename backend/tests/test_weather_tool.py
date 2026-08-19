"""Weather tool tests.

Every case runs against an injected mock transport, so the suite never touches the
network and stays deterministic. `today` is injected too, so the forecast-horizon
logic does not drift as the calendar moves.
"""

from datetime import date, timedelta

import httpx
import pytest

from app.tools.weather import (
    FORECAST_DAYS,
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


async def test_the_last_forecast_day_is_still_fetched() -> None:
    """The boundary the old test stepped over, and where this was wrong.

    Open-Meteo counts its 16 days inclusive of today, so `today + 15` is the last date it
    answers for. The horizon used to be computed as `today + 16`, so exactly one date --
    the one upstream refuses with a 400 -- got past the guard and onto the wire.
    """
    seen: list[httpx.Request] = []
    last = TODAY + timedelta(days=FORECAST_DAYS - 1)

    async with make_client(happy_handler(seen)) as client:
        result = await get_weather_forecast(
            "Chicago", last.isoformat(), last.isoformat(), client=client, today=TODAY
        )

    assert result.ok is True
    assert seen[1].url.params["end_date"] == last.isoformat()


async def test_the_day_after_the_window_never_reaches_the_wire() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=GEO_PAYLOAD)

    just_past = TODAY + timedelta(days=FORECAST_DAYS)
    async with make_client(handler) as client:
        result = await get_weather_forecast(
            "Chicago", just_past.isoformat(), just_past.isoformat(), client=client, today=TODAY
        )

    assert calls == []
    # ok, not an error: no forecast exists yet, so there is nothing to retry and nothing
    # went wrong. Reporting it as a failure is what put "weather service unavailable"
    # plus a raw upstream URL on screen for "3 days in Los Angeles next month".
    assert result.ok is True
    assert result.error is None
    assert result.days == []
    assert "no forecast yet" in result.note
    assert "seasonal norms" in result.note


async def test_a_trip_a_month_out_succeeds_with_nothing_to_show() -> None:
    """The product's most ordinary request, and the one this broke on."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=GEO_PAYLOAD)

    far_start = TODAY + timedelta(days=30)
    async with make_client(handler) as client:
        result = await get_weather_forecast(
            "Chicago",
            far_start.isoformat(),
            (far_start + timedelta(days=2)).isoformat(),
            client=client,
            today=TODAY,
        )

    assert result.ok is True
    assert calls == []


async def test_a_trip_straddling_the_horizon_says_which_days_are_missing() -> None:
    # The partial answer is the one most likely to be read as a whole one.
    seen: list[httpx.Request] = []
    start = TODAY + timedelta(days=FORECAST_DAYS - 3)

    async with make_client(happy_handler(seen)) as client:
        result = await get_weather_forecast(
            "Chicago",
            start.isoformat(),
            (start + timedelta(days=6)).isoformat(),
            client=client,
            today=TODAY,
        )

    assert result.ok is True
    horizon = (TODAY + timedelta(days=FORECAST_DAYS - 1)).isoformat()
    assert seen[1].url.params["end_date"] == horizon
    assert horizon in result.note
    assert "seasonal norms" in result.note


async def test_a_range_inside_the_window_carries_no_note() -> None:
    async with make_client(happy_handler()) as client:
        result = await get_weather_forecast(
            "Chicago",
            TODAY.isoformat(),
            (TODAY + timedelta(days=2)).isoformat(),
            client=client,
            today=TODAY,
        )

    assert result.ok is True
    assert result.note is None


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
