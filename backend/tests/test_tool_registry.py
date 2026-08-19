"""Tool registry tests.

The registry is the seam between model-chosen input and our code, so every way the
model can get a call wrong has to come back as feedback rather than an exception.
"""

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
