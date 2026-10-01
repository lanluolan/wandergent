"""The validate -> repair -> re-validate loop in the orchestrator.

The validator itself is covered in test_validation.py. What matters here is what the
agent does with a violation: feed it back, re-check the answer rather than trusting it,
and be honest when the repair fails.
"""

import json
from datetime import date

import pytest

from app.agent.orchestrator import stream_plan
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import WeatherForecast
from tests.fakes import FakeLLM, completion

TODAY = date(2026, 8, 5)


def itinerary_json(cost: float, budget: float = 1000) -> str:
    return json.dumps(
        {
            "destination": "Chicago",
            "start_date": "2026-08-06",
            "end_date": "2026-08-06",
            "budget": budget,
            "days": [
                {
                    "date": "2026-08-06",
                    "summary": "One day",
                    "activities": [
                        {
                            "start_time": "09:00",
                            "end_time": "11:00",
                            "title": "Art Institute of Chicago",
                            "estimated_cost": cost,
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )


OVER_BUDGET = itinerary_json(cost=5000)
WITHIN_BUDGET = itinerary_json(cost=200)


@pytest.fixture(autouse=True)
def stub_weather(monkeypatch):
    async def fake(city: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)


async def collect(llm):
    return [
        event
        async for event in stream_plan(
            "1 day in Chicago", client=llm, model="test-model", today=TODAY
        )
    ]


async def test_violations_are_fed_back_and_the_repair_is_re_validated() -> None:
    llm = FakeLLM([completion(content=OVER_BUDGET), completion(content=WITHIN_BUDGET)])

    events = await collect(llm)

    validations = [event for event in events if event.type == "validation"]
    # First check fails, second passes -- proving the repaired plan was re-checked
    # rather than accepted on the model's word.
    assert [v.code for v in validations[0].violations] == ["over_budget"]
    assert validations[-1].violations == []

    # The repair prompt carried the actual numbers, not a vague "fix it".
    repair_prompt = llm.requests[1]["messages"][-1]["content"]
    assert "5000" in repair_prompt and "1000" in repair_prompt

    result = events[-1].result
    assert result.validation.ok
    assert result.itinerary.total_estimated_cost == 200.0
    assert result.warnings == []


async def test_a_failed_repair_ships_the_plan_with_honest_warnings() -> None:
    """The bounded retries stop. The plan goes out, but not as if it were sound."""
    llm = FakeLLM(
        [
            completion(content=OVER_BUDGET),
            completion(content=OVER_BUDGET),
            completion(content=OVER_BUDGET),
            completion(content=OVER_BUDGET),
        ]
    )

    events = await collect(llm)
    result = events[-1].result

    assert result.itinerary is not None
    assert not result.validation.ok
    assert any(
        v.code == "over_budget" and "over by" in v.message for v in result.validation.violations
    )
    # The unresolved finding rides in the report and nowhere else. `warnings` is about
    # what the *run* could not finish, and this run finished everything it attempted.
    assert result.warnings == []
    # Exactly three repair attempts, so a stubborn model cannot loop forever.
    assert len(llm.requests) == 4


async def test_a_second_repair_can_finish_an_imperfect_first_repair() -> None:
    still_over = itinerary_json(cost=1200)
    llm = FakeLLM(
        [
            completion(content=OVER_BUDGET),
            completion(content=still_over),
            completion(content=WITHIN_BUDGET),
        ]
    )

    events = await collect(llm)

    validations = [event for event in events if event.type == "validation"]
    assert [report.violations[0].code for report in validations[:2]] == [
        "over_budget",
        "over_budget",
    ]
    assert validations[-1].violations == []
    assert events[-1].result.validation.ok


async def test_a_malformed_repair_keeps_the_original_plan() -> None:
    llm = FakeLLM(
        [
            completion(content=OVER_BUDGET),
            completion(content="sorry, I cannot"),
            completion(content="invalid"),
        ]
    )

    events = await collect(llm)
    result = events[-1].result

    # Still the over-budget plan: well-formed and flagged beats malformed.
    assert result.itinerary.total_estimated_cost == 5000.0
    assert not result.validation.ok
    assert any(v.code == "over_budget" for v in result.validation.violations)
    assert len(llm.requests) == 3


async def test_format_correction_recovers_a_partial_repair_and_revalidates():
    llm = FakeLLM(
        [
            completion(content=OVER_BUDGET),
            completion(content='{"days":[]}'),
            completion(content=WITHIN_BUDGET),
        ]
    )
    events = await collect(llm)
    assert events[-1].result.validation.ok
    assert events[-1].result.itinerary.total_estimated_cost == 200
    assert len([e for e in events if e.type == "validation"]) == 2
    assert "COMPLETE itinerary" in llm.requests[2]["messages"][-1]["content"]
    assert "destination" in llm.requests[2]["messages"][-1]["content"]


async def test_format_correction_does_not_bypass_remaining_constraints():
    llm = FakeLLM(
        [
            completion(content=OVER_BUDGET),
            completion(content="invalid"),
            completion(content=OVER_BUDGET),
            completion(content=OVER_BUDGET),
            completion(content=OVER_BUDGET),
        ]
    )
    events = await collect(llm)
    assert not events[-1].result.validation.ok
    assert len(llm.requests) == 5  # initial + three constraint repairs + one format correction
    assert len([e for e in events if e.type == "validation"]) == 4


async def test_format_allowance_is_shared_across_constraint_repairs():
    llm = FakeLLM(
        [
            completion(content=OVER_BUDGET),
            completion(content="invalid"),
            completion(content=OVER_BUDGET),
            completion(content="invalid again"),
        ]
    )
    events = await collect(llm)
    assert len(llm.requests) == 4
    assert events[-1].result.itinerary.total_estimated_cost == 5000
    assert not events[-1].result.validation.ok


async def test_format_recovery_cannot_raise_request_owned_budget_to_pass():
    inflated = itinerary_json(cost=5000, budget=10000)
    llm = FakeLLM(
        [
            completion(content=OVER_BUDGET),
            completion(content="invalid"),
            completion(content=inflated),
            completion(content=inflated),
            completion(content=inflated),
        ]
    )
    events = [
        event
        async for event in stream_plan(
            "1 day in Chicago, budget 100 USD", client=llm, model="test-model", today=TODAY
        )
    ]
    assert events[-1].result.constraints.budget == 100
    assert not events[-1].result.validation.ok
    assert any(v.code == "over_budget" for v in events[-1].result.validation.violations)
    assert len(llm.requests) == 5


async def test_a_feasible_plan_skips_the_repair_round() -> None:
    llm = FakeLLM([completion(content=WITHIN_BUDGET)])

    events = await collect(llm)

    assert len(llm.requests) == 1
    assert [event.type for event in events if event.type == "validation"] == ["validation"]
    assert events[-1].result.validation.ok
