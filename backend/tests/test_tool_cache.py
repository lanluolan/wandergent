"""Repeated tool calls are replayed, not re-run.

From a live run: the model asked for the same forecast in three consecutive rounds and
wrote near-duplicate preferences each time. The tool itself must run once.
"""

from datetime import date

import pytest

from app.agent.orchestrator import stream_plan
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

TODAY = date(2026, 8, 5)
ARGS = {"city": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-07"}


@pytest.fixture
def weather_calls(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def fake(city: str, start_date: str, end_date: str, **kwargs) -> WeatherForecast:
        seen.append({"city": city, "start_date": start_date, "end_date": end_date})
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)
    return seen


async def collect(llm):
    return [
        event
        async for event in stream_plan(
            "2 days in Chicago", client=llm, model="test-model", today=TODAY, max_tool_rounds=4
        )
    ]


async def test_an_identical_repeat_does_not_run_the_tool_again(weather_calls) -> None:
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("get_weather_forecast", ARGS, call_id="a")]),
            completion(tool_calls=[tool_call("get_weather_forecast", ARGS, call_id="b")]),
            completion(content=ITINERARY_JSON),
        ]
    )

    events = await collect(llm)

    assert len(weather_calls) == 1, "the tool ran more than once for identical arguments"
    # Both attempts are still reported, so the trail stays honest about what the agent did.
    assert len([e for e in events if e.type == "tool_call"]) == 2
    assert events[-1].result.itinerary is not None


async def test_the_replay_tells_the_model_it_is_a_repeat(weather_calls) -> None:
    """Silently returning the same thing invites another identical call."""
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("get_weather_forecast", ARGS, call_id="a")]),
            completion(tool_calls=[tool_call("get_weather_forecast", ARGS, call_id="b")]),
            completion(content=ITINERARY_JSON),
        ]
    )

    await collect(llm)

    # Third request carries the reply to the repeated call.
    replay = llm.requests[2]["messages"][-1]["content"]
    assert '"repeat": true' in replay
    assert "use it and move on" in replay.lower()


async def test_different_arguments_still_run(weather_calls) -> None:
    other = {**ARGS, "city": "Denver"}
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("get_weather_forecast", ARGS, call_id="a")]),
            completion(tool_calls=[tool_call("get_weather_forecast", other, call_id="b")]),
            completion(content=ITINERARY_JSON),
        ]
    )

    await collect(llm)

    assert [call["city"] for call in weather_calls] == ["Chicago", "Denver"]
