"""Clarification is a terminal result, not a malformed itinerary to repair."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agent.constraints import TripConstraints
from app.agent.events import PlanEvent
from app.agent.orchestrator import plan_trip, stream_plan
from app.agent.results import Clarification, PlanContinuation, PlanResult
from app.main import app
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import DailyForecast, WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

TODAY = date(2026, 8, 5)
WEATHER = {"city": "Chicago", "start_date": "2026-08-06", "end_date": "2026-08-07"}
QUESTION = {"questions": ["What exact dates and how many travellers?"], "reason": "inputs"}


@pytest.fixture
def weather_calls(monkeypatch):
    calls = []

    async def fake(city, start_date, end_date, **kwargs):
        calls.append({"city": city, "start_date": start_date, "end_date": end_date})
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)
    return calls


async def test_clarification_tool_stops_even_a_routed_mixed_tool_turn(weather_calls):
    llm = FakeLLM(
        [
            completion(
                tool_calls=[
                    tool_call("get_weather_forecast", WEATHER),
                    tool_call("ask_clarification", QUESTION, call_id="question"),
                ]
            )
        ]
    )
    events = [
        event
        async for event in stream_plan(
            "Chicago next week",
            client=llm,
            model="strong",
            fast_model="cheap",
            today=TODAY,
        )
    ]
    assert weather_calls == []
    assert len(llm.requests) == 1
    assert llm.requests[0]["tool_choice"] == "required"
    assert [e.type for e in events].count("result") == 1
    result = events[-1].result
    assert result.itinerary is None
    assert result.clarification.questions == QUESTION["questions"]
    assert result.warnings == []
    assert result.continuation.request == "Chicago next week"


@pytest.mark.parametrize("prefix", [[], [completion(content="")]])
async def test_json_clarification_bypasses_format_repairs(prefix):
    llm = FakeLLM([*prefix, completion(content=json.dumps({"clarification": QUESTION}))])
    result = await plan_trip("Chicago next week", client=llm, model="test", today=TODAY)
    assert result.clarification.questions == QUESTION["questions"]
    assert result.warnings == []
    assert len(llm.requests) == len(prefix) + 1


async def test_indoor_plan_cannot_skip_weather(weather_calls):
    llm = FakeLLM([completion(content=ITINERARY_JSON)])
    result = await plan_trip(
        "Chicago 2026-08-06 to 2026-08-07, 1 person, indoors only",
        client=llm,
        model="test",
        today=TODAY,
    )
    assert result.itinerary is not None
    assert weather_calls == [WEATHER]


async def test_forced_weather_facts_reach_the_model_before_finalizing(monkeypatch):
    async def rain(city, **kwargs):
        return WeatherForecast(
            ok=True,
            city=city,
            days=[
                DailyForecast(date=date(2026, 8, 6), condition="rain"),
            ],
        )

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", rain)
    llm = FakeLLM(
        [
            completion(content=ITINERARY_JSON),
            completion(content=ITINERARY_JSON),
        ]
    )
    result = await plan_trip("Chicago", client=llm, model="test", today=TODAY)
    assert result.itinerary is not None
    assert len(llm.requests) == 2
    tool_reply = llm.requests[1]["messages"][-1]
    assert tool_reply["role"] == "tool"
    assert '"condition":"rain"' in tool_reply["content"]
    assert len(result.tool_calls) == 1


async def test_uncovered_dates_stop_research_before_the_next_model_turn(weather_calls):
    future = {**WEATHER, "start_date": "2026-09-06", "end_date": "2026-09-07"}
    llm = FakeLLM([completion(tool_calls=[tool_call("get_weather_forecast", future)])])
    result = await plan_trip("Chicago in September", client=llm, model="test", today=TODAY)
    assert result.itinerary is None
    assert result.clarification.reason == "weather"
    assert result.continuation.weather_dates == ["2026-09-06", "2026-09-07"]
    assert len(llm.requests) == 1
    assert weather_calls == [future]


async def test_confirmation_resumes_original_request_and_labels_assumptions(weather_calls):
    plan = json.loads(ITINERARY_JSON)
    plan["start_date"], plan["end_date"] = "2026-09-06", "2026-09-07"
    for day, stamp in zip(plan["days"], ["2026-09-06", "2026-09-07"], strict=True):
        day["date"] = stamp
    context = PlanContinuation(
        request="Chicago 2026-09-06 to 2026-09-07, 1 person",
        questions=["Continue with seasonal weather?"],
        weather_dates=["2026-09-06", "2026-09-07"],
    )
    llm = FakeLLM([completion(content=json.dumps(plan))])
    result = await plan_trip(
        "Yes",
        continuation=context,
        weather_fallback_confirmed=True,
        client=llm,
        model="test",
        today=TODAY,
    )
    assert result.clarification is None
    assert result.itinerary is not None
    assert "assumed" in result.itinerary.notes[-1]
    assert all("not forecast" in day.weather for day in result.itinerary.days)
    assert "Chicago 2026-09-06" in llm.requests[0]["messages"][1]["content"]
    assert "Traveller reply:\nYes" in llm.requests[0]["messages"][1]["content"]
    assert len(weather_calls) == 1
    assert result.constraints.weather_fallback_dates == [date(2026, 9, 6), date(2026, 9, 7)]


async def test_confirmation_cannot_cover_changed_dates(weather_calls):
    context = PlanContinuation(
        request="Chicago",
        questions=["Continue?"],
        weather_dates=["2026-09-06"],
    )
    future = {**WEATHER, "start_date": "2026-10-06", "end_date": "2026-10-07"}
    llm = FakeLLM([completion(tool_calls=[tool_call("get_weather_forecast", future)])])
    result = await plan_trip(
        "Yes, but October instead",
        continuation=context,
        weather_fallback_confirmed=True,
        client=llm,
        model="test",
        today=TODAY,
    )
    assert result.clarification.reason == "weather"


async def test_confirmed_dates_survive_a_later_revision(weather_calls):
    future = {**WEATHER, "start_date": "2026-09-06", "end_date": "2026-09-07"}
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("get_weather_forecast", future)]),
            completion(content=json.dumps({"clarification": QUESTION})),
        ]
    )
    constraints = TripConstraints(weather_fallback_dates=[date(2026, 9, 6), date(2026, 9, 7)])
    result = await plan_trip(
        "Change lunch",
        previous_constraints=constraints,
        client=llm,
        model="test",
        today=TODAY,
    )
    assert result.clarification.reason == "inputs"
    assert len(llm.requests) == 2
    assert result.constraints.weather_fallback_dates == constraints.weather_fallback_dates


async def test_partial_forecast_requires_confirmation_only_for_uncovered_dates(weather_calls):
    partial = {**WEATHER, "start_date": "2026-08-19", "end_date": "2026-08-22"}
    llm = FakeLLM([completion(tool_calls=[tool_call("get_weather_forecast", partial)])])
    result = await plan_trip("Chicago", client=llm, model="test", today=TODAY)
    assert result.continuation.weather_dates == ["2026-08-21", "2026-08-22"]


async def test_new_trip_discards_pending_questions(weather_calls):
    context = PlanContinuation(request="Paris", questions=["Continue?"], weather_dates=[])
    llm = FakeLLM([completion(content=json.dumps({"clarification": QUESTION}))])
    result = await plan_trip(
        "New trip Chicago",
        continuation=context,
        client=llm,
        model="test",
        today=TODAY,
    )
    assert result.continuation.request == "New trip Chicago"
    assert "Paris" not in llm.requests[0]["messages"][1]["content"]


async def test_reply_overrides_original_request_constraints():
    context = PlanContinuation(request="Chicago, 1 person", questions=["How many travellers?"])
    llm = FakeLLM([completion(content=json.dumps({"clarification": QUESTION}))])
    result = await plan_trip(
        "2 people",
        continuation=context,
        client=llm,
        model="test",
        today=TODAY,
    )
    assert result.constraints.travelers == 2


@pytest.mark.parametrize("endpoint", ["/plan", "/plan/stream"])
def test_both_endpoints_forward_continuation_and_consent(monkeypatch, endpoint):
    seen = []
    result = PlanResult(clarification=Clarification(**QUESTION))

    async def plan(message, **kwargs):
        seen.append((message, kwargs))
        return result

    async def stream(message, **kwargs):
        yield PlanEvent(type="result", result=await plan(message, **kwargs))

    monkeypatch.setattr("app.main.plan_trip", plan)
    monkeypatch.setattr("app.main.stream_plan", stream)
    context = PlanContinuation(
        request="Chicago",
        questions=["Continue?"],
        weather_dates=["2026-11-01"],
    )
    with TestClient(app) as client:
        response = client.post(
            endpoint,
            json={
                "message": "Yes",
                "continuation": context.model_dump(),
                "weather_fallback_confirmed": True,
            },
        )
    assert response.status_code == 200
    assert seen[0][0] == "Yes"
    assert seen[0][1]["continuation"] == context
    assert seen[0][1]["weather_fallback_confirmed"] is True
    assert '"clarification"' in response.text
