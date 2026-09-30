"""Tool efficiency is bounded, measurable, and does not trade away required facts."""

import asyncio
from datetime import date

import pytest

from app.agent.orchestrator import MAX_TOOL_CALLS, plan_trip
from app.tools import cache as cache_module
from app.tools.cache import CachedToolResult, TTLToolCache
from app.tools.maps import PlacesResult
from app.tools.registry import TOOL_FUNCTIONS, shared_cache_ttl, tool_cache_key
from app.tools.weather import DailyForecast, WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

TODAY = date(2026, 8, 5)
WEATHER = {"city": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-07"}


def weather_call(arguments: dict | None = None, call_id: str = "weather"):
    return tool_call("get_weather_forecast", arguments or WEATHER, call_id=call_id)


async def run(llm: FakeLLM):
    return await plan_trip(
        "2 days in Chicago", client=llm, model="test-model", today=TODAY, max_tool_rounds=2
    )


def test_semantically_equal_arguments_share_one_key() -> None:
    first = tool_cache_key(
        "search_places",
        {"query": "  Art   Museum ", "near": "CHICAGO", "purpose": "optional"},
    )
    second = tool_cache_key(
        "search_places",
        {"query": "art museum", "near": "chicago", "limit": 4, "language": "en"},
    )

    assert first == second
    assert shared_cache_ttl("remember_preference") == 0


def test_ttl_cache_expires_without_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: clock[0])
    cache = TTLToolCache(max_entries=2)
    value = CachedToolResult(content="{}", ok=True, code=None, error=None)

    cache.put("weather", value, ttl_seconds=5)
    assert cache.get("weather") is not None
    clock[0] += 6
    assert cache.get("weather") is None


def test_ttl_cache_evicts_the_least_recently_used_entry() -> None:
    cache = TTLToolCache(max_entries=2)
    value = CachedToolResult(content="{}", ok=True, code=None, error=None)
    cache.put("first", value, ttl_seconds=60)
    cache.put("second", value, ttl_seconds=60)
    assert cache.get("first") is not None  # first is now most recently used
    cache.put("third", value, ttl_seconds=60)

    assert cache.get("first") is not None
    assert cache.get("second") is None
    assert cache.get("third") is not None


async def test_normalized_duplicate_in_one_round_executes_once(monkeypatch) -> None:
    calls: list[str] = []

    async def fake(city: str, **_) -> WeatherForecast:
        calls.append(city)
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)
    equivalent = {**WEATHER, "city": "  chicago  "}
    llm = FakeLLM(
        [
            completion(
                tool_calls=[weather_call(call_id="a"), weather_call(equivalent, call_id="b")]
            ),
            completion(content=ITINERARY_JSON),
        ]
    )

    result = await run(llm)

    assert len(calls) == 1
    assert [record.cache_status for record in result.tool_calls] == ["miss", "run"]
    assert result.tool_usage.executed_calls == 1
    assert result.tool_usage.cache_hits == 1


async def test_successful_read_is_reused_across_runs(monkeypatch) -> None:
    calls: list[str] = []

    async def fake(city: str, **_) -> WeatherForecast:
        calls.append(city)
        return WeatherForecast(
            ok=True,
            city=city,
            days=[
                DailyForecast(date=date(2026, 8, 6), condition="Thunderstorms"),
                DailyForecast(date=date(2026, 8, 7), condition="clear"),
            ],
        )

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)
    first = FakeLLM([completion(tool_calls=[weather_call()]), completion(content=ITINERARY_JSON)])
    second = FakeLLM(
        [
            completion(
                tool_calls=[weather_call({**WEATHER, "city": " chicago "}, call_id="again")]
            ),
            completion(content=ITINERARY_JSON),
        ]
    )

    first_result = await run(first)
    second_result = await run(second)

    assert len(calls) == 1
    assert first_result.tool_calls[0].contributed
    assert second_result.tool_calls[0].cache_status == "shared"
    assert second_result.tool_usage.shared_cache_hits == 1
    assert second_result.tool_usage.executed_calls == 0


async def test_required_fact_wins_when_a_round_exceeds_the_budget(monkeypatch) -> None:
    weather: list[str] = []

    async def fake_weather(city: str, **_) -> WeatherForecast:
        weather.append(city)
        return WeatherForecast(ok=True, city=city)

    async def fake_places(query: str, near: str, **_) -> PlacesResult:
        return PlacesResult(ok=True, query=f"{query} in {near}")

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake_weather)
    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", fake_places)
    optional = [
        tool_call(
            "search_places",
            {"query": f"extra option {index}", "near": "Chicago", "purpose": "optional"},
            call_id=f"p{index}",
        )
        for index in range(MAX_TOOL_CALLS)
    ]
    llm = FakeLLM(
        [
            completion(tool_calls=[*optional, weather_call(call_id="required-weather")]),
            completion(content=ITINERARY_JSON),
        ]
    )

    result = await run(llm)

    assert weather == ["Chicago"]
    assert len(result.tool_calls) == MAX_TOOL_CALLS
    assert result.tool_usage.dropped_calls == 1
    assert result.tool_usage.requested_calls == MAX_TOOL_CALLS + 1
    assert result.tool_usage.calls_by_tool == {
        "search_places": MAX_TOOL_CALLS,
        "get_weather_forecast": 1,
    }


async def test_per_tool_parallelism_is_bounded_but_not_serial(monkeypatch) -> None:
    active = 0
    peak = 0

    async def fake_places(query: str, near: str, **_) -> PlacesResult:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return PlacesResult(ok=True, query=f"{query} in {near}")

    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", fake_places)
    calls = [
        tool_call(
            "search_places",
            {"query": f"required venue {index}", "near": "Chicago"},
            call_id=f"p{index}",
        )
        for index in range(8)
    ]
    llm = FakeLLM([completion(tool_calls=calls), completion(content=ITINERARY_JSON)])

    await run(llm)

    assert 1 < peak <= 4
