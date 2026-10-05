"""Confirm suspected transfer problems against real travel times.

The heuristic **proposes**, Google **disposes**. `validation._same_place` judges hops from
location strings and is deliberately forgiving -- a live run once produced six false
positives, every one "visit X" followed by "eat near X" -- so forgiving that it also
misses real ones. `transfer_candidates` adds every other real-stop pair as an advisory,
then this module measures both kinds with at most `MAX_TRANSFER_CHECKS` Routes calls.

Only permitted modes may clear a finding. An explicit leg uses its own mode. When
no mode is declared, use walking conservatively instead of assuming a car is available.

**Measured at the hour on the plan**, resolved at each departure place and local time.
A hop can measure 13 minutes by car at 05:00 and 29 at 17:30,
so a fixed reference hour clears hops nobody could make.

Everything degrades: no key, no timezone, a timeout, an unroutable pair -- the original
heuristic violation stands unchanged.
"""

import asyncio
import logging
import math
from datetime import UTC, date, datetime, time, timedelta

from app.agent.validation import ValidationReport, Violation
from app.config import settings
from app.observability import route_facts, span
from app.tools.cache import collected_now
from app.tools.maps import TravelTime, local_utc_offset
from app.tools.mcp_client import research_session
from app.tools.registry import call_tool, tool_capacity

logger = logging.getLogger(__name__)

# Slack on top of the measured time, for finding the door and paying the bill.
TRANSFER_MARGIN_MINUTES = 5
MAX_TRANSFER_CHECKS = 16
MAX_PARALLEL_TRANSFER_CHECKS = 4


async def get_travel_time(origin, destination, mode, *, depart_at=None) -> TravelTime:
    result = await call_tool(
        "get_travel_time",
        {"origin": origin, "destination": destination, "mode": mode, "depart_at": depart_at},
    )
    if isinstance(result, TravelTime):
        return result
    return TravelTime(
        ok=False,
        origin=origin,
        destination=destination,
        mode=mode,
        code=result.code,
        error=result.error,
    )


def _needs_confirming(violation: Violation) -> bool:
    return (
        violation.code in {"insufficient_transfer", "transfer_unverified"}
        and bool(violation.origin)
        and bool(violation.destination)
        and violation.gap_minutes is not None
    )


async def _measure(
    origin: str,
    destination: str,
    depart_at: datetime | None = None,
    modes: tuple[str, ...] = ("WALK",),
    planned_day: date | None = None,
    departure_minute: int | None = None,
) -> tuple[int, str] | None:
    """Measure only the modes justified by the request and the scheduled leg."""

    async def measure(mode):
        with span(
            "tool.confirm_route",
            **{
                "openinference.span.kind": "TOOL",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "get_travel_time",
                "wandergent.route.mode": mode,
            },
        ) as record:
            result = await get_travel_time(origin, destination, mode, depart_at=depart_at)
            record.attributes["wandergent.tool.ok"] = result.ok
            if not result.ok:
                record.error_type = result.code or "tool_error"
                record.attributes["error.type"] = record.error_type
            else:
                record.attributes["wandergent.route.seconds"] = result.seconds
            route_facts().append(
                {
                    "origin": origin,
                    "destination": destination,
                    "mode": mode,
                    "seconds": result.seconds if result.ok else None,
                    "collected_at": collected_now(),
                    "departure": depart_at.isoformat() if depart_at else None,
                    "day": str(planned_day) if planned_day else None,
                    "departure_minute": departure_minute,
                    "transit_fare": result.transit_fare.model_dump()
                    if result.transit_fare
                    else None,
                    "toll_prices": [price.model_dump() for price in result.toll_prices],
                    "toll_prices_known": result.toll_prices_known,
                }
            )
            return result

    results = await asyncio.gather(*(measure(mode) for mode in modes))
    usable = [
        (result.seconds, result.mode)
        for result in results
        if result.ok and result.seconds is not None
    ]
    if not usable:
        return None
    seconds, mode = min(usable)
    return math.ceil(seconds / 60), mode


def _departure_instant(day: date | None, minute: int | None, offset: timedelta) -> datetime | None:
    """The trip's own departure moment in UTC, without substituting another date."""
    if day is None or minute is None:
        return None
    local_naive = datetime.combine(day, time(0, 0)) + timedelta(minutes=minute)
    return local_naive.replace(tzinfo=UTC) - offset


async def confirm_transfers(
    report: ValidationReport, *, allowed_modes: list[str] | None = None
) -> ValidationReport:
    """Re-judge every transfer candidate against a measured travel time.

    Returns a new report: violations that the real numbers clear are dropped, the rest
    are kept with the measurement written into the message so the repair instruction
    tells the model how much time it actually has to find.
    """
    candidates = [violation for violation in report.violations if _needs_confirming(violation)][
        :MAX_TRANSFER_CHECKS
    ]
    if not candidates or not settings.google_maps_api_key:
        return report

    limiter = asyncio.Semaphore(MAX_PARALLEL_TRANSFER_CHECKS)

    async def resolve_offset(origin, day, minute):
        if day is None or minute is None:
            return None
        async with limiter:
            with span("tool.local_utc_offset", **{"openinference.span.kind": "TOOL"}) as record:
                offset = await local_utc_offset(origin, day, minute=minute)
                record.attributes["wandergent.tool.ok"] = offset is not None
                return offset

    def departure_key(v):
        if v.departure_instant:
            moment = v.departure_instant
            return v.origin, moment.date(), moment.hour * 60 + moment.minute
        return v.origin, v.day, v.depart_at_minute

    def arrival_key(v):
        moment = v.arrival_deadline
        return v.destination, moment.date(), moment.hour * 60 + moment.minute

    keys = dict.fromkeys(
        key
        for v in candidates
        for key in (
            [departure_key(v), arrival_key(v)] if v.arrival_deadline else [departure_key(v)]
        )
    )
    resolved = await asyncio.gather(
        *(resolve_offset(origin or "", day, minute) for origin, day, minute in keys),
        return_exceptions=True,
    )
    offsets = dict(zip(keys, resolved, strict=True))

    async def measure_bounded(violation: Violation):
        offset = offsets[departure_key(violation)]
        if offset is None or isinstance(offset, BaseException):
            return None
        if violation.departure_instant and violation.departure_instant.utcoffset() != offset:
            return None
        if violation.arrival_deadline:
            destination_offset = offsets[arrival_key(violation)]
            if (
                destination_offset is None
                or isinstance(destination_offset, BaseException)
                or violation.arrival_deadline.utcoffset() != destination_offset
            ):
                return None
        async with limiter:
            async with tool_capacity("get_travel_time"):
                return await _measure(
                    violation.origin or "",
                    violation.destination or "",
                    violation.departure_instant.astimezone(UTC)
                    if violation.departure_instant
                    else _departure_instant(violation.day, violation.depart_at_minute, offset),
                    modes=((violation.travel_mode,) if violation.travel_mode else ("WALK",))
                    if not allowed_modes or (violation.travel_mode or "WALK") in allowed_modes
                    else (),
                    planned_day=violation.day,
                    departure_minute=violation.depart_at_minute,
                )

    async with research_session():
        measured = await asyncio.gather(
            *(measure_bounded(v) for v in candidates),
            return_exceptions=True,
        )

    verdicts: dict[int, tuple[int, str] | None] = {}
    for violation, outcome in zip(candidates, measured, strict=False):
        if isinstance(outcome, BaseException):
            logger.warning("transfer confirmation failed type=%s", type(outcome).__name__)
            verdicts[id(violation)] = None
        else:
            verdicts[id(violation)] = outcome

    kept: list[Violation] = []
    for violation in report.violations:
        if id(violation) not in verdicts:
            kept.append(violation)
            continue

        verdict = verdicts[id(violation)]
        gap = violation.gap_minutes or 0
        if verdict is None:
            # Unmeasurable: keep the heuristic's word rather than quietly clearing it.
            kept.append(violation)
            continue
        minutes, mode = verdict

        needed = minutes + TRANSFER_MARGIN_MINUTES
        if needed <= gap:
            logger.info(
                "transfer cleared by measurement: %s -> %s takes %s min by %s, %s available",
                violation.origin,
                violation.destination,
                minutes,
                mode.lower(),
                gap,
            )
            continue

        kept.append(
            violation.model_copy(
                update={
                    "code": "insufficient_transfer",
                    "needed_minutes": needed,
                    "travel_mode": mode,
                    "message": (
                        f"On {violation.day}, getting from {violation.origin} to "
                        f"{violation.destination} takes about {minutes} minutes by "
                        f"{mode.lower()}, but the schedule leaves {gap}. Allow at least "
                        f"{needed} minutes between the real stops. A transport label alone "
                        "does not create time. To use another permitted mode, schedule a "
                        "separate transport activity with travel_mode; it will be measured again."
                    ),
                }
            )
        )

    return ValidationReport(violations=kept)
