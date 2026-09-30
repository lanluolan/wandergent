"""The live evaluator must measure every turn and keep its report text-free."""

from pathlib import Path

from app.agent.constraints import TripConstraints
from app.agent.results import PlanResult, ToolUsage, Usage
from app.agent.schemas import Itinerary
from app.agent.validation import ValidationReport
from app.memory.store import PreferenceStore
from evals.cases import Case, Step
from evals.checks import produced_a_plan
from evals.run import run_case


def plan(title: str, budget: float = 100) -> Itinerary:
    return Itinerary.model_validate(
        {
            "destination": "Los Angeles",
            "start_date": "2026-10-20",
            "end_date": "2026-10-20",
            "budget": budget,
            "days": [
                {
                    "date": "2026-10-20",
                    "summary": "Private summary",
                    "activities": [
                        {
                            "start_time": "09:00",
                            "end_time": "10:00",
                            "title": title,
                            "location": "Private address",
                            "estimated_cost": 20,
                        }
                    ],
                }
            ],
        }
    )


async def test_setup_and_every_revision_are_counted_and_redacted(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict] = []

    async def fake(request: str, **kwargs) -> PlanResult:
        calls.append({"request": request, **kwargs})
        return PlanResult(
            itinerary=plan(f"Private title {len(calls)}"),
            constraints=kwargs.get("previous_constraints") or TripConstraints(budget=100),
            validation=ValidationReport(),
            usage=Usage(llm_calls=1, prompt_tokens=10, completion_tokens=2),
            tool_usage=ToolUsage(
                requested_calls=2,
                executed_calls=1,
                cache_hits=1,
                shared_cache_hits=1,
                contributed_calls=1,
                calls_by_tool={"search_places": 2},
            ),
        )

    monkeypatch.setattr("evals.run.plan_trip", fake)
    case = Case(
        id="chain",
        setup_request="Private setup request",
        request="Private initial request",
        user_id="isolated-eval-user",
        checks=[produced_a_plan()],
        revise_request="Private revision request",
        steps=(
            Step("Private third edit", (produced_a_plan(),)),
            Step("Private new trip", (produced_a_plan(),), new_trip=True),
        ),
    )

    report = await run_case(case, memory=PreferenceStore(tmp_path / "eval-memory.db"))

    assert report.passed
    assert report.usage["llm_calls"] == 5
    assert report.usage["total_tokens"] == 60
    assert report.tool_usage["requested_calls"] == 10
    assert report.tool_usage["executed_calls"] == 5
    assert report.tool_usage["shared_cache_hits"] == 5
    assert calls[2]["previous"] is not None
    assert calls[2]["previous_constraints"] == TripConstraints(budget=100)
    assert calls[-1]["previous"] is None
    encoded = str(report.turns)
    assert "Private title" not in encoded
    assert "Private address" not in encoded
    assert "Private setup request" not in encoded


async def test_error_report_keeps_only_type_status_and_category(monkeypatch) -> None:
    class PrivateUpstreamError(Exception):
        status_code = 503

    async def fail(*args, **kwargs):
        cause = PrivateUpstreamError("secret URL and response")
        from app.agent.orchestrator import PlanningError

        raise PlanningError("private model message") from cause

    monkeypatch.setattr("evals.run.plan_trip", fail)
    report = await run_case(Case(id="failure", request="private request", checks=[]))
    assert report.error == "PlanningError/PrivateUpstreamError/HTTP503"
    assert report.failure_categories == ["model_error"]
    assert "secret" not in str(report)
    assert "private request" not in str(report)
