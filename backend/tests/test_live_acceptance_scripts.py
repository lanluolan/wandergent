"""Live gate harnesses are tested offline; no paid call is hidden in this suite."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date

import pytest

from app.agent import orchestrator
from app.agent.results import ToolUsage, Usage
from app.tools import memory as memory_tool
from evals.cases import Case
from evals.run import CaseReport
from scripts import benchmark_live, verify_live_hours
from scripts.benchmark_live import CONTROL, CURRENT, runtime_arm, summarize
from scripts.verify_acceptance_traces import trace_ids
from scripts.verify_live_hours import broken_schedule


def row(arm, *, seconds=10, tools=5, passed=True, repetition=0):
    report = CaseReport(id="case", passed=passed, checks_run=2, seconds=seconds)
    report.usage = Usage(llm_calls=2).model_dump()
    report.tool_usage = ToolUsage(executed_calls=tools).model_dump()
    if not passed:
        report.failures = {"feasible": "hard constraint"}
    return {"arm": arm, "case_id": "case", "repetition": repetition, "report": asdict(report)}


def test_live_summary_requires_quality_and_actual_improvement():
    rows = [row(CONTROL), row(CURRENT, seconds=8)]
    result = summarize(rows, 1)
    assert result["sample_acceptance_passed"]
    assert result["median_paired_seconds_ratio"] == 0.8
    assert not summarize([row(CONTROL), row(CURRENT, seconds=11)], 1)["sample_acceptance_passed"]
    assert not summarize(rows, 2)["sample_acceptance_passed"]
    assert not summarize([row(CONTROL), row(CURRENT, seconds=5, passed=False)], 1)[
        "sample_acceptance_passed"
    ]


def test_quality_failures_cannot_be_hidden_by_another_good_repetition():
    rows = [
        row(CONTROL, passed=False),
        row(CURRENT, seconds=8),
        row(CONTROL, repetition=1),
        row(CURRENT, seconds=8, repetition=1),
    ]
    assert not summarize(rows, 2)["sample_acceptance_passed"]
    assert not summarize(rows, 2)["quality_all_checks_pass_both_arms"]


def test_tools_reduction_is_eligible_without_asserting_a_latency_win():
    result = summarize([row(CONTROL), row(CURRENT, tools=4, seconds=12)], 1)
    assert result["sample_acceptance_passed"]
    assert result["executed_tools_lower"]
    assert not result["latency_lower"]
    assert not result["generalized_statistical_or_cost_claim"]


async def test_control_runtime_restores_every_patch_after_failure():
    original = orchestrator._execute_tool_call
    ttl = orchestrator.shared_cache_ttl
    with pytest.raises(ValueError), runtime_arm(True):
        assert orchestrator._execute_tool_call is not original
        assert orchestrator.shared_cache_ttl("search_places") == 0
        raise ValueError("fixture")
    assert orchestrator._execute_tool_call is original
    assert orchestrator.shared_cache_ttl is ttl
    with runtime_arm(False):
        assert orchestrator._execute_tool_call is original


async def test_live_benchmark_uses_same_grader_cases_isolated_memory_and_two_repetitions(
    monkeypatch, tmp_path
):
    seen = []
    original_store = memory_tool.default_store

    async def run(case, *, today, memory):
        control = orchestrator.shared_cache_ttl("search_places") == 0
        seen.append((case, today, memory))
        return CaseReport(
            **row(CONTROL if control else CURRENT, seconds=10 if control else 8)["report"]
        )

    monkeypatch.setattr(benchmark_live, "run_case", run)
    monkeypatch.setattr(benchmark_live, "tree_hash", lambda _: "fixed-source")
    case = Case("case", "fixture request", [])
    report = await benchmark_live.benchmark(
        [case], repetitions=2, today=date(2026, 9, 28), target=tmp_path / "result.json"
    )
    assert len(seen) == 4 and all(entry[0] is case for entry in seen)
    assert len({id(entry[2]) for entry in seen}) == 4
    assert memory_tool.default_store is original_store
    assert report["source_unchanged"] and report["summary"]["sample_acceptance_passed"]
    assert [entry["arm"] for entry in report["rows"]] == [CURRENT, CONTROL, CONTROL, CURRENT]


async def test_changed_source_cannot_be_accepted_as_a_controlled_benchmark(monkeypatch, tmp_path):
    hashes = iter(["before", "after"])
    monkeypatch.setattr(benchmark_live, "tree_hash", lambda _: next(hashes))

    async def run(case, **kwargs):
        control = orchestrator.shared_cache_ttl("search_places") == 0
        return CaseReport(
            **row(CONTROL if control else CURRENT, seconds=10 if control else 8)["report"]
        )

    monkeypatch.setattr(benchmark_live, "run_case", run)
    report = await benchmark_live.benchmark(
        [Case("case", "fixture", [])],
        repetitions=2,
        today=date(2026, 9, 28),
        target=tmp_path / "changed.json",
    )
    assert not report["source_unchanged"]
    assert not report["summary"]["sample_acceptance_passed"]


async def test_index_delay_retries_only_read_only_phoenix_requests(monkeypatch):
    import httpx

    trace_id = "a" * 32
    root = {
        "name": "plan",
        "parent_id": None,
        "status_code": "OK",
        "context": {"trace_id": trace_id, "span_id": "root"},
        "attributes": {"wandergent.implementation.sha256": "source"},
    }
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [] if len(calls) == 1 else [root]})

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        verify_live_hours.httpx,
        "AsyncClient",
        lambda **kwargs: factory(transport=httpx.MockTransport(handle), **kwargs),
    )
    spans = await verify_live_hours.persisted_spans(trace_id, "http://phoenix.test", {"plan"})
    assert spans == [root] and len(calls) == 2
    assert all(request.method == "GET" for request in calls)


def test_trace_audit_reads_release_pairs_and_fault_reports_without_silent_empty_success():
    turn = {"trace_id": "trace"}
    assert trace_ids({"reports": [{"turns": [turn, turn]}]}) == ["trace"]
    assert trace_ids({"rows": [{"report": {"turns": [turn]}}]}) == ["trace"]
    assert trace_ids({"result": turn}) == ["trace"]
    with pytest.raises(ValueError):
        trace_ids({"reports": []})


def museum():
    return {
        "name": "Observed Museum",
        "address": "1 Real St",
        "types": ["museum"],
        "opening_hours": ["Wednesday: 10:00-17:00"],
    }


def test_fault_is_derived_from_observed_hours_without_mutating_facts():
    value = museum()
    original = deepcopy(value)
    plan = broken_schedule(
        value, {"name": "Observed Park", "address": "2 Real St"}, date(2026, 10, 21)
    )
    assert plan.days[0].activities[1].start_time == "08:00"
    assert plan.days[0].activities[1].end_time == "09:00"
    assert plan.total_estimated_cost == 40 and plan.budget == 200
    assert value == original


def test_packed_prefix_fault_uses_real_closing_bound_not_fabricated_hours():
    value = museum()
    plan = broken_schedule(
        value, {"name": "Park", "address": "2 Real St"}, date(2026, 10, 21), packed_prefix=True
    )
    assert plan.days[0].activities[0].start_time == "16:00"
    assert plan.days[0].activities[0].end_time == "17:00"
    assert plan.days[0].activities[1].start_time == "18:00"
    assert plan.days[0].activities[1].end_time == "19:00"
    assert value["opening_hours"] == ["Wednesday: 10:00-17:00"]


@pytest.mark.parametrize(
    "change", [{"types": []}, {"opening_hours": []}, {"opening_hours": ["Wednesday: Closed"]}]
)
def test_fault_cannot_fabricate_a_usable_live_window(change):
    with pytest.raises(ValueError):
        broken_schedule(
            museum() | change, {"name": "Park", "address": "Address"}, date(2026, 10, 21)
        )
