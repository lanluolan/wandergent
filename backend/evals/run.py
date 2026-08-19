"""Run the eval case set against a live model and report.

Every prompt, tool or orchestration change until now was verified by reading one smoke
run. That found real bugs, but it cannot answer "is this better or worse than last
time", which is the only question that matters when tuning prompts or swapping models
for cheaper ones in Phase 4.

    cd backend
    uv run python -m evals.run                 # the smoke subset (default; costs tokens)
    uv run python -m evals.run --all
    uv run python -m evals.run --case exclusions --case budget-tight
    uv run python -m evals.run --json report.json

Exits non-zero if any check fails, so this can gate a change.
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.agent.orchestrator import PlanningError, plan_trip
from app.agent.results import Usage
from app.config import settings
from app.memory import store
from evals.cases import Case, select


@dataclass
class CaseReport:
    id: str
    passed: bool = False
    failures: dict[str, str] = field(default_factory=dict)
    checks_run: int = 0
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    error: str | None = None


async def run_case(case: Case) -> CaseReport:
    report = CaseReport(id=case.id)

    # Memory cases must not inherit state from an earlier run of the same case.
    if case.user_id:
        await store.forget(case.user_id)

    started = time.monotonic()
    revised = None
    try:
        if case.setup_request:
            await plan_trip(case.setup_request, user_id=case.user_id)
        result = await plan_trip(case.request, user_id=case.user_id)
        if case.revise_request and result.itinerary is not None:
            # The revision is a second live run, seeded with the plan the first one
            # produced -- exactly what the client sends. Its tokens are part of this
            # case's cost, so they are summed rather than reported separately.
            revised = await plan_trip(
                case.revise_request,
                user_id=case.user_id,
                previous=result.itinerary,
            )
    except PlanningError as exc:
        report.error = str(exc)
        report.seconds = time.monotonic() - started
        return report

    report.seconds = time.monotonic() - started
    usage = result.usage.plus(revised.usage) if revised is not None else result.usage
    report.usage = usage.model_dump()
    report.checks_run = len(case.checks) + len(case.revision_checks)

    for check in case.checks:
        reason = check(result)
        if reason is not None:
            report.failures[check.name] = reason

    if case.revise_request:
        if revised is None:
            # The first plan failed, so there was nothing to edit. Say that rather than
            # letting every revision check fail with its own confusing reason.
            report.failures["revision"] = "the first plan produced no itinerary to revise"
        else:
            for revision_check in case.revision_checks:
                reason = revision_check(result, revised)
                if reason is not None:
                    report.failures[revision_check.name] = reason

    report.passed = not report.failures
    return report


def render(reports: list[CaseReport]) -> None:
    print()
    print(f"{'case':<26} {'result':<8} {'checks':<8} {'time':>7} {'calls':>6} {'tokens':>8}")
    print("-" * 68)

    total = Usage()
    for report in reports:
        # total_tokens is computed, so it cannot be fed back into the constructor.
        usage = Usage(**{k: v for k, v in report.usage.items() if k != "total_tokens"})
        total = total.plus(usage)
        outcome = "PASS" if report.passed else ("ERROR" if report.error else "FAIL")
        passed_count = report.checks_run - len(report.failures)
        print(
            f"{report.id:<26} {outcome:<8} {f'{passed_count}/{report.checks_run}':<8}"
            f" {report.seconds:>6.1f}s {usage.llm_calls:>6} {usage.total_tokens:>8}"
        )

    print("-" * 68)
    passed = sum(1 for report in reports if report.passed)
    print(
        f"{passed}/{len(reports)} cases passed | "
        f"{total.llm_calls} LLM calls | {total.total_tokens} tokens "
        f"({total.cached_prompt_tokens} cached prompt, {total.reasoning_tokens} reasoning)"
    )

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
    args = parser.parse_args()

    if not settings.openai_api_key:
        print("OPENAI_API_KEY is empty. Put your key in backend/.env, then rerun.")
        return 1

    cases = select(tag=None if args.all else "smoke", ids=args.ids)
    print(f"model: {settings.openai_model} @ {settings.openai_base_url}")
    print(f"running {len(cases)} case(s): {', '.join(case.id for case in cases)}")
    print("this calls a real model and costs tokens.")

    reports = []
    for case in cases:
        print(f"\n-> {case.id}")
        report = await run_case(case)
        print(f"   {'PASS' if report.passed else 'FAIL'} in {report.seconds:.1f}s")
        reports.append(report)

    render(reports)

    if args.json_path:
        payload = json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2)
        await asyncio.to_thread(Path(args.json_path).write_text, payload, encoding="utf-8")
        print(f"\nwrote {args.json_path}")

    return 0 if all(report.passed for report in reports) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
