"""Weather tool, backed by Open-Meteo.

Open-Meteo needs no API key and resolves Chinese city names, so Phase 1 runs end to
end without any signup. Two upstream calls: geocoding (city name -> coordinates),
then the daily forecast.

Open-Meteo only publishes ~16 days of forecast. A trip planned further out is the
normal case, not an error, so an out-of-range request comes back as a degraded
result with an explanation the planner can act on ("no forecast, use seasonal
norms") rather than as an exception.
"""

import logging
from datetime import date, timedelta

import httpx
from pydantic import BaseModel

from app.config import settings
from app.tools.base import ToolOutcome

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo serves at most 16 days ahead on the free forecast endpoint.
MAX_FORECAST_DAYS = 16

# Guard against an LLM asking for a whole season in one call.
MAX_REQUESTED_DAYS = 16

DAILY_FIELDS = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
)

# WMO weather interpretation codes -> short descriptions.
WMO_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


class DailyForecast(BaseModel):
    """One day of daily-aggregated weather."""

    date: date
    condition: str
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability_pct: int | None = None


class WeatherForecast(ToolOutcome):
    """Weather tool result. `days` may be empty even when `ok` is False."""

    city: str
    resolved_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    days: list[DailyForecast] = []


# OpenAI function-calling schema. Kept next to the implementation so the two cannot
# drift apart; the registry only wires them together.
WEATHER_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_weather_forecast",
        "description": (
            "Get the daily weather forecast for a city over a date range. Use it to decide "
            "whether outdoor activities are viable on a given day. Only covers up to 16 days "
            "ahead; for later dates it returns no data and the plan should rely on seasonal "
            "norms instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, e.g. 'Chicago' or 'Boston'.",
                },
                "start_date": {
                    "type": "string",
                    "description": "First day of the range, ISO format YYYY-MM-DD.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Last day of the range, ISO format YYYY-MM-DD.",
                },
            },
            "required": ["city", "start_date", "end_date"],
            "additionalProperties": False,
        },
    },
}


def _parse_date(value: str | date) -> date:
    """Accept either a date or an ISO string; raise ValueError on anything else."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


async def _geocode(client: httpx.AsyncClient, city: str, language: str) -> dict | None:
    """Resolve a city name to coordinates. Returns None when nothing matches."""
    resp = await client.get(
        GEOCODING_URL,
        params={"name": city, "count": 1, "language": language, "format": "json"},
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    return results[0] if results else None


async def _fetch_daily(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
    start: date,
    end: date,
) -> dict:
    """Fetch the daily forecast block for a coordinate pair and date range."""
    resp = await client.get(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "daily": ",".join(DAILY_FIELDS),
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    resp.raise_for_status()
    return resp.json().get("daily") or {}


def _to_daily_forecasts(daily: dict) -> list[DailyForecast]:
    """Turn Open-Meteo's column-oriented daily block into row objects."""
    days: list[DailyForecast] = []
    for index, day in enumerate(daily.get("time") or []):

        def column(field: str, i: int = index):
            values = daily.get(field) or []
            return values[i] if i < len(values) else None

        code = column("weather_code")
        days.append(
            DailyForecast(
                date=_parse_date(day),
                condition=WMO_CODES.get(code, "unknown") if code is not None else "unknown",
                temp_min_c=column("temperature_2m_min"),
                temp_max_c=column("temperature_2m_max"),
                precipitation_mm=column("precipitation_sum"),
                precipitation_probability_pct=column("precipitation_probability_max"),
            )
        )
    return days


def _clamp_range(start: date, end: date, today: date) -> tuple[date, date, str | None]:
    """Clip a requested range to the forecast window.

    Returns the usable range plus a note when it had to be trimmed, or a reason when
    nothing at all is available.
    """
    horizon = today + timedelta(days=MAX_FORECAST_DAYS)
    if end < today:
        return start, end, "requested dates are in the past; the forecast only covers today onward"
    if start > horizon:
        return (
            start,
            end,
            f"requested dates are more than {MAX_FORECAST_DAYS} days ahead, "
            "which is beyond the forecast horizon",
        )

    clamped_start = max(start, today)
    clamped_end = min(end, horizon)
    if (clamped_end - clamped_start).days >= MAX_REQUESTED_DAYS:
        clamped_end = clamped_start + timedelta(days=MAX_REQUESTED_DAYS - 1)
    return clamped_start, clamped_end, None


async def get_weather_forecast(
    city: str,
    start_date: str | date,
    end_date: str | date,
    *,
    client: httpx.AsyncClient | None = None,
    language: str = "zh",
    today: date | None = None,
) -> WeatherForecast:
    """Look up the daily forecast for `city` between the two dates.

    Never raises for an upstream problem: a timeout, an HTTP error, an unknown city
    or an out-of-range date range all come back as `ok=False` with a reason.
    `client` is injectable so tests can run without touching the network.
    """
    if client is not None:
        return await _forecast(client, city, start_date, end_date, language, today)

    timeout = httpx.Timeout(settings.tool_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as owned_client:
        return await _forecast(owned_client, city, start_date, end_date, language, today)


async def _forecast(
    client: httpx.AsyncClient,
    city: str,
    start_date: str | date,
    end_date: str | date,
    language: str,
    today: date | None,
) -> WeatherForecast:
    """Body of `get_weather_forecast`, with the HTTP client already resolved."""
    try:
        start = _parse_date(start_date)
        end = _parse_date(end_date)
    except (TypeError, ValueError):
        return WeatherForecast(
            ok=False, city=city, error="dates must be ISO formatted, e.g. 2026-08-05"
        )

    if end < start:
        return WeatherForecast(ok=False, city=city, error="end_date is before start_date")

    start, end, reason = _clamp_range(start, end, today or date.today())
    if reason is not None:
        return WeatherForecast(ok=False, city=city, error=reason)

    try:
        place = await _geocode(client, city, language)
        if place is None:
            return WeatherForecast(ok=False, city=city, error=f"no location found for {city!r}")

        latitude = place["latitude"]
        longitude = place["longitude"]
        daily = await _fetch_daily(client, latitude, longitude, start, end)
        days = _to_daily_forecasts(daily)
    except httpx.TimeoutException:
        logger.warning("weather lookup timed out for %s", city)
        return WeatherForecast(ok=False, city=city, error="weather service timed out")
    except httpx.HTTPError as exc:
        logger.warning("weather lookup failed for %s: %s", city, exc)
        return WeatherForecast(ok=False, city=city, error=f"weather service unavailable: {exc}")
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("unexpected weather payload for %s: %s", city, exc)
        return WeatherForecast(
            ok=False, city=city, error=f"unexpected response from weather service: {exc}"
        )

    return WeatherForecast(
        ok=True,
        city=city,
        resolved_name=place.get("name"),
        latitude=latitude,
        longitude=longitude,
        days=days,
    )
