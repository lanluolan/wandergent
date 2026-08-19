"""Manual end-to-end check of the memory loop, against a live model.

Two runs for one user:

1. A request that reveals something durable ("I don't want to hike"). The agent should
   call `remember_preference` on its own, off the back of the tool loop it was already
   running -- no extra LLM call.
2. A different city, with the preference **not** mentioned. It should come back out of
   the store and into the system prompt, and the plan should respect it unprompted.

    cd backend && uv run python -m scripts.smoke_memory
"""

import asyncio
import sys

from app.agent.orchestrator import stream_plan
from app.config import settings
from app.memory import store

USER = "smoke-memory-user"
FIRST = "2 days in Chicago, budget 1000, no hiking, I like museums"
SECOND = "2 days in Denver, budget 1000"


async def run(request: str, label: str) -> None:
    print(f"\n=== {label} ===")
    print(f"request: {request}")
    result = None
    async for event in stream_plan(request, user_id=USER):
        if event.type == "tool_call":
            print(f"  TOOL -> {event.name}{event.arguments}")
        elif event.type == "tool_result":
            print(f"  TOOL <- {event.name}: {'ok' if event.ok else event.error}")
        elif event.type == "result":
            result = event.result

    if result is None or result.itinerary is None:
        print("  (no itinerary)")
        return
    itinerary = result.itinerary
    print(f"  {itinerary.destination} | {itinerary.total_estimated_cost:.0f} {itinerary.currency}")
    for day in itinerary.days:
        titles = ", ".join(activity.title for activity in day.activities)
        print(f"   {day.date}: {titles}")


async def main() -> int:
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is empty. Put your key in backend/.env, then rerun.")
        return 1

    removed = await store.forget(USER)
    print(f"cleared {removed} old preference(s) for {USER}")

    await run(FIRST, "run 1 - states a preference")
    remembered = [preference.text for preference in await store.recall(USER)]
    print(f"\nstored after run 1: {remembered or '(nothing)'}")

    if not remembered:
        print("the agent did not remember anything; run 2 would prove nothing")
        return 1

    await run(SECOND, "run 2 - different city, preference NOT mentioned")
    print("\nDoes run 2's plan respect the remembered preference? Read the activities above.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
