"""Manual smoke test for the streaming path, against a live model.

Prints every event with the seconds elapsed since the request started, which is the
number that matters: the whole point of Phase 2 is that something useful appears long
before the itinerary is finished.

    cd backend
    uv run python -m scripts.smoke_stream
    uv run python -m scripts.smoke_stream "3 days in Boston, budget 60000 JPY, temples"
"""

import asyncio
import sys
import time

from app.agent.orchestrator import PlanningError, stream_plan
from app.config import settings

DEFAULT_REQUEST = "3 days in Chicago next week, budget 3000, I like food and history, no hiking"


async def main() -> int:
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is empty. Put your key in backend/.env, then rerun.")
        return 1

    request = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REQUEST
    print(f"model: {settings.openai_model} @ {settings.openai_base_url}")
    print(f"request: {request}\n")

    started = time.monotonic()
    first_event_at: float | None = None
    result = None

    try:
        async for event in stream_plan(request):
            elapsed = time.monotonic() - started
            if first_event_at is None:
                first_event_at = elapsed

            if event.type == "stage":
                print(f"[{elapsed:6.1f}s] STAGE     {event.name}: {event.message}")
            elif event.type == "tool_call":
                print(f"[{elapsed:6.1f}s] TOOL ->   {event.name}{event.arguments}")
            elif event.type == "tool_result":
                status = "ok" if event.ok else f"DEGRADED: {event.error}"
                print(f"[{elapsed:6.1f}s] TOOL <-   {event.name}: {status}")
            elif event.type == "composing":
                print(f"[{elapsed:6.1f}s] WRITING   {event.message}")
            elif event.type == "validation":
                if not event.violations:
                    print(f"[{elapsed:6.1f}s] CHECK     passed")
                for violation in event.violations or []:
                    print(f"[{elapsed:6.1f}s] CHECK !   {violation.code}: {violation.message}")
            elif event.type == "result":
                result = event.result
                print(f"[{elapsed:6.1f}s] RESULT")
    except PlanningError as exc:
        print(f"\nplanning failed: {exc}")
        return 1

    total = time.monotonic() - started
    print(f"\nfirst event after {first_event_at:.1f}s, finished in {total:.1f}s")

    if result is None or result.itinerary is None:
        print("no valid itinerary")
        return 1

    itinerary = result.itinerary
    print(
        f"{itinerary.destination} {itinerary.start_date}..{itinerary.end_date} | "
        f"{itinerary.total_estimated_cost:.0f}/{itinerary.budget or 0:.0f} {itinerary.currency} | "
        f"{len(itinerary.days)} days"
    )
    for warning in result.warnings:
        print(f"[warning] {warning.code}: {warning.detail}")
    # No longer folded into `warnings`, so it has to be printed in its own right.
    report = result.validation
    if report is not None:
        for violation in report.violations:
            kind = "note" if violation.advisory else "unresolved"
            print(f"[{kind}] {violation.code}: {violation.message}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
