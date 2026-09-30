"""Confirm suspected transfer problems against real travel times.

The heuristic **proposes**, Google **disposes**. `validation._same_place` judges hops from
location strings and is deliberately forgiving -- a live run once produced six false
positives, every one "visit X" followed by "eat near X" -- so forgiving that it also
misses real ones. `transfer_candidates` adds every other real-stop pair as an advisory,
then this module measures both kinds with at most `MAX_TRANSFER_CHECKS` Routes calls.

Only permitted modes may clear a finding. An explicit leg uses its own mode. When
no mode is declared, use walking conservatively instead of assuming a car is available.

**Measured at the hour on the plan**, resolved to the destination's local clock via one
Time Zone lookup per report. A hop can measure 13 minutes by car at 05:00 and 29 at 17:30,
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
from app.tools.maps import get_travel_time, local_utc_offset
from app.tools.registry import tool_capacity

logger = logging.getLogger(__name__)

# Slack on top of the measured time, for finding the door and paying the bill.
TRANSFER_MARGIN_MINUTES = 5
MAX_TRANSFER_CHECKS = 16
MAX_PARALLEL_TRANSFER_CHECKS = 4


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
) -> tuple[int, str] | None:
    """Measure only the modes justified by the request and the scheduled leg."""
    results = await asyncio.gather(
        *(get_travel_time(origin, destination, mode, depart_at=depart_at) for mode in modes)
    )
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
    """The trip's own departure moment, expressed in UTC and pushed into the future.

    An eval case, a re-run of a saved plan, or a trip whose first days have passed all
    produce dates Google will not answer for. Sliding forward in **whole weeks** keeps
    what the measurement depends on -- the weekday and the local clock time. Sliding by
    days would turn a Tuesday rush hour into a Sunday morning.
    """
    if day is None or minute is None:
        return None
    local_naive = datetime.combine(day, time(0, 0)) + timedelta(minutes=minute)
    moment = local_naive.replace(tzinfo=UTC) - offset

    horizon = datetime.now(UTC) + timedelta(days=1)
    if moment < horizon:
        weeks_behind = (horizon - moment).days // 7 + 1
        moment += timedelta(weeks=weeks_behind)
    return moment


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

    # One offset for the whole report: every hop is in the same city and the lookup costs
    # two calls. Without it we cannot preserve the plan's local departure time, so every
    # finding remains unchanged rather than being cleared by a misleading measurement.
    offset = await local_utc_offset(
        candidates[0].origin or "", next((v.day for v in candidates if v.day), None)
    )
    if offset is None:
        logger.info("no local offset; preserving transfer findings")
        return report

    limiter = asyncio.Semaphore(MAX_PARALLEL_TRANSFER_CHECKS)

    async def measure_bounded(violation: Violation):
        async with limiter:
            async with tool_capacity("get_travel_time"):
                return await _measure(
                    violation.origin or "",
                    violation.destination or "",
                    _departure_instant(violation.day, violation.depart_at_minute, offset)
                    if offset is not None
                    else None,
                    modes=((violation.travel_mode,) if violation.travel_mode else ("WALK",))
                    if not allowed_modes or (violation.travel_mode or "WALK") in allowed_modes
                    else (),
                )

    measured = await asyncio.gather(
        *(measure_bounded(v) for v in candidates),
        return_exceptions=True,
    )

    verdicts: dict[int, tuple[int, str] | None] = {}
    for violation, outcome in zip(candidates, measured, strict=False):
        if isinstance(outcome, BaseException):
            logger.warning("transfer confirmation failed: %s", outcome)
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
                    "message": (
                        f"On {violation.day}, getting from {violation.origin} to "
                        f"{violation.destination} takes about {minutes} minutes by "
                        f"{mode.lower()}, but the schedule leaves {gap}. Allow at least "
                        f"{needed} minutes, move one of them, or add a transport step."
                    ),
                }
            )
        )

    return ValidationReport(violations=kept)
