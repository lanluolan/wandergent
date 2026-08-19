"""Orchestration-loop tests.

The LLM is scripted and the tool registry is stubbed, so these cover the loop's own
behaviour -- tool dispatch, failure feedback, the JSON repair round, the round cap --
without a network call or an API key.
"""

import json
from datetime import date

import httpx
import pytest
from openai import APITimeoutError

from app.agent.orchestrator import PlanningTimeout, plan_trip
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

TODAY = date(2026, 8, 5)


@pytest.fixture
def stub_weather(monkeypatch):
    """Replace the real weather tool with a recorder, so no test touches the network."""
    seen: list[dict] = []

    async def fake(city: str, start_date: str, end_date: str, **kwargs) -> WeatherForecast:
        seen.append({"city": city, "start_date": start_date, "end_date": end_date})
        return WeatherForecast(ok=True, city=city, resolved_name=city, days=[])

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)
    return seen


def weather_turn() -> object:
    """An assistant turn that asks for the weather in Chicago."""
    return completion(
        tool_calls=[
            tool_call(
                "get_weather_forecast",
                {"city": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-07"},
            )
        ]
    )


async def test_tool_round_then_itinerary(stub_weather) -> None:
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    result = await plan_trip(
        "2 days in Chicago, budget 3000", client=llm, model="test-model", today=TODAY
    )

    assert stub_weather == [
        {"city": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-07"}
    ]
    assert result.warnings == []
    assert [(c.name, c.ok) for c in result.tool_calls] == [("get_weather_forecast", True)]
    # Fast path: the turn that ended the tool loop was already the itinerary, so no
    # extra emit round trip was spent.
    assert len(llm.requests) == 2

    itinerary = result.itinerary
    assert itinerary is not None
    assert itinerary.destination == "Chicago"
    # Totals are derived from the activities, never taken from the model.
    assert itinerary.days[0].estimated_cost == 180.0
    assert itinerary.total_estimated_cost == 290.0


async def test_system_prompt_anchors_relative_dates(stub_weather) -> None:
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    await plan_trip("Chicago next month", client=llm, model="test-model", today=TODAY)

    system_prompt = llm.requests[0]["messages"][0]["content"]
    assert "2026-08-05" in system_prompt
    assert "Wednesday" in system_prompt


async def test_tool_result_is_fed_back_to_the_model(stub_weather) -> None:
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    # The second request must carry the assistant's tool call and our tool reply.
    messages = llm.requests[1]["messages"]
    assistant, tool_reply = messages[-2], messages[-1]
    assert assistant["tool_calls"][0]["function"]["name"] == "get_weather_forecast"
    assert tool_reply["role"] == "tool"
    assert tool_reply["tool_call_id"] == "call_1"
    assert json.loads(tool_reply["content"])["ok"] is True


async def test_failing_tool_degrades_without_killing_the_plan(monkeypatch) -> None:
    async def failing(city: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=False, city=city, error="weather service timed out")

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", failing)
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    result = await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert result.tool_calls[0].ok is False
    assert result.tool_calls[0].error == "weather service timed out"
    # The failure is visible to the model, and a plan still comes out.
    assert "timed out" in llm.requests[1]["messages"][-1]["content"]
    assert result.itinerary is not None


async def test_unknown_tool_is_reported_back(stub_weather) -> None:
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("book_flight", {"to": "Chicago"})]),
            completion(content=ITINERARY_JSON),
        ]
    )

    result = await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert result.tool_calls[0].ok is False
    assert "unknown tool" in result.tool_calls[0].error
    assert result.itinerary is not None


async def test_markdown_fenced_json_is_accepted(stub_weather) -> None:
    """Models wrap JSON in fences even when told not to; that must not cost a retry."""
    llm = FakeLLM([completion(content=f"```json\n{ITINERARY_JSON}\n```")])

    result = await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert result.itinerary is not None
    assert len(llm.requests) == 1


async def test_invalid_json_triggers_one_repair_round(stub_weather) -> None:
    broken = json.dumps({"destination": "Chicago"})  # missing the required dates
    llm = FakeLLM(
        [
            completion(content="ready"),
            completion(content=broken),
            completion(content=ITINERARY_JSON),
        ]
    )

    result = await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert result.itinerary is not None
    assert len(llm.requests) == 3
    repair_prompt = llm.requests[2]["messages"][-1]["content"]
    assert "did not validate" in repair_prompt
    assert "start_date" in repair_prompt


async def test_gives_up_after_the_repair_round(stub_weather) -> None:
    llm = FakeLLM([completion(content="ready"), completion(content="{}"), completion(content="{}")])

    result = await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert result.itinerary is None
    assert result.raw_reply == "{}"
    assert any(w.code == "no_itinerary" for w in result.warnings)


async def test_tool_round_cap_is_enforced(stub_weather) -> None:
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    result = await plan_trip(
        "2 days in Chicago", client=llm, model="test-model", today=TODAY, max_tool_rounds=1
    )

    assert any(w.code == "tool_rounds_spent" and w.budget == 1 for w in result.warnings)
    assert result.itinerary is not None


async def test_upstream_timeout_becomes_planning_timeout(stub_weather) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    llm = FakeLLM([APITimeoutError(request)])

    with pytest.raises(PlanningTimeout):
        await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)
