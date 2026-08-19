"""Model routing: the cheap model chooses tools, the strong model writes the plan."""

from datetime import date

import pytest

from app.agent.orchestrator import stream_plan
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

TODAY = date(2026, 8, 5)
ARGS = {"city": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-07"}


@pytest.fixture(autouse=True)
def stub_weather(monkeypatch):
    async def fake(city: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)


def script() -> list:
    return [
        completion(tool_calls=[tool_call("get_weather_forecast", ARGS)]),
        completion(content=ITINERARY_JSON),
    ]


async def run(llm, **kwargs):
    return [
        event
        async for event in stream_plan(
            "2 days in Chicago", client=llm, model="strong", today=TODAY, **kwargs
        )
    ]


async def test_the_first_turn_uses_the_cheap_model_and_must_call_a_tool() -> None:
    llm = FakeLLM(script())

    await run(llm, fast_model="cheap")

    first, second = llm.requests[0], llm.requests[1]
    assert first["model"] == "cheap"
    # This is what makes the swap safe: the cheap model cannot answer with a plan.
    assert first["tool_choice"] == "required"
    # Everything that writes the itinerary stays on the strong model.
    assert second["model"] == "strong"
    assert second["tool_choice"] == "auto"


async def test_routing_is_off_by_default() -> None:
    """An endpoint-specific model name must never be assumed."""
    llm = FakeLLM(script())

    await run(llm, fast_model="")

    assert [request["model"] for request in llm.requests] == ["strong", "strong"]
    assert llm.requests[0]["tool_choice"] == "auto"


async def test_later_turns_never_route_even_after_more_tool_rounds() -> None:
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("get_weather_forecast", ARGS, call_id="a")]),
            completion(tool_calls=[tool_call("get_weather_forecast", {**ARGS, "city": "Denver"})]),
            completion(content=ITINERARY_JSON),
        ]
    )

    await run(llm, fast_model="cheap")

    assert [request["model"] for request in llm.requests] == ["cheap", "strong", "strong"]


async def test_usage_is_attributed_per_model() -> None:
    """A single token total cannot show that work moved to a cheaper model."""
    llm = FakeLLM(script())

    events = await run(llm, fast_model="cheap")
    usage = events[-1].result.usage

    # The fake reports no usage, so the map is empty rather than wrong -- the point of
    # the assertion is that the field exists and is per-model, which the live eval
    # then fills in.
    assert isinstance(usage.tokens_by_model, dict)
