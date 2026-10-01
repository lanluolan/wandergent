"""Run the eval case set against a live model and report.

Reading one smoke run finds real bugs but cannot answer "is this better or worse than last
time", which is the only question that matters when tuning prompts or swapping models.

    cd backend
    uv run python -m evals.run                 # the smoke subset (default; costs tokens)
    uv run python -m evals.run --all
    uv run python -m evals.run --case exclusions --case budget-tight
    uv run python -m evals.run --json report.json

Exits non-zero if any check fails, so this can gate a change.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from app.agent.orchestrator import PlanningError, plan_trip
from app.agent.results import ToolUsage, Usage
from app.config import settings
from app.memory.store import PreferenceStore
from app.tools import memory as memory_tool
from evals.cases import Case, select


def tree_hash(root: Path) -> str:
    return hashlib.sha256(
        b"".join(path.read_bytes() for path in sorted(root.rglob("*.py")))
    ).hexdigest()


def diagnostic_snapshot(result) -> dict:
    """A text-free trace shape that can also read pre-change PlanResult objects."""
    plan = result.itinerary
    constraints = getattr(result, "constraints", None)
    return {
        "schema_version": 2,
        "trace_id": getattr(result, "trace_id", None),
        "days": [
            [
                {
                    "start_time": activity.start_time,
                    "end_time": activity.end_time,
                    "estimated_cost": activity.estimated_cost,
                    "category": activity.category,
                    "travel_mode": getattr(activity, "travel_mode", None),
                }
                for activity in day.activities
            ]
            for day in plan.days
        ]
        if plan
        else [],
        "budget": getattr(constraints, "budget", None),
        "total_estimated_cost": plan.total_estimated_cost if plan else None,
        "violations": [violation.code for violation in result.validation.violations]
        if result.validation
        else [],
        "tool_failures": sum(not call.ok for call in result.tool_calls),
        "tool_usage": result.tool_usage.model_dump(),
    }


@dataclass
class CaseReport:
    id: str
    passed: bool = False
    failures: dict[str, str] = field(default_factory=dict)
    checks_run: int = 0
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    tool_usage: dict = field(default_factory=dict)
    error: str | None = None
    turns: list[dict] = field(default_factory=list)
    failure_categories: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    cost_note: str = "Unknown: no operator-supplied price schedule; excludes Maps charges."


async def run_case(
    case: Case, *, today: date | None = None, memory: PreferenceStore | None = None
) -> CaseReport:
    report = CaseReport(id=case.id)
    started = time.monotonic()
    usage = Usage()
    tool_usage = ToolUsage()
    # The CLI injects an isolated store; tests also replace this module's binding.
    memory = memory or memory_tool.default_store
    if case.user_id:
        await memory.forget(case.user_id)

    async def run(request: str, previous_result=None):
        nonlocal tool_usage, usage
        previous = previous_result.itinerary if previous_result is not None else None
        previous_constraints = getattr(previous_result, "constraints", None)
        kwargs = {
            "user_id": case.user_id,
            "previous": previous,
            "today": today,
            "memory": memory,
        }
        parameters = inspect.signature(plan_trip).parameters
        if "previous_constraints" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            kwargs["previous_constraints"] = previous_constraints
        result = await plan_trip(request, **kwargs)
        usage = usage.plus(result.usage)
        tool_usage = tool_usage.plus(result.tool_usage)
        report.turns.append(diagnostic_snapshot(result))
        if any(not call.ok for call in result.tool_calls):
            report.failure_categories.append("tool_error")
        return result

    def grade(checks, result, prefix: str, before=None):
        for check in checks:
            report.checks_run += 1
            reason = check(result) if before is None else check(before, result)
            if reason is not None:
                report.failures[f"{prefix}/{check.name}"] = reason

    try:
        if case.setup_request:
            await run(case.setup_request)
        result = await run(case.request)
        grade(case.checks, result, "initial")
        if case.revise_request:
            if result.itinerary is None:
                report.failures["revision"] = "no itinerary to revise"
            else:
                revised = await run(case.revise_request, result)
                grade(case.revision_checks, revised, "revision", before=result)
                result = revised
        for index, step in enumerate(case.steps, 1):
            if result.itinerary is None and not step.new_trip:
                report.failures[f"step{index}"] = "no itinerary to revise"
                break
            revised = await run(step.request, None if step.new_trip else result)
            grade(step.checks, revised, f"step{index}")
            grade(step.revision_checks, revised, f"step{index}", before=result)
            result = revised
    except PlanningError as exc:
        # Exception strings can contain upstream URLs or headers. Store only the type.
        cause = exc.__cause__
        report.error = type(exc).__name__ + "/" + type(cause).__name__
        status = getattr(cause, "status_code", None)
        if status is not None:
            report.error += f"/HTTP{status}"
        report.failure_categories.append("model_error")
    finally:
        report.seconds = time.monotonic() - started
        report.usage = usage.model_dump()
        report.tool_usage = tool_usage.model_dump()
    if report.failures:
        # This is a triage candidate, not proof of which component caused the failure.
        report.failure_categories.append("needs_review")
    report.failure_categories = sorted(set(report.failure_categories))
    report.passed = not report.failures and report.error is None
    return report


def render(reports: list[CaseReport]) -> None:
    print()
    print(
        f"{'case':<26} {'result':<8} {'checks':<8} {'time':>7} {'llm':>5} "
        f"{'tools':>7} {'hit':>5} {'tokens':>8}"
    )
    print("-" * 82)

    total = Usage()
    total_tools = ToolUsage()
    for report in reports:
        # total_tokens is computed, so it cannot be fed back into the constructor.
        usage = Usage(**{k: v for k, v in report.usage.items() if k != "total_tokens"})
        total = total.plus(usage)
        raw_tools = {k: v for k, v in report.tool_usage.items() if k != "cache_hit_rate"}
        tools = ToolUsage(**raw_tools)
        total_tools = total_tools.plus(tools)
        outcome = "PASS" if report.passed else ("ERROR" if report.error else "FAIL")
        passed_count = report.checks_run - len(report.failures)
        print(
            f"{report.id:<26} {outcome:<8} {f'{passed_count}/{report.checks_run}':<8}"
            f" {report.seconds:>6.1f}s {usage.llm_calls:>5} "
            f"{f'{tools.executed_calls}/{tools.requested_calls}':>7} "
            f"{tools.cache_hit_rate:>4.0%} {usage.total_tokens:>8}"
        )

    print("-" * 82)
    passed = sum(1 for report in reports if report.passed)
    print(
        f"{passed}/{len(reports)} cases passed | "
        f"{total.llm_calls} LLM calls | {total.total_tokens} tokens "
        f"({total.cached_prompt_tokens} cached prompt, {total.reasoning_tokens} reasoning)"
    )
    print(
        f"tools: {total_tools.executed_calls}/{total_tools.requested_calls} executed, "
        f"{total_tools.cache_hits} cache hits ({total_tools.cache_hit_rate:.0%}), "
        f"{total_tools.contributed_calls} observably used, "
        f"{total_tools.retried_calls} retries, {total_tools.failed_calls} failures"
    )
    if reports and all(report.cost_usd is not None for report in reports):
        print(f"estimated model cost: ${sum(report.cost_usd for report in reports):.4f}")

    if len(total.tokens_by_model) > 1:
        # With routing on, the total alone hides the point: the same work, spread over
        # a cheaper model.
        split = "  ".join(
            f"{model}={tokens} ({tokens / total.total_tokens:.0%})"
            for model, tokens in sorted(total.tokens_by_model.items(), key=lambda item: -item[1])
        )
        print(f"tokens by model: {split}")

    for report in reports:
        if report.error:
            print(f"\n{report.id}: ERROR {report.error}")
        elif report.failures:
            print(f"\n{report.id}:")
            for name, reason in report.failures.items():
                print(f"  x {name} -- {reason}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="run every case, not just smoke")
    parser.add_argument("--case", action="append", dest="ids", help="run a specific case by id")
    parser.add_argument("--json", dest="json_path", help="also write the report as JSON")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=date.today(),
        help="fixed reference date for both baseline and candidate",
    )
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--cached-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--pricing-source")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue a matching partial --json report after the last completed case",
    )
    args = parser.parse_args()
    prices = (args.input_usd_per_million, args.cached_usd_per_million, args.output_usd_per_million)
    if any(p is not None for p in prices) and (
        any(p is None for p in prices) or any(p < 0 for p in prices if p is not None)
    ):
        parser.error("supply all three non-negative token prices, or none")

    missing = [
        name
        for name, value in (
            ("OPENAI_API_KEY", settings.openai_api_key),
            ("OPENAI_BASE_URL", settings.openai_base_url),
            ("OPENAI_MODEL", settings.openai_model),
        )
        if not value
    ]
    if missing:
        # Only the key has no default, but a box that blanked either of the others in
        # .env should hear about all three at once rather than one rerun at a time.
        print(f"{', '.join(missing)} not set. Put them in backend/.env, then rerun.")
        return 1

    cases = select(tag=None if args.all else "smoke", ids=args.ids)
    print(f"model: {settings.openai_model}")
    print(f"running {len(cases)} case(s): {', '.join(case.id for case in cases)}")
    print("this calls a real model and costs tokens.")

    # A repository-local database, never the application's preference database.
    scratch = Path(".eval")
    await asyncio.to_thread(scratch.mkdir, exist_ok=True)
    memory = PreferenceStore(scratch / "memory.db")
    memory_tool.default_store = memory
    reports: list[CaseReport] = []
    revision_result = await asyncio.to_thread(
        subprocess.run,
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    revision = revision_result.stdout.strip()
    worktree = await asyncio.to_thread(
        subprocess.run,
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    implementation_hash, harness_hash = await asyncio.gather(
        asyncio.to_thread(tree_hash, Path("app")),
        asyncio.to_thread(tree_hash, Path("evals")),
    )
    source_hash = hashlib.sha256((implementation_hash + harness_hash).encode()).hexdigest()
    suite_hash = await asyncio.to_thread(
        lambda: hashlib.sha256(
            b"".join(Path(f"evals/{name}.py").read_bytes() for name in ("cases", "checks"))
        ).hexdigest()
    )
    metadata = {
        "schema_version": 2,
        "revision": revision,
        "worktree_dirty": bool(worktree.stdout.strip()),
        "source_sha256": source_hash,
        "implementation_sha256": implementation_hash,
        "harness_sha256": harness_hash,
        "suite_sha256": suite_hash,
        "model": settings.openai_model,
        "fast_model": settings.fast_model,
        "today": str(args.today),
        "started_at": datetime.now(UTC).isoformat(),
        "case_ids": [case.id for case in cases],
        "maps_enabled": bool(settings.google_maps_api_key),
        "token_prices_usd_per_million": prices,
        "pricing_source": args.pricing_source,
    }

    if args.resume:
        resume_target = Path(args.json_path) if args.json_path else None
        if resume_target is None or not await asyncio.to_thread(resume_target.exists):
            parser.error("--resume requires an existing --json report")
        existing = json.loads(
            await asyncio.to_thread(resume_target.read_text, encoding="utf-8-sig")
        )
        comparable = (
            "source_sha256",
            "suite_sha256",
            "model",
            "fast_model",
            "today",
            "case_ids",
            "maps_enabled",
            "token_prices_usd_per_million",
            "pricing_source",
        )
        changed = [key for key in comparable if existing.get(key) != metadata.get(key)]
        if changed:
            parser.error(f"partial report does not match current run: {', '.join(changed)}")
        reports = [CaseReport(**item) for item in existing.get("reports", [])]
        completed_ids = [report.id for report in reports]
        if completed_ids != [case.id for case in cases[: len(reports)]]:
            parser.error("partial report cases are not a prefix of the selected suite")
        metadata["started_at"] = existing.get("started_at", metadata["started_at"])
        print(f"resuming after {len(reports)} completed case(s).")

    def save() -> None:
        if not args.json_path:
            return
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **metadata,
            "completed": len(reports) == len(cases),
            "passed": bool(reports) and all(r.passed for r in reports),
            "total_cost_usd": (
                sum(report.cost_usd for report in reports)
                if reports and all(report.cost_usd is not None for report in reports)
                else None
            ),
            "reports": [asdict(report) for report in reports],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    await asyncio.to_thread(save)
    for case in cases[len(reports) :]:
        print(f"\n-> {case.id}", flush=True)
        report = await run_case(case, today=args.today, memory=memory)
        used_models = set(report.usage["tokens_by_model"])
        if all(price is not None for price in prices) and used_models <= {settings.openai_model}:
            u = report.usage
            report.cost_usd = (
                (u["prompt_tokens"] - u["cached_prompt_tokens"]) * prices[0]
                + u["cached_prompt_tokens"] * prices[1]
                + u["completion_tokens"] * prices[2]
            ) / 1_000_000
            report.cost_note = (
                "Estimated from operator-supplied base text rates; excludes Maps charges "
                "and any long-context multiplier."
            )
        elif all(price is not None for price in prices):
            report.cost_note = "Unknown: the run used multiple models without per-model prices."
        print(f"   {'PASS' if report.passed else 'FAIL'} in {report.seconds:.1f}s", flush=True)
        reports.append(report)
        await asyncio.to_thread(save)

    render(reports)

    return 0 if all(report.passed for report in reports) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
