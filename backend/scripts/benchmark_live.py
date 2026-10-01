"""Paid, randomized paired smoke benchmark with unchanged production graders.

Control serializes research tool execution and disables cross-run cache reads/writes.
Both arms retain run-local deduplication, budgets, retries, prompts and route validation.
Cache and preference stores are isolated before each arm. Two repetitions are a small
acceptance sample, not statistical proof about production traffic or provider billing.
"""

import argparse
import asyncio
import hashlib
import json
import random
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from unittest.mock import patch

from app.agent import orchestrator
from app.config import settings
from app.memory.store import PreferenceStore
from app.tools import memory as memory_tool
from app.tools.cache import shared_tool_cache
from evals.cases import select
from evals.run import run_case, tree_hash

CONTROL = "serial_no_shared_cache"
CURRENT = "current_runtime"


@contextmanager
def runtime_arm(control: bool):
    """Process-local experiment only; never alter application source or global config."""
    shared_tool_cache.clear()
    original = orchestrator._execute_tool_call
    gate = asyncio.Lock()

    async def serial(call, context):
        async with gate:
            return await original(call, context)

    try:
        if control:
            with (
                patch.object(orchestrator, "shared_cache_ttl", lambda _: 0),
                patch.object(orchestrator, "_execute_tool_call", serial),
            ):
                yield
        else:
            yield
    finally:
        shared_tool_cache.clear()


def summarize(rows: list[dict], expected_samples: int) -> dict:
    grouped = {}
    for row in rows:
        grouped.setdefault((row["repetition"], row["case_id"]), {})[row["arm"]] = row["report"]
    pairs = [pair for pair in grouped.values() if set(pair) == {CONTROL, CURRENT}]
    ratios = [pair[CURRENT]["seconds"] / pair[CONTROL]["seconds"] for pair in pairs]
    arms = {}
    for arm in (CONTROL, CURRENT):
        reports = [row["report"] for row in rows if row["arm"] == arm]
        arms[arm] = {
            "samples": len(reports),
            "passed_cases": sum(report["passed"] for report in reports),
            "checks": sum(report["checks_run"] for report in reports),
            "failed_checks": sum(len(report["failures"]) for report in reports),
            "seconds": sum(report["seconds"] for report in reports),
            "llm_calls": sum(report["usage"]["llm_calls"] for report in reports),
            "tokens": sum(report["usage"]["total_tokens"] for report in reports),
            "executed_tools": sum(report["tool_usage"]["executed_calls"] for report in reports),
            "cache_hits": sum(report["tool_usage"]["cache_hits"] for report in reports),
            "failed_tools": sum(report["tool_usage"]["failed_calls"] for report in reports),
        }
    complete = len(rows) == expected_samples * 2 and len(pairs) == expected_samples
    quality_ok = complete and all(row["report"]["passed"] for row in rows)
    tools_lower = arms[CURRENT]["executed_tools"] < arms[CONTROL]["executed_tools"]
    latency_lower = (
        bool(ratios) and median(ratios) < 1 and arms[CURRENT]["seconds"] < arms[CONTROL]["seconds"]
    )
    return {
        "complete": complete,
        "arms": arms,
        "paired_seconds_ratios_current_over_control": ratios,
        "median_paired_seconds_ratio": median(ratios) if ratios else None,
        "quality_all_checks_pass_both_arms": quality_ok,
        "executed_tools_lower": tools_lower,
        "latency_lower": latency_lower,
        "sample_acceptance_passed": quality_ok and (tools_lower or latency_lower),
        "generalized_statistical_or_cost_claim": False,
    }


async def benchmark(cases, *, repetitions: int, today: date, target: Path) -> dict:
    def versions():
        return (
            tree_hash(Path("app")),
            hashlib.sha256(
                b"".join(Path(f"evals/{name}.py").read_bytes() for name in ("cases", "checks"))
            ).hexdigest(),
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )

    implementation_hash, suite_hash, harness_hash = await asyncio.to_thread(versions)
    report = {
        "kind": "paid_randomized_paired_live_small_sample",
        "model": settings.openai_model,
        "fast_model": settings.fast_model,
        "maps_enabled": bool(settings.google_maps_api_key),
        "today": str(today),
        "implementation_sha256": implementation_hash,
        "suite_sha256": suite_hash,
        "harness_sha256": harness_hash,
        "started_at": datetime.now(UTC).isoformat(),
        "case_ids": [case.id for case in cases],
        "repetitions_per_arm": repetitions,
        "order_seed": 42,
        "order_counterbalanced_per_case": True,
        "provider_sampling_and_fact_variance_remain": True,
        "cache_policy": "cold per arm/case; within-case cross-turn sharing only in current arm",
        "acceptance_rule": "all checks pass in both arms, and fewer executed research tools OR "
        "lower summed seconds and median paired seconds ratio < 1",
        "model_cost_usd": None,
        "cost_note": "No confirmed price schedule; Maps charges and billing unknown.",
        "rows": [],
    }
    rng = random.Random(42)
    orders = {}
    for case in cases:
        order = [CONTROL, CURRENT]
        rng.shuffle(order)
        orders[case.id] = order

    def save():
        report["summary"] = summarize(report["rows"], len(cases) * repetitions)
        if report.get("source_unchanged") is False:
            report["summary"]["sample_acceptance_passed"] = False
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    original_store = memory_tool.default_store
    try:
        await asyncio.to_thread(save)
        for repetition in range(repetitions):
            for case in cases:
                order = orders[case.id] if repetition % 2 == 0 else list(reversed(orders[case.id]))
                for arm in order:
                    print(f"-> pair {repetition + 1}/{repetitions} {case.id} {arm}", flush=True)
                    store = PreferenceStore(
                        target.parent / f"{target.stem}-{repetition}-{case.id}-{arm}.db"
                    )
                    memory_tool.default_store = store
                    with runtime_arm(arm == CONTROL):
                        result = await run_case(case, today=today, memory=store)
                    report["rows"].append(
                        {
                            "repetition": repetition,
                            "case_id": case.id,
                            "arm": arm,
                            "report": asdict(result),
                        }
                    )
                    await asyncio.to_thread(save)
                    print(
                        f"   {'PASS' if result.passed else 'FAIL'} {result.seconds:.1f}s "
                        f"{result.usage['llm_calls']} LLM calls",
                        flush=True,
                    )
    finally:
        memory_tool.default_store = original_store
        report["source_unchanged"] = (
            await asyncio.to_thread(tree_hash, Path("app"))
        ) == implementation_hash
        report["finished_at"] = datetime.now(UTC).isoformat()
        await asyncio.to_thread(save)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--case", action="append", dest="ids")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    target = args.json.resolve()
    workspace = Path(__file__).resolve().parents[2]
    if not target.is_relative_to(workspace) or target.exists():
        parser.error("Output must be a new workspace-local file")
    if args.repetitions < 2:
        parser.error("At least two repetitions required; one stochastic pair is insufficient")
    if not settings.openai_api_key or not settings.google_maps_api_key:
        parser.error("Existing model and Maps configuration is required")
    target.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(
        benchmark(
            select(tag=None if args.all else "smoke", ids=args.ids),
            repetitions=args.repetitions,
            today=args.today,
            target=target,
        )
    )
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["sample_acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
