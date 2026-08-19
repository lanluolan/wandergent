"""Hard-constraint validation for a generated itinerary.

The model is good at plausible and bad at arithmetic. This layer is the part that
makes an itinerary *feasible* rather than merely convincing: pure functions over the
finished plan, with no LLM involved, so the answer is deterministic and testable.

Design notes:

- **Codes are the contract, messages are for the model.** `code` is stable and
  machine-readable so the client can localise it; `message` is English diagnostic text
  that gets fed straight back to the model as repair instructions.
- **Nothing here raises.** A violation is data, not an exception -- the orchestrator
  decides whether to repair, warn, or ship it.
- **Route sanity is checked in time, not distance.** Without a maps tool there are no
  real travel times, so the rule is: consecutive activities in different places need
  either an explicit transport activity between them or a minimum gap. That catches
  the "teleporting tourist" plan without pretending to know the city.
"""

import re
from datetime import date, timedelta
from typing import Literal, TypeVar

from pydantic import BaseModel

from app.agent import opening_hours
from app.agent.schemas import Activity, DayPlan, Itinerary

# A plan that moves between two places with less than this and no transport activity
# is claiming teleportation.
MIN_TRANSFER_MINUTES = 15

# Days that start before dawn or end after midnight are almost always model error
# rather than an intentionally brutal schedule. Minutes since midnight throughout,
# because every comparison in this module is on that scale.
EARLIEST_START_MINUTE = 6 * 60
LATEST_END_MINUTE = 23 * 60 + 59
EARLIEST_START_LABEL = "06:00"
LATEST_END_LABEL = "23:59"

# A venue name shorter than this is not distinctive enough to match an activity on.
# "Bar" would attach some bar's hours to every activity containing the word, and a
# wrong closure is worse than no check at all.
MIN_VENUE_MATCH_CHARS = 6

# Sixteen hours of scheduled activity is a forced march, not a holiday.
MAX_DAILY_ACTIVE_HOURS = 16

# Only over-budget by more than this is worth flagging; rounding noise is not.
BUDGET_TOLERANCE = 0.01

ViolationCode = Literal[
    "over_budget",
    "time_conflict",
    "insufficient_transfer",
    "day_out_of_range",
    "duplicate_day",
    "unsociable_hours",
    "overlong_day",
    "empty_day",
    "missing_accommodation",
    "vague_venue",
    "outside_opening_hours",
    "understated_cost",
]

# Two kinds of finding, and they deserve different treatment.
#
# Most codes describe a plan contradicting itself or the world: two activities at once, a
# locked door, no time to cross town. The traveller never asked for those and cannot want
# them, so they block -- they are repaired, and if the repair fails they are shown as
# unresolved.
#
# These two are different. They are judgements about *pace*, and pace is the traveller's
# to choose. Someone who says "pack it in" and gets a 13-hour day got what they asked
# for; a late jazz set that ends past the sociable-hours cutoff is the reason they came.
# Treating those as failures makes the agent argue with the person it works for, and
# spends a repair round undoing an explicit wish. They are reported and never enforced.
ADVISORY_CODES: frozenset[str] = frozenset({"overlong_day", "unsociable_hours"})

# Google's price bands, and what this layer is willing to conclude from them. A band is
# far too coarse to price an activity -- "MODERATE" is not a number, and it means
# something different for a Chicago steakhouse and a Lisbon cafe -- so the only inference
# drawn is the one that needs no scale at all: a venue Google prices *at all* does not
# cost nothing. That direction is also the dangerous one, because an activity budgeted at
# zero is a plan claiming to fit a budget it has not accounted for.
#
# FREE and UNSPECIFIED are excluded deliberately. FREE against a non-zero cost looks like
# a contradiction and is not: a picnic in a free park still costs what the picnic costs.
PAID_PRICE_LEVELS = (
    "PRICE_LEVEL_INEXPENSIVE",
    "PRICE_LEVEL_MODERATE",
    "PRICE_LEVEL_EXPENSIVE",
    "PRICE_LEVEL_VERY_EXPENSIVE",
)

# Categories where a zero is the normal way to write a real cost, so a price band proves
# nothing: a four-night hotel is billed once and the other three nights entered at 0,
# transport is not a venue at all, and "rest at the hotel" costs what was already paid.
#
# Probed 2026-08-17: Google returned no band for any Chicago hotel or museum, so today
# this exemption is belt-and-braces rather than load-bearing -- in practice the check
# only ever fires on food. Kept because coverage is Google's to change, not ours.
COST_EXEMPT_CATEGORIES = ("transport", "accommodation", "rest")

PRICE_LEVEL_WORDS = {
    "PRICE_LEVEL_INEXPENSIVE": "inexpensive but not free",
    "PRICE_LEVEL_MODERATE": "moderately priced",
    "PRICE_LEVEL_EXPENSIVE": "expensive",
    "PRICE_LEVEL_VERY_EXPENSIVE": "very expensive",
}

# Phrases that mean the model declined to choose. Deliberately short and unambiguous:
# the same bias as the transfer rule, because a false alarm sends the agent off to
# "repair" a plan that was already fine. "a restaurant near Central Park" is also a hedge
# and is deliberately *not* caught -- "near X" alone is a legitimate way to place a
# named venue.
#
# Both scripts are kept even though the product is English: the model follows the
# language of the request, so a Chinese request still produces Chinese text that this
# has to be able to read.
HEDGE_MARKERS = (
    "或类似",
    "或相似",
    "或附近",
    "某家",
    "某个",
    "某间",
    "任意一家",
)

# English hedges vary by article in a way the Chinese ones do not: "or similar",
# "or a similar" and "or something similar" are one evasion wearing three coats, and a
# substring list catches whichever spelling happens to be enumerated. Switching the app
# to English surfaced this immediately -- "Grand Central Market or a similar food hall"
# walked straight past a list that contained "or similar".
HEDGE_PATTERN = re.compile(
    r"\bor\s+(?:a|an|the|some|something)?\s*(?:similar|nearby|equivalent|another)\b"
    r"|\b(?:some|any)\s+(?:restaurant|cafe|café|hotel|place|venue|spot)\b"
    r"|\bof your choice\b"
    r"|\bto be (?:decided|confirmed)\b"
    r"|\bt\.?b\.?d\.?\b",
    re.IGNORECASE,
)

# Words that show the plan has addressed lodging, wherever it chose to say so.
# No bare "inn": it is a substring of "dinner", which would let any plan with an
# evening meal claim it had booked a bed.
LODGING_WORDS = (
    "stay",
    "酒店",
    "旅馆",
    "民宿",
    "客栈",
    "hotel",
    "hostel",
    "lodging",
    "airbnb",
    "guesthouse",
    "ryokan",
    "accommodation",
)


class Violation(BaseModel):
    """One broken constraint. Never fatal on its own."""

    code: ViolationCode
    message: str
    # Named day, not date: a field called date would shadow the imported type
    # while its own annotation is being evaluated, which fails at class definition.
    day: date | None = None

    # Transfer detail, set only on `insufficient_transfer`. It exists so the finding is
    # actionable by something other than a human reading prose: the confirm pass needs
    # the two endpoints to ask for a real travel time, and a failing eval needs to say
    # *which* hop was too tight rather than just naming the code three times.
    origin: str | None = None
    destination: str | None = None
    gap_minutes: int | None = None
    needed_minutes: int | None = None
    #: Wall-clock minutes past midnight when the traveller would set off. Carried so the
    #: measurement can ask about the hour they will actually travel: a hop measured at
    #: 05:00 gets empty roads and a skeleton timetable, which is not the trip.
    depart_at_minute: int | None = None

    @property
    def advisory(self) -> bool:
        """A remark about pace rather than a defect. See `ADVISORY_CODES`."""
        return self.code in ADVISORY_CODES


class ValidationReport(BaseModel):
    violations: list[Violation] = []

    @property
    def blocking(self) -> list[Violation]:
        """The findings that make the plan wrong, as opposed to opinionated."""
        return [violation for violation in self.violations if not violation.advisory]

    @property
    def advisory(self) -> list[Violation]:
        """The findings about pace, which are the traveller's call. See ADVISORY_CODES."""
        return [violation for violation in self.violations if violation.advisory]

    @property
    def ok(self) -> bool:
        """Nothing is *wrong*. An advisory remark does not make a plan unsound."""
        return not self.blocking

    def as_instructions(self) -> str:
        """Render the repairable violations as instructions for the model.

        Advisory findings are left out on purpose: a repair round spent talking the model
        out of a packed day the traveller asked for is a round not spent on the locked
        door, and it costs a full generation either way.
        """
        return "\n".join(f"- {violation.message}" for violation in self.blocking)


def _minutes(value: str) -> int:
    """ "HH:MM" -> minutes since midnight. Format is already enforced by the model."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _is_transport(activity: Activity) -> bool:
    return activity.category == "transport"


def _hedge_in(activity: Activity) -> str | None:
    """The hedge phrase this activity used to avoid naming a place, if any."""
    haystack = f"{activity.title} {activity.location or ''}".lower()
    literal = next((marker for marker in HEDGE_MARKERS if marker in haystack), None)
    if literal is not None:
        return literal
    match = HEDGE_PATTERN.search(haystack)
    return match.group(0) if match else None


# Words a model uses to say "next to the last place". When it says so explicitly, take
# it at its word -- the measurement pass in `transfers.py` is what checks it.
PROXIMITY_MARKERS = (
    "周边",
    "附近",
    "内",
    "旁",
    "nearby",
    "near ",
    "inside",
    "next to",
    "same building",
    "opposite",
)

# Two Chinese place names sharing this many leading characters are almost always the
# same area ("锦里古街" / "锦里小吃街").
SHARED_PREFIX_CHARS = 2

# For Latin scripts the comparable unit is a whole word, not a character count. Two
# leading characters is a meaningful root in Chinese and almost nothing in English:
# "Santa Monica Pier" and "Santa Ana Zoo" share "Sa" while being an hour apart, so a
# character-based prefix would silently declare them the same place and skip the
# transfer check entirely. The minimum length keeps "The"/"Old"/"New" from matching
# everything.
MIN_LATIN_ROOT = 4

_CJK = re.compile(r"[一-鿿]")


def _place_root(value: str) -> str:
    """The leading chunk of a place name, measured the way its script reads.

    Returns "" when there is nothing comparable, which never matches -- the caller
    treats a missing root as "not obviously the same place".
    """
    text = value.strip()
    if not text:
        return ""
    if _CJK.search(text):
        return text[:SHARED_PREFIX_CHARS]
    first = re.split(r"[\s,]+", text)[0].lower()
    return first if len(first) >= MIN_LATIN_ROOT else ""


def _same_place(first: Activity, second: Activity) -> bool:
    """Whether moving between these two needs travel time.

    STOPGAP. Real travel times need the maps tool, which is blocked on a provider
    choice. Until then this works on the location *strings*, which means it must be
    forgiving: a live run flagged six false positives in one plan, all of the form
    "visit X" -> "lunch near X", because the names differ while the places do not.

    Deliberately biased towards false negatives. A missed transfer produces a slightly
    optimistic schedule; a false one sends the agent off to "repair" a plan that was
    already fine, which costs a round trip and usually makes the plan worse.
    """
    if first.location is None or second.location is None:
        # Absence of evidence, not evidence of a move.
        return True

    left = first.location.strip()
    right = second.location.strip()

    if left == right or left in right or right in left:
        return True
    if any(marker in right or marker in left for marker in PROXIMITY_MARKERS):
        return True
    root = _place_root(left)
    return bool(root) and root == _place_root(right)


def validate_itinerary(
    itinerary: Itinerary,
    known_hours: dict[str, list[str]] | None = None,
    known_prices: dict[str, str] | None = None,
) -> ValidationReport:
    """Check an itinerary against the hard constraints. Pure and deterministic.

    `known_hours` maps a venue name to Google's opening-hours lines, gathered from the
    `search_places` calls the run already made. It is optional because the checker must
    keep working with no maps key and for callers that never searched -- an absent entry
    means no opinion, never a closure.
    """
    violations: list[Violation] = []

    violations.extend(_check_budget(itinerary))
    violations.extend(_check_days(itinerary))
    violations.extend(_check_accommodation(itinerary))
    for day in itinerary.days:
        violations.extend(_check_day(day))
        violations.extend(_check_opening_hours(day, known_hours or {}))
        violations.extend(_check_price_levels(day, known_prices or {}))

    return ValidationReport(violations=violations)


# Written with a TypeVar rather than PEP 695 syntax: the project supports 3.11, where
# `def f[T](...)` is a syntax error, and CI runs a 3.11 + 3.13 matrix.
_Fact = TypeVar("_Fact")


def _match_known(activity: Activity, known: dict[str, _Fact]) -> _Fact | None:
    """Whatever the run looked up about the venue this activity refers to, if anything.

    Matched on containment in both directions, because the model rarely writes the
    venue name exactly as Google returned it: "Lunch at Lou Malnati's" against
    "Lou Malnati's Pizzeria". Deliberately requires a reasonably long name -- a
    three-letter match would attach the wrong venue's hours, and a wrong closure is
    worse than no check.
    """
    haystack = f"{activity.title} {activity.location or ''}".lower()
    best: _Fact | None = None
    longest = 0
    for name, fact in known.items():
        needle = name.lower().strip()
        if len(needle) < MIN_VENUE_MATCH_CHARS:
            continue
        if (needle in haystack or haystack in needle) and len(needle) > longest:
            best, longest = fact, len(needle)
    return best


def _check_opening_hours(day: DayPlan, known_hours: dict[str, list[str]]) -> list[Violation]:
    """Nothing scheduled at a venue while it is shut.

    This is the one constraint the layer could not express at all until the field mask
    asked for hours: budget, timing and routing all pass cleanly for a plan that arrives
    at a locked door. It reads hours the run already paid for rather than the model's
    own account of them, so a plan cannot satisfy it by asserting.
    """
    if not known_hours:
        return []

    weekday = day.date.strftime("%A").lower()
    violations: list[Violation] = []
    for activity in day.activities:
        if _is_transport(activity):
            continue
        descriptions = _match_known(activity, known_hours)
        if not descriptions:
            continue
        reason = opening_hours.closed_reason(
            descriptions, weekday, _minutes(activity.start_time), _minutes(activity.end_time)
        )
        if reason is None:
            continue
        violations.append(
            Violation(
                code="outside_opening_hours",
                day=day.date,
                message=(
                    f"On {day.date}, {activity.title} is scheduled "
                    f"{activity.start_time}-{activity.end_time}, but the venue is {reason}. "
                    "Move it to a time it is open, put it on another day, or choose "
                    "somewhere else."
                ),
            )
        )
    return violations


def _check_price_levels(day: DayPlan, known_prices: dict[str, str]) -> list[Violation]:
    """Nothing Google charges for is budgeted at nothing.

    The narrowest useful thing that can be said with a price *band*. It exists because
    every cost in a plan is the model's invention, and the budget check downstream is
    only as good as those inventions: a dinner entered at 0 makes an over-budget trip
    validate cleanly. This catches the free-lunch case without pretending a band is a
    price -- see `PAID_PRICE_LEVELS` for what is deliberately not concluded.
    """
    if not known_prices:
        return []

    violations: list[Violation] = []
    for activity in day.activities:
        if activity.category in COST_EXEMPT_CATEGORIES or activity.estimated_cost > 0:
            continue
        level = _match_known(activity, known_prices)
        if level not in PAID_PRICE_LEVELS:
            continue
        violations.append(
            Violation(
                code="understated_cost",
                day=day.date,
                message=(
                    f"On {day.date}, '{activity.title}' is budgeted at 0, but Google "
                    f"lists {activity.location or activity.title} as "
                    f"{PRICE_LEVEL_WORDS[str(level)]}. Put a realistic estimate on it "
                    "and keep the trip inside the budget, or choose somewhere free."
                ),
            )
        )
    return violations


def _check_accommodation(itinerary: Itinerary) -> list[Violation]:
    """A trip with nights in it has to say where those nights are spent.

    Two ways to satisfy this: schedule an accommodation activity, or say in the trip
    notes that lodging is already handled. Both are fine; silence is not. A live run
    produced a three-day plan with no hotel at all, whose breakfast entry then read
    "the hotel or a nearby cafe" -- referring to lodging the plan never chose.
    """
    if itinerary.end_date <= itinerary.start_date:
        return []
    if any(
        activity.category == "accommodation"
        for day in itinerary.days
        for activity in day.activities
    ):
        return []
    if any(word in note.lower() for note in itinerary.notes for word in LODGING_WORDS):
        return []

    nights = (itinerary.end_date - itinerary.start_date).days
    return [
        Violation(
            code="missing_accommodation",
            message=(
                f"The trip covers {nights} night(s) but no accommodation is planned. "
                "Add an accommodation activity with a named hotel or area, or state in "
                "notes that lodging is already arranged."
            ),
        )
    ]


def _check_budget(itinerary: Itinerary) -> list[Violation]:
    if itinerary.budget is None:
        return []
    overspend = itinerary.total_estimated_cost - itinerary.budget
    if overspend <= BUDGET_TOLERANCE:
        return []
    return [
        Violation(
            code="over_budget",
            message=(
                f"The plan costs {itinerary.total_estimated_cost:.0f} {itinerary.currency} "
                f"but the budget is {itinerary.budget:.0f} {itinerary.currency}, "
                f"over by {overspend:.0f}. Cut or cheapen activities until it fits."
            ),
        )
    ]


def _check_days(itinerary: Itinerary) -> list[Violation]:
    violations: list[Violation] = []
    seen: set[date] = set()

    for day in itinerary.days:
        if day.date < itinerary.start_date or day.date > itinerary.end_date:
            violations.append(
                Violation(
                    code="day_out_of_range",
                    day=day.date,
                    message=(
                        f"Day {day.date} falls outside the trip "
                        f"({itinerary.start_date} to {itinerary.end_date}). "
                        "Move it inside the range or drop it."
                    ),
                )
            )
        if day.date in seen:
            violations.append(
                Violation(
                    code="duplicate_day",
                    day=day.date,
                    message=f"Day {day.date} appears more than once. Merge the duplicates.",
                )
            )
        seen.add(day.date)

    return violations


def _check_day(day: DayPlan) -> list[Violation]:
    violations: list[Violation] = []

    if not day.activities:
        return [
            Violation(
                code="empty_day",
                day=day.date,
                message=f"Day {day.date} has no activities. Fill it or remove the day.",
            )
        ]

    ordered = sorted(day.activities, key=lambda item: _minutes(item.start_time))

    for activity in ordered:
        hedge = _hedge_in(activity)
        if hedge is not None:
            violations.append(
                Violation(
                    code="vague_venue",
                    day=day.date,
                    message=(
                        f"On {day.date}, '{activity.title}' says {hedge!r} instead of "
                        "naming a place. Commit to one venue; if you are unsure it is "
                        "still open, name it anyway and put the caveat in notes."
                    ),
                )
            )

        start = _minutes(activity.start_time)
        end = _minutes(activity.end_time)
        if start < EARLIEST_START_MINUTE or end > LATEST_END_MINUTE:
            violations.append(
                Violation(
                    code="unsociable_hours",
                    day=day.date,
                    message=(
                        f"On {day.date}, '{activity.title}' runs "
                        f"{activity.start_time}-{activity.end_time}, outside "
                        f"{EARLIEST_START_LABEL}-{LATEST_END_LABEL}. Move it into the day."
                    ),
                )
            )

    for earlier, later in zip(ordered, ordered[1:], strict=False):
        gap = _minutes(later.start_time) - _minutes(earlier.end_time)

        if gap < 0:
            violations.append(
                Violation(
                    code="time_conflict",
                    day=day.date,
                    message=(
                        f"On {day.date}, '{earlier.title}' "
                        f"({earlier.start_time}-{earlier.end_time}) overlaps "
                        f"'{later.title}' ({later.start_time}-{later.end_time}). "
                        "Give them separate slots."
                    ),
                )
            )
            continue

        moves = not _same_place(earlier, later)
        if (
            moves
            and gap < MIN_TRANSFER_MINUTES
            and not (_is_transport(earlier) or _is_transport(later))
        ):
            violations.append(
                Violation(
                    code="insufficient_transfer",
                    day=day.date,
                    origin=earlier.location,
                    destination=later.location,
                    gap_minutes=gap,
                    depart_at_minute=_minutes(earlier.end_time),
                    message=(
                        f"On {day.date}, only {gap} minutes separate "
                        f"'{earlier.title}' at {earlier.location} from "
                        f"'{later.title}' at {later.location}. Allow at least "
                        f"{MIN_TRANSFER_MINUTES} minutes to travel, or add a transport step."
                    ),
                )
            )

    span = _minutes(ordered[-1].end_time) - _minutes(ordered[0].start_time)
    if span > MAX_DAILY_ACTIVE_HOURS * 60:
        violations.append(
            Violation(
                code="overlong_day",
                day=day.date,
                message=(
                    f"Day {day.date} runs {timedelta(minutes=span)} from first to last "
                    f"activity, over the {MAX_DAILY_ACTIVE_HOURS}-hour limit. Trim it."
                ),
            )
        )

    return violations
