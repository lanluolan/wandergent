"""Manual end-to-end smoke test: one real planning run against a live LLM.

Deliberately outside the pytest suite -- it costs tokens and needs the network,
whereas `uv run pytest` stays offline and free. This is the check to run after
putting a key in `backend/.env`, and after any prompt or tool change.

    cd backend
    uv run python -m scripts.smoke_plan
    uv run python -m scripts.smoke_plan "3 days in Seoul, budget 2000, I like museums, no hiking"

It prints which tools were called and whether they degraded, so a plan that came
out thin can be traced to a failing tool rather than guessed at.
"""

import asyncio
import sys

from app.agent.orchestrator import PlanningError, PlanResult, plan_trip
from app.config import settings

DEFAULT_REQUEST = "3 days in Chicago next week, budget 3000, I like food and history, no hiking"


def render(result: PlanResult) -> None:
    """Print a planning result in a shape that is quick to eyeball."""
    print("\n--- tool calls ---")
    if not result.tool_calls:
        print("  (none -- the model answered without calling a tool)")
    for call in result.tool_calls:
        status = "ok" if call.ok else f"DEGRADED: {call.error}"
        print(f"  {call.name}{call.arguments} -> {status}")

    # `detail` rather than the client's sentence: this script is read while debugging,
    # which is exactly the audience `detail` is written for.
    for warning in result.warnings:
        print(f"\n[warning] {warning.code}: {warning.detail}")

    # Printed here because it stopped being folded into `warnings`. Without this the
    # script would go quiet about the one thing it exists to check.
    report = result.validation
    if report is not None:
        for violation in report.violations:
            kind = "note" if violation.advisory else "unresolved"
            print(f"[{kind}] {violation.code}: {violation.message}")

    itinerary = result.itinerary
    if itinerary is None:
        print("\n--- no valid itinerary ---")
        print(result.raw_reply)
        return

    print(f"\n--- {itinerary.destination} {itinerary.start_date} .. {itinerary.end_date} ---")
    print(f"travelers: {itinerary.travelers}")
    budget = f"{itinerary.budget:.0f}" if itinerary.budget is not None else "not stated"
    print(
        f"budget: {budget} {itinerary.currency} | "
        f"estimated: {itinerary.total_estimated_cost:.0f} {itinerary.currency}"
    )

    for day in itinerary.days:
        print(f"\n{day.date}  {day.summary}  [{day.estimated_cost:.0f} {itinerary.currency}]")
        if day.weather:
            print(f"  weather: {day.weather}")
        for item in day.activities:
            indoor = {True: "indoor", False: "outdoor", None: "?"}[item.indoor]
            where = f" @ {item.location}" if item.location else ""
            print(
                f"  {item.start_time}-{item.end_time}  {item.title}{where}  "
                f"({item.category}, {indoor}, {item.estimated_cost:.0f})"
            )

    if itinerary.notes:
        print("\nnotes:")
        for note in itinerary.notes:
            print(f"  - {note}")


async def main() -> int:
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is empty. Put your key in backend/.env, then rerun.")
        return 1

    request = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REQUEST
    print(f"model: {settings.openai_model} @ {settings.openai_base_url}")
    print(f"request: {request}")

    try:
        result = await plan_trip(request)
    except PlanningError as exc:
        print(f"\nplanning failed: {exc}")
        return 1

    render(result)
    return 0 if result.itinerary is not None else 1


if __name__ == "__main__":
    # Windows consoles default to a codepage that cannot print Chinese place names.
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
