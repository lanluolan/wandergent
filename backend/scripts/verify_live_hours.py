"""Paid live dependency test of observed-hours recovery with explicit fault injection.

No fake model, Places, timezone or Routes responses. Only the parsed schedule is replaced
twice, after genuine model outputs, to emulate a model repeating an observed closure.
This proves live dependency integration, NOT natural model failure/recovery frequency.
The release eval suite is untouched. No injected code is enabled in the application.
"""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from app.agent import opening_hours, orchestrator
from app.agent.llm import StreamedToolCall
from app.agent.schemas import Activity, DayPlan, Itinerary
from app.agent.validation import validate_itinerary
from app.config import settings
from app.memory.store import PreferenceStore
from app.observability import implementation_version, span
from evals.run import diagnostic_snapshot
from scripts.verify_phoenix import verify


def clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def broken_schedule(
    museum: dict, park: dict, day: date, *, packed_prefix: bool = False
) -> Itinerary:
    windows = opening_hours.parse(museum.get("opening_hours") or []).get(
        day.strftime("%A").lower(), []
    )
    window = next(
        ((start, end) for start, end in windows if 570 <= start <= 720 and end - start >= 60),
        None,
    )
    if window is None or "museum" not in (museum.get("types") or []):
        raise ValueError("No suitable observed museum opening window")
    bad_start = window[1] + 60 if packed_prefix else window[0] - 120
    if bad_start + 60 > 23 * 60 + 59:
        raise ValueError("Observed closing window cannot support the packed-prefix fault")
    return Itinerary(
        destination="Boston",
        start_date=day,
        end_date=day,
        budget=200,
        currency="USD",
        days=[
            DayPlan(
                date=day,
                summary="Park and museum",
                activities=[
                    Activity(
                        title=f"Walk at {park['name']}",
                        category="sightseeing",
                        location=f"{park['name']}, {park['address']}",
                        start_time=clock(window[1] - 60) if packed_prefix else "06:00",
                        end_time=clock(window[1]) if packed_prefix else "06:15",
                    ),
                    Activity(
                        title=f"Visit {museum['name']}",
                        category="activity",
                        location=f"{museum['name']}, {museum['address']}",
                        start_time=clock(bad_start),
                        end_time=clock(bad_start + 60),
                        estimated_cost=40,
                    ),
                ],
            )
        ],
    )


async def persisted_spans(trace_id: str, endpoint: str, expected: set[str]) -> list[dict]:
    """Collector acknowledgement precedes indexing; retry reads, never paid model calls."""
    async with httpx.AsyncClient(timeout=5) as client:
        for attempt in range(5):
            response = await client.get(
                f"{endpoint.rstrip('/')}/v1/projects/wandergent/spans",
                params={"trace_id": trace_id, "limit": 1000},
            )
            response.raise_for_status()
            spans = response.json()["data"]
            try:
                verify(spans, trace_id, expected)
                return spans
            except ValueError:
                if attempt == 4:
                    raise
                await asyncio.sleep(1)
    raise ValueError("No persisted trace")


async def verify_recovery(report: dict, endpoint: str) -> None:
    result = report["result"]
    spans = await persisted_spans(
        result["trace_id"], endpoint, {"plan", "repair.schedule", "experiment.inject_schedule"}
    )
    report["phoenix"] = verify(spans, result["trace_id"], {"plan"})
    schedule = [s for s in spans if s["name"] == "repair.schedule"]
    report["schedule_spans"] = [s["attributes"] for s in schedule]
    routes = [s for s in spans if s["name"] == "tool.confirm_route"]
    report["confirmed_route_passes"] = len(routes)
    report["successful_route_passes"] = sum(
        bool(s["attributes"].get("wandergent.tool.ok")) for s in routes
    )
    report["passed"] = (
        report["injected_candidates"] == 2
        and not result["violations"]
        and report["usage"]["llm_calls"] >= 2
        and report["usage"]["total_tokens"] > 0
        and report["frame_and_cost_preserved"]
        and report["final_times"] != report["injected_initial_times"]
        and len(schedule) == 1
        and schedule[0]["attributes"].get("wandergent.repair.output_accepted") is True
        and len(report["validation_history"]) == 2
        and not report["validation_history"][0]["ok"]
        and report["validation_history"][1]["ok"]
        and report["successful_route_passes"] >= 3
        and report["successful_route_passes"] == report["confirmed_route_passes"]
        and report["phoenix"]["implementation_sha256"] == report["implementation_sha256"]
        and report["phoenix"]["root_status"] == "OK"
    )


async def experiment(
    *, today: date, target: Path, endpoint: str, packed_prefix: bool = False
) -> dict:
    day = date(2026, 10, 21)
    report = {
        "kind": "live_dependencies_with_explicit_schedule_fault_injection",
        "natural_model_recovery_evidence": False,
        "fixture": "closing_time_packed_prefix" if packed_prefix else "before_opening",
        "model": settings.openai_model,
        "implementation_sha256": implementation_version(),
        "started_at": datetime.now(UTC).isoformat(),
        "fault": "repeat a closed-hours candidate after genuine initial/repair model outputs",
        "injected_candidates": 0,
        "validation_history": [],
        "passed": False,
        "model_cost_usd": None,
    }
    original_parse, original_emit = orchestrator.parse, orchestrator.emit
    original_repair, original_validate = orchestrator.repair, orchestrator.validate
    broken = None

    async def inject(state, update):
        nonlocal broken
        if update.get("itinerary") is None:
            return update
        if broken is None:
            records = list(state["records"])
            facts = {
                key: dict(state.get(key) or {})
                for key in ("place_hours", "place_prices", "place_points", "place_addresses")
            }
            venues = []
            for query in ("Museum of Fine Arts Boston", "Boston Public Garden"):
                record, _, value = await orchestrator._execute_tool_call(
                    StreamedToolCall(
                        id=f"live-hours-{len(venues)}",
                        name="search_places",
                        arguments=json.dumps({"query": query, "near": "Boston", "limit": 1}),
                    ),
                    {"user_id": ""},
                )
                if not record.ok:
                    raise ValueError("Live Places lookup failed")
                payload = json.loads(value.content)
                places = payload.get("places") or []
                if len(places) != 1 or not places[0].get("address"):
                    raise ValueError("Live Places fixture lacks one unambiguous address")
                record.fact_payload = payload
                records.append(record)
                venues.append(places[0])
                for key, harvest in (
                    ("place_hours", orchestrator.harvest_place_hours),
                    ("place_prices", orchestrator.harvest_place_prices),
                    ("place_points", orchestrator.harvest_place_points),
                    ("place_addresses", orchestrator.harvest_place_addresses),
                ):
                    harvest(record.name, value.content, facts[key])
            broken = broken_schedule(*venues, day, packed_prefix=packed_prefix)
            checked = validate_itinerary(
                broken,
                facts["place_hours"],
                facts["place_prices"],
                constraints=state["constraints"],
            )
            if "outside_opening_hours" not in {v.code for v in checked.blocking}:
                raise ValueError("Fault does not reproduce an observed closure")
            update.update(facts)
            update["records"] = records
            report["observed_fixture_checks"] = sorted({v.code for v in checked.blocking})
            report["injected_initial_times"] = [
                [activity.start_time, activity.end_time] for activity in broken.days[0].activities
            ]
        with span(
            "experiment.inject_schedule",
            **{"wandergent.experiment.fault_injected": True},
        ):
            update["itinerary"] = broken.model_copy(deep=True)
            report["injected_candidates"] += 1
        return update

    async def parse(state):
        return await inject(state, await original_parse(state))

    async def emit(state):
        return await inject(state, await original_emit(state))

    async def repair(state):
        return await inject(state, await original_repair(state))

    async def validate(state):
        update = await original_validate(state)
        report["validation_history"].append(
            {
                "ok": update["report"].ok,
                "blocking_codes": sorted({v.code for v in update["report"].blocking}),
                "advisory_codes": sorted({v.code for v in update["report"].advisory}),
            }
        )
        return update

    try:
        with (
            patch.object(orchestrator, "parse", parse),
            patch.object(orchestrator, "emit", emit),
            patch.object(orchestrator, "repair", repair),
            patch.object(orchestrator, "validate", validate),
        ):
            graph = orchestrator._build_graph()
        with patch.object(orchestrator, "GRAPH", graph):
            result = await orchestrator.plan_trip(
                f"1 day in Boston on {day}, budget 200 USD, a museum and a park, flexible timing",
                today=today,
                fast_model="",
                memory=PreferenceStore(target.parent / f"{target.stem}-memory.db"),
            )
        report["result"] = diagnostic_snapshot(result)
        report["usage"] = result.usage.model_dump()
        report["tool_usage"] = result.tool_usage.model_dump()
        report["final_times"] = [
            [activity.start_time, activity.end_time]
            for activity in result.itinerary.days[0].activities
        ]
        report["frame_and_cost_preserved"] = (
            result.itinerary.destination == broken.destination
            and result.itinerary.start_date == broken.start_date
            and result.itinerary.end_date == broken.end_date
            and result.itinerary.budget == broken.budget
            and result.itinerary.total_estimated_cost == broken.total_estimated_cost
        )
        await verify_recovery(report, endpoint)
    except Exception as exc:
        report["error_type"] = type(exc).__name__  # Never retain provider exception text.
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        await asyncio.to_thread(target.write_text, json.dumps(report, indent=2), encoding="utf-8")
    return report


async def reverify(source: Path, target: Path, endpoint: str) -> dict:
    report = json.loads(await asyncio.to_thread(source.read_text, encoding="utf-8-sig"))
    if report.get("kind") != "live_dependencies_with_explicit_schedule_fault_injection":
        raise ValueError("Only an existing live hours fault experiment may be reverified")
    report["verification_source_report"] = source.name
    report["verification_previous_passed"] = report["passed"]
    report["verification_previous_error_type"] = report.pop("error_type", None)
    report["verification_only_no_paid_calls"] = True
    await verify_recovery(report, endpoint)
    report["verified_at"] = datetime.now(UTC).isoformat()
    await asyncio.to_thread(target.write_text, json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--verify-report", type=Path, help="read-only reverify; no paid calls")
    parser.add_argument(
        "--packed-prefix", action="store_true", help="exercise bounded prefix reflow"
    )
    args = parser.parse_args()
    target = args.json.resolve()
    workspace = Path(__file__).resolve().parents[2]
    if not target.is_relative_to(workspace) or target.exists():
        parser.error("Output must be a new workspace-local file")
    if args.verify_report:
        if not args.verify_report.resolve().is_relative_to(workspace):
            parser.error("Source must be workspace-local")
        report = asyncio.run(reverify(args.verify_report, target, args.endpoint))
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    if not settings.openai_api_key or not settings.google_maps_api_key:
        parser.error("Existing model and Maps configuration is required")
    if not settings.otel_exporter_otlp_traces_endpoint:
        parser.error("Enable the approved local Phoenix exporter for this evidence test")
    target.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(
        experiment(
            today=args.today,
            target=target,
            endpoint=args.endpoint,
            packed_prefix=args.packed_prefix,
        )
    )
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
