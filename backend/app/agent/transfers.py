"""Confirm suspected transfer problems against real travel times.

The constraint layer decides "can you get from here to there in the gap" from the
location *strings*, because until the maps tools landed there was nothing better. That
rule is deliberately forgiving -- a live run once produced six false positives, every
one "visit X" followed by "eat near X" -- and forgiving means it also misses real ones.

This module is the second half of that answer: the heuristic **proposes**, Google
**disposes**. Only pairs the heuristic already flagged get a real lookup, so the cost is
a handful of Routes calls per plan rather than one per activity pair.

**All three modes, shortest wins.** The question this layer asks is "is this schedule
*possible*", not "how will they travel", so a hop that is a long walk and a short train
ride is not infeasible -- it is a schedule with a train in it. Walking, transit and
driving are measured together and the quickest stands.

Transit earns its place by being the honest number where driving is not: a tourist
without a car takes the train, and in a dense city at a real hour the train often beats
the car anyway. That comparison is only fair because driving is now measured
`TRAFFIC_AWARE`; free-flow drive times made the car look best everywhere and quietly
turned every measurement optimistic.

Transit coverage is regional and its absence is **not** an error -- Japan returns no
transit route at all. An empty answer from any one mode simply means "not that way
here", and the others carry the verdict.

**Measured at the hour on the plan.** Every lookup is priced for the moment the
traveller actually sets off, resolved to the destination's local clock via one Time Zone
lookup per report. This is not a detail: the Art Institute to Wrigley Field measures 13
minutes by car at 05:00 and 29 at 17:30, so the previous fixed 11:00 UTC reference --
dawn in Chicago -- was clearing hops nobody could make. Anything measured off-peak
launders the model's optimism instead of catching it.

Everything degrades. No key, no timezone, a timeout, an unroutable pair -- the original
heuristic violation stands unchanged, which is exactly the behaviour from before this
existed.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta

from app.agent.validation import ValidationReport, Violation
from app.config import settings
from app.tools.maps import get_travel_time, local_utc_offset

logger = logging.getLogger(__name__)

# Slack on top of the measured time, for finding the door and paying the bill.
TRANSFER_MARGIN_MINUTES = 5


def _needs_confirming(violation: Violation) -> bool:
    return (
        violation.code == "insufficient_transfer"
        and bool(violation.origin)
        and bool(violation.destination)
        and violation.gap_minutes is not None
    )


async def _measure(
    origin: str, destination: str, depart_at: datetime | None = None
) -> tuple[int, str] | None:
    """Shortest sensible travel time in minutes and the mode it assumes.

    All three modes are asked at once rather than transit-then-fallback in sequence: the
    fallback is needed often enough (every Japanese city) that a second round trip would
    be the common case, not the exception, and three concurrent lookups cost the same
    wall-clock as one.
    """
    transit, walk, drive = await asyncio.gather(
        get_travel_time(origin, destination, "TRANSIT", depart_at=depart_at),
        get_travel_time(origin, destination, "WALK", depart_at=depart_at),
        get_travel_time(origin, destination, "DRIVE", depart_at=depart_at),
    )
    usable = [
        (result.seconds, result.mode)
        for result in (transit, walk, drive)
        if result.ok and result.seconds is not None
    ]
    if not usable:
        return None
    seconds, mode = min(usable)
    return round(seconds / 60), mode


def _departure_instant(day: date | None, minute: int | None, offset: timedelta) -> datetime | None:
    """The trip's own departure moment, expressed in UTC and pushed into the future.

    Why the push: the plan is normally for a future date, but not always -- an eval case,
    a re-run of a saved plan, or a trip whose first days have passed all produce dates
    Google will not answer for. Sliding forward in **whole weeks** keeps both things the
    measurement actually depends on: the weekday (a Sunday timetable is not a Tuesday
    one) and the local clock time. Sliding by days would silently turn a Tuesday rush
    hour into a Sunday morning, which is the very error this function exists to remove.
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


async def confirm_transfers(report: ValidationReport) -> ValidationReport:
    """Re-judge every `insufficient_transfer` against a measured travel time.

    Returns a new report: violations that the real numbers clear are dropped, the rest
    are kept with the measurement written into the message so the repair instruction
    tells the model how much time it actually has to find.
    """
    candidates = [violation for violation in report.violations if _needs_confirming(violation)]
    if not candidates or not settings.google_maps_api_key:
        return report

    # One offset for the whole report: every hop in a plan is in the same city, and the
    # lookup costs two calls. Failure is not fatal -- `_measure` then omits the departure
    # time and gets the generic future weekday, which is what it always used to get.
    offset = await local_utc_offset(
        candidates[0].origin or "", next((v.day for v in candidates if v.day), None)
    )
    if offset is None:
        logger.info(
            "no local offset for %r; measuring at the generic reference hour", candidates[0].origin
        )

    measured = await asyncio.gather(
        *(
            _measure(
                v.origin or "",
                v.destination or "",
                _departure_instant(v.day, v.depart_at_minute, offset)
                if offset is not None
                else None,
            )
            for v in candidates
        ),
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
