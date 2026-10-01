"""Offline paired experiment: serialized/no-shared-cache control versus current runtime.

Requires the existing dev environment. Model replies and tool facts are fixed, external
APIs/export are disabled. This isolates scheduling/cache mechanics, NOT live model quality.
"""

import argparse
import asyncio
import json
import random
from datetime import date
from pathlib import Path
from statistics import median
from time import perf_counter

import pytest

from app import observability
from app.agent import orchestrator
from app.config import settings
from app.tools.cache import shared_tool_cache
from app.tools.maps import Place, PlacesResult
from app.tools.registry import TOOL_FUNCTIONS
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call


async def sample(control: bool, delay: float) -> dict:
    shared_tool_cache.clear()
    calls = 0
    gate = asyncio.Lock()
    original = orchestrator._execute_tool_call

    async def serial(call, context):
        async with gate:
            return await original(call, context)

    async def places(query, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(delay)
        return PlacesResult(ok=True, query=query, places=[Place(ok=True, name=query)])

    async def no_export(trace):
        return False

    plans, validations, model_calls, executed, hits = [], [], 0, 0, 0
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "google_maps_api_key", "")
        patch.setattr(settings, "trace_directory", "")
        patch.setattr(observability, "export_trace", no_export)
        patch.setitem(TOOL_FUNCTIONS, "search_places", places)
        if control:
            patch.setattr(orchestrator, "shared_cache_ttl", lambda _: 0)
            patch.setattr(orchestrator, "_execute_tool_call", serial)
        started = perf_counter()
        for _ in range(2):
            llm = FakeLLM(
                [
                    completion(
                        tool_calls=[
                            tool_call("search_places", {"query": f"Fixture venue {i}"}, str(i))
                            for i in range(4)
                        ]
                    ),
                    completion(content=ITINERARY_JSON),
                ]
            )
            result = await orchestrator.plan_trip(
                "2 days in Chicago", client=llm, model="fixture-model", today=date(2026, 8, 5)
            )
            plans.append(result.itinerary.model_dump(mode="json"))
            validations.append(result.validation.model_dump(mode="json"))
            model_calls += len(llm.requests)
            executed += result.tool_usage.executed_calls
            hits += result.tool_usage.cache_hits
        elapsed = (perf_counter() - started) * 1000
    shared_tool_cache.clear()
    return {
        "elapsed_ms": elapsed,
        "fixture_tool_calls": calls,
        "executed_calls": executed,
        "cache_hits": hits,
        "scripted_model_calls": model_calls,
        "quality_hash": observability.fingerprint(json.dumps([plans, validations], sort_keys=True)),
    }


async def benchmark(repetitions: int = 5, delay: float = 0.05) -> dict:
    observability.implementation_version()  # exclude one-time source fingerprint cost
    rng = random.Random(42)
    rows = {"serial_no_shared_cache": [], "current_runtime": []}
    for _ in range(repetitions):
        order = [True, False]
        rng.shuffle(order)
        for control in order:
            name = "serial_no_shared_cache" if control else "current_runtime"
            rows[name].append(await sample(control, delay))
    hashes = {row["quality_hash"] for samples in rows.values() for row in samples}
    if len(hashes) != 1:
        raise ValueError("Frozen itinerary or validation differs between arms")
    return {
        "kind": "offline_frozen_fixture_not_live_quality",
        "repetitions_per_arm": repetitions,
        "fixture_latency_ms": delay * 1000,
        "identical_itinerary_and_validation": True,
        "model_calls_are_scripted_not_paid": True,
        "implementation_sha256": observability.implementation_version(),
        "arms": {
            name: {
                "median_elapsed_ms": median(s["elapsed_ms"] for s in samples),
                "samples": samples,
            }
            for name, samples in rows.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    target = args.json.resolve() if args.json else None
    workspace = Path(__file__).resolve().parents[2]
    if target and (not target.is_relative_to(workspace) or target.exists()):
        parser.error("Output must be a new workspace-local file")
    report = asyncio.run(benchmark())
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
