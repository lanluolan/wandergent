"""Tests for the two budgets that stop a confused model, and for how the run says so.

A round cap alone bounds turns, not work: one round can carry any number of tool calls.
Both caps exist, both have to end the loop, and neither is allowed to shrink the
research silently -- a plan built on fewer answers than were asked for is a plan with a
caveat on it.
"""

import json

import pytest

from app.agent.orchestrator import LAST_ROUND_NOTICE, MAX_TOOL_CALLS, plan_trip
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import FakeLLM, completion, tool_call

ITINERARY = json.dumps(
    {
        "destination": "Chicago",
        "start_date": "2026-09-07",
        "end_date": "2026-09-07",
        "budget": 500,
        "days": [
            {
                "date": "2026-09-07",
                "summary": "one day",
                "activities": [
                    {
                        "start_time": "10:00",
                        "end_time": "12:00",
                        "title": "Art Institute of Chicago",
                        "location": "Art Institute of Chicago",
                        "estimated_cost": 30.0,
                    }
                ],
            }
        ],
    }
)


@pytest.fixture
def counting_tool(monkeypatch):
    """A weather tool that records every call it is actually asked to make."""
    made: list[str] = []

    async def fake(city: str, **kwargs) -> WeatherForecast:
        made.append(city)
        return WeatherForecast(ok=True, city=city, resolved_name=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)
    return made


def flood(count: int):
    """One assistant turn asking for `count` tool calls at once."""
    return completion(
        tool_calls=[
            tool_call(
                "get_weather_forecast",
                {"city": f"Chicago {index}", "start_date": "2026-09-07", "end_date": "2026-09-07"},
                call_id=f"c{index}",
            )
            for index in range(count)
        ]
    )


async def test_one_round_cannot_spend_more_than_the_call_budget(counting_tool) -> None:
    """The round cap says nothing about fan-out. This is what bounds the fan-out."""
    llm = FakeLLM([flood(MAX_TOOL_CALLS + 6), completion(content=ITINERARY)])

    await plan_trip("weather in Chicago", client=llm, model="test-model")

    assert len(counting_tool) == MAX_TOOL_CALLS


async def test_the_dropped_calls_are_named_not_swallowed(counting_tool) -> None:
    """A quietly truncated research phase reads as a confident plan. Say what was cut."""
    llm = FakeLLM([flood(MAX_TOOL_CALLS + 6), completion(content=ITINERARY)])

    result = await plan_trip("weather in Chicago", client=llm, model="test-model")

    assert any(
        warning.code == "tool_calls_dropped"
        and warning.dropped_tools == ["get_weather_forecast"]
        and warning.dropped_calls == 6
        and warning.budget == MAX_TOOL_CALLS
        for warning in result.warnings
    )


async def test_spending_the_budget_ends_the_loop(counting_tool) -> None:
    """Exhausting calls has to stop the loop, not just empty a single round -- otherwise
    every remaining round is a turn that can request nothing and answers nothing."""
    llm = FakeLLM([flood(MAX_TOOL_CALLS), completion(content=ITINERARY)])

    result = await plan_trip("weather in Chicago", client=llm, model="test-model")

    assert len(llm.requests) == 2  # the flood, then the itinerary. No idle rounds.
    assert any(
        warning.code == "tool_calls_spent" and warning.budget == MAX_TOOL_CALLS
        for warning in result.warnings
    )


async def test_a_still_shipped_plan_survives_the_budget(counting_tool) -> None:
    """The point of degrading is that something comes out the other end."""
    llm = FakeLLM([flood(MAX_TOOL_CALLS + 6), completion(content=ITINERARY)])

    result = await plan_trip("weather in Chicago", client=llm, model="test-model")

    assert result.itinerary is not None
    assert result.itinerary.destination == "Chicago"


async def test_the_model_is_told_when_it_is_on_its_last_round(counting_tool) -> None:
    """Observed live twice: the loop goes quiet mid-research and the run ends with no
    plan. The model can only wrap up early if something tells it to."""
    llm = FakeLLM(
        [
            completion(
                tool_calls=[
                    tool_call(
                        "get_weather_forecast",
                        {"city": "Chicago", "start_date": "2026-09-07", "end_date": "2026-09-07"},
                    )
                ]
            ),
            completion(
                tool_calls=[
                    tool_call(
                        "get_weather_forecast",
                        {"city": "Evanston", "start_date": "2026-09-07", "end_date": "2026-09-07"},
                    )
                ]
            ),
            completion(
                tool_calls=[
                    tool_call(
                        "get_weather_forecast",
                        {"city": "Oak Park", "start_date": "2026-09-07", "end_date": "2026-09-07"},
                    )
                ]
            ),
            completion(
                tool_calls=[
                    tool_call(
                        "get_weather_forecast",
                        {"city": "Skokie", "start_date": "2026-09-07", "end_date": "2026-09-07"},
                    )
                ]
            ),
            completion(content=ITINERARY),
        ]
    )

    await plan_trip("weather near Chicago", client=llm, model="test-model", max_tool_rounds=4)

    notices = [
        request
        for request in llm.requests
        if any(LAST_ROUND_NOTICE in (m.get("content") or "") for m in request["messages"])
    ]
    assert len(notices) >= 1


async def test_the_first_round_is_not_a_last_round(counting_tool) -> None:
    """With a one-round budget the opening turn is also the final one, but telling the
    model to wrap up before it has asked anything would defeat the tool loop entirely."""
    llm = FakeLLM(
        [
            completion(
                tool_calls=[
                    tool_call(
                        "get_weather_forecast",
                        {"city": "Chicago", "start_date": "2026-09-07", "end_date": "2026-09-07"},
                    )
                ]
            ),
            completion(content=ITINERARY),
        ]
    )

    await plan_trip("weather in Chicago", client=llm, model="test-model", max_tool_rounds=1)

    opening = llm.requests[0]["messages"]
    assert not any(LAST_ROUND_NOTICE in (m.get("content") or "") for m in opening)


# --- the conversation stays well-formed -------------------------------------------
#
# Both of these were found by a live eval case dying on a 400, not by the suite.


async def test_the_history_declares_exactly_the_calls_that_get_answered(counting_tool) -> None:
    """Every tool call an assistant message declares must come back with a matching tool
    reply. Trimming the executed calls without trimming the message leaves the model
    promising twenty and delivering sixteen -- a malformed conversation, which the
    endpoint rejects on the *next* request rather than this one."""
    llm = FakeLLM([flood(MAX_TOOL_CALLS + 6), completion(content=ITINERARY)])

    await plan_trip("weather in Chicago", client=llm, model="test-model")

    sent = llm.requests[1]["messages"]
    declared = [
        call["id"]
        for message in sent
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    ]
    answered = [m["tool_call_id"] for m in sent if m.get("role") == "tool"]

    assert declared, "the assistant turn should still carry the calls it did make"
    assert sorted(declared) == sorted(answered)


async def test_a_turn_that_said_nothing_is_not_sent_back(counting_tool) -> None:
    """An assistant message with neither content nor tool calls is rejected outright:
    `400 ... assistant must provide content, reasoning_content or tool_calls`. It killed
    a whole eval case. It also carries no information, so it is simply left out."""
    llm = FakeLLM([completion(content=""), completion(content=ITINERARY)])

    await plan_trip("weather in Chicago", client=llm, model="test-model")

    for request in llm.requests:
        for message in request["messages"]:
            if message.get("role") != "assistant":
                continue
            assert message.get("content") or message.get("tool_calls"), message
