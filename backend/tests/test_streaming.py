"""Streaming tests: event order, delta reassembly, and SSE framing."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agent.events import PlanEvent
from app.agent.orchestrator import PlanningTimeout, stream_plan
from app.agent.results import PlanResult, tool_calls_spent
from app.main import app
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

TODAY = date(2026, 8, 5)


@pytest.fixture
def stub_weather(monkeypatch):
    async def fake(city: str, start_date: str, end_date: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=True, city=city, resolved_name=city, days=[])

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)


def weather_turn():
    return completion(
        tool_calls=[
            tool_call(
                "get_weather_forecast",
                {"city": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-07"},
            )
        ]
    )


async def collect(llm, **kwargs) -> list[PlanEvent]:
    return [
        event
        async for event in stream_plan(
            "2 days in Chicago", client=llm, model="test-model", today=TODAY, **kwargs
        )
    ]


async def test_event_order_tells_the_story_of_the_run(stub_weather) -> None:
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    events = await collect(llm)
    types = [event.type for event in events]

    assert types[0] == "stage"
    assert types.index("tool_call") < types.index("tool_result")
    assert types[-1] == "result"
    # Exactly one terminal event, so a client can close on it.
    assert types.count("result") == 1


async def test_tool_arguments_are_reassembled_from_fragments(stub_weather) -> None:
    """The fake splits arguments into 8-character pieces; they must come back whole."""
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    events = await collect(llm)
    call = next(event for event in events if event.type == "tool_call")

    assert call.name == "get_weather_forecast"
    assert call.arguments == {
        "city": "Chicago",
        "start_date": "2026-08-06",
        "end_date": "2026-08-07",
    }


async def test_tool_result_carries_the_outcome(stub_weather, monkeypatch) -> None:
    async def failing(city: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=False, city=city, error="weather service timed out")

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", failing)
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    events = await collect(llm)
    result_event = next(event for event in events if event.type == "tool_result")

    assert result_event.ok is False
    assert result_event.error == "weather service timed out"


async def test_composing_events_name_what_is_being_written(stub_weather) -> None:
    """The point of streaming: name the place as it is written, not just spin."""
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    events = await collect(llm)
    hints = [event.message for event in events if event.type == "composing"]

    assert "Art Institute of Chicago" in hints
    assert "Shedd Aquarium" in hints
    # Each hint fires once, when it first appears -- no repeats on every chunk.
    assert len(hints) == len(set(hints))


async def test_result_event_carries_the_full_plan(stub_weather) -> None:
    llm = FakeLLM([weather_turn(), completion(content=ITINERARY_JSON)])

    events = await collect(llm)
    result = events[-1].result

    assert isinstance(result, PlanResult)
    assert result.itinerary is not None
    assert result.itinerary.total_estimated_cost == 290.0
    assert [call.name for call in result.tool_calls] == ["get_weather_forecast"]


async def test_upstream_failure_raises_rather_than_yielding(stub_weather) -> None:
    """The HTTP layer decides status codes, so the generator must not swallow this."""
    import httpx
    from openai import APITimeoutError

    llm = FakeLLM([APITimeoutError(httpx.Request("POST", "https://example.com"))])

    with pytest.raises(PlanningTimeout):
        await collect(llm)


# --- SSE endpoint -----------------------------------------------------------------


def parse_sse(body: str) -> list[dict]:
    """Split a raw SSE body into {event, data} records."""
    records = []
    for block in body.strip().split("\n\n"):
        fields: dict = {}
        for line in block.splitlines():
            key, _, value = line.partition(": ")
            if key == "data":
                fields["data"] = json.loads(value)
            elif key == "event":
                fields["event"] = value
        if fields:
            records.append(fields)
    return records


def test_sse_endpoint_frames_events(monkeypatch) -> None:
    async def fake_stream(message: str, **kwargs):
        yield PlanEvent(type="stage", name="understanding", message="Understanding your request")
        yield PlanEvent(
            type="tool_call", name="get_weather_forecast", arguments={"city": "Chicago"}
        )
        yield PlanEvent(type="result", result=PlanResult(warnings=[tool_calls_spent(16)]))

    monkeypatch.setattr("app.main.stream_plan", fake_stream)

    with TestClient(app) as client:
        response = client.post("/plan/stream", json={"message": "2 days in Chicago"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"

    records = parse_sse(response.text)
    assert [r["event"] for r in records] == ["stage", "tool_call", "result"]
    assert records[1]["data"]["arguments"] == {"city": "Chicago"}
    # Warnings cross the wire as objects, not prose: the client renders the code, and
    # `detail` is the fallback for a reader that does not know it.
    assert records[2]["data"]["result"]["warnings"] == [
        {
            "code": "tool_calls_spent",
            "detail": "stopped calling tools after 16 calls",
            "budget": 16,
            "dropped_calls": None,
            "dropped_tools": [],
        }
    ]


def test_sse_endpoint_reports_failures_as_a_terminal_event(monkeypatch) -> None:
    """The status line is long gone by then, so the failure has to ride the stream."""

    async def failing_stream(message: str, **kwargs):
        yield PlanEvent(type="stage", name="understanding")
        raise PlanningTimeout("the language model timed out")

    monkeypatch.setattr("app.main.stream_plan", failing_stream)

    with TestClient(app) as client:
        response = client.post("/plan/stream", json={"message": "2 days in Chicago"})

    records = parse_sse(response.text)
    assert response.status_code == 200
    assert [r["event"] for r in records] == ["stage", "error"]
    assert "timed out" in records[-1]["data"]["message"]


def test_sse_endpoint_validates_the_request() -> None:
    with TestClient(app) as client:
        assert client.post("/plan/stream", json={"message": ""}).status_code == 422
