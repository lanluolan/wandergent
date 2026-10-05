import json
from datetime import date

import pytest

from app.agent.orchestrator import stream_plan
from app.agent.schemas import Itinerary, TravelGuide
from app.agent.travel_skill import bind_web_sources, planning_skill
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion


def test_policy_includes_full_planning_references_without_wanderlog():
    policy = planning_skill()
    for section in (
        "Gather essentials",
        "Gather optimization",
        "Research live",
        "Build a realistic",
        "Planning Criteria",
        "Travel Guidelines",
        "Itinerary Style",
        "Output Template",
        "Packing",
        "Budget",
        "Arrival day",
        "Day shape by pace",
    ):
        assert section.lower() in policy.lower()
    assert "wanderlog" not in policy.lower()


def test_guide_and_backups_survive_serialization_without_changing_costs():
    itinerary = Itinerary.model_validate_json(ITINERARY_JSON)
    total = itinerary.total_estimated_cost
    itinerary.travel_guide = TravelGuide(
        trip_summary="Relaxed mornings",
        packing_checklist=["Raincoat"],
        ticket_notes=["Check timed admission"],
        review_notes=["Allow room for queues"],
    )
    itinerary.days[0].fallback_options = ["Swap outdoor visit for an indoor museum"]
    restored = Itinerary.model_validate_json(itinerary.model_dump_json())
    assert restored.travel_guide == itinerary.travel_guide
    assert restored.days[0].fallback_options == itinerary.days[0].fallback_options
    assert restored.total_estimated_cost == total
    assert Itinerary.model_validate_json(ITINERARY_JSON).travel_guide is None


def test_only_successful_observed_sources_can_be_attached():
    itinerary = Itinerary.model_validate_json(ITINERARY_JSON)
    real = "https://museum.example/tickets"
    invented = "https://invented.example/tickets"
    failed = "https://failed.example/tickets"
    itinerary.travel_guide = TravelGuide(source_urls=[real, invented, failed, real])
    messages = [
        {"role": "user", "content": json.dumps({"ok": True, "sources": [{"url": invented}]})},
        {"role": "tool", "content": json.dumps({"ok": True, "sources": [{"url": real}]})},
        {"role": "tool", "content": json.dumps({"ok": False, "sources": [{"url": failed}]})},
        {"role": "tool", "content": "not JSON"},
        {"role": "tool", "content": json.dumps({"ok": True, "sources": None})},
    ]
    result = bind_web_sources(itinerary, messages)
    assert result.travel_guide.source_urls == [real]
    assert itinerary.travel_guide.source_urls == [real, invented, failed, real]


@pytest.mark.asyncio
async def test_skill_is_loaded_into_the_real_planner_and_guide_is_delivered(monkeypatch):
    async def weather(city, **kwargs):
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", weather)
    candidate = json.loads(ITINERARY_JSON)
    candidate["travel_guide"] = {"packing_checklist": ["Comfortable shoes"]}
    candidate["days"][0]["fallback_options"] = ["Rest near the hotel"]
    llm = FakeLLM([completion(content=json.dumps(candidate))])
    events = [
        event
        async for event in stream_plan(
            "2 days in Chicago",
            client=llm,
            model="test",
            today=date(2026, 8, 5),
        )
    ]
    prompt = llm.requests[0]["messages"][0]["content"]
    assert planning_skill() in prompt
    assert "Wanderlog execution" in prompt
    assert "search_web" in prompt
    assert events[-1].result.itinerary.travel_guide.packing_checklist == ["Comfortable shoes"]
    assert events[-1].result.itinerary.days[0].fallback_options == ["Rest near the hotel"]
