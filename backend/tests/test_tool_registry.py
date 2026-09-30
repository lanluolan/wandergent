"""Tool registry tests.

The registry is the seam between model-chosen input and our code, so every way the
model can get a call wrong has to come back as feedback rather than an exception.
"""

from app.tools.base import RATE_LIMITED, TIMED_OUT
from app.tools.registry import TOOL_FUNCTIONS, TOOL_SCHEMAS, call_tool
from app.tools.weather import WeatherForecast


def test_schema_names_match_registered_callables() -> None:
    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert names == set(TOOL_FUNCTIONS)


async def test_unknown_tool_lists_what_is_available() -> None:
    outcome = await call_tool("book_flight", {"to": "Chicago"})

    assert outcome.ok is False
    assert "unknown tool" in outcome.error
    assert "get_weather_forecast" in outcome.error


async def test_bad_arguments_are_reported_not_raised(monkeypatch) -> None:
    async def fake(city: str, start_date: str, end_date: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)

    outcome = await call_tool("get_weather_forecast", {"town": "Chicago"})

    assert outcome.ok is False
    assert "bad arguments" in outcome.error


async def test_unexpected_tool_exception_is_contained(monkeypatch) -> None:
    async def exploding(**kwargs) -> WeatherForecast:
        raise RuntimeError("upstream client blew up")

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", exploding)

    outcome = await call_tool("get_weather_forecast", {"city": "Chicago"})

    assert outcome.ok is False
    assert "failed unexpectedly" in outcome.error


async def test_transient_read_failure_is_retried_once(monkeypatch) -> None:
    calls = 0

    async def flaky(city: str, **kwargs) -> WeatherForecast:
        nonlocal calls
        calls += 1
        if calls == 1:
            return WeatherForecast(ok=False, city=city, error="slow", code=TIMED_OUT)
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", flaky)

    outcome = await call_tool("get_weather_forecast", {"city": "Chicago"})

    assert outcome.ok
    assert outcome.attempts == 2
    assert calls == 2


async def test_rate_limit_degrades_without_an_immediate_retry(monkeypatch) -> None:
    calls = 0

    async def limited(city: str, **kwargs) -> WeatherForecast:
        nonlocal calls
        calls += 1
        return WeatherForecast(ok=False, city=city, error="quota", code=RATE_LIMITED)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", limited)

    outcome = await call_tool("get_weather_forecast", {"city": "Chicago"})

    assert not outcome.ok
    assert outcome.attempts == 1
    assert calls == 1


async def test_memory_write_is_never_retried(monkeypatch) -> None:
    calls = 0

    async def unavailable(**kwargs):
        nonlocal calls
        calls += 1
        return WeatherForecast(ok=False, city="", error="store down", code="unavailable")

    monkeypatch.setitem(TOOL_FUNCTIONS, "remember_preference", unavailable)

    outcome = await call_tool("remember_preference", {"preferences": []})

    assert not outcome.ok
    assert calls == 1


async def test_http_reader_receives_the_batch_connection_pool(monkeypatch) -> None:
    marker = object()
    seen = None

    async def fake(city: str, *, client=None) -> WeatherForecast:
        nonlocal seen
        seen = client
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)

    await call_tool("get_weather_forecast", {"city": "Chicago"}, context={"http_client": marker})

    assert seen is marker
