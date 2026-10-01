"""Bounded schedule candidates from observed hours, never an assertion of feasibility.

The orchestrator must fully validate and remeasure a candidate before accepting it.
No venue discovery, speculative hours, cost increases or edits to locked days here.
"""

import re
from collections.abc import Iterator

from app.agent import opening_hours
from app.agent.results import ToolCallRecord
from app.agent.revision import RevisionScope, contains_venue
from app.agent.schemas import Activity, Itinerary
from app.agent.validation import _match_known, _minutes, validate_itinerary

MAX_CANDIDATES = 4
MAX_CHANGED_ACTIVITIES = 3
MAX_REFLOWED_ACTIVITIES = 6
_BOOKED = re.compile(
    r"\b(?:booked|reservation|reserved|ticketed|fixed|confirmed)\b|预约|已订", re.I
)
_DIETARY = re.compile(
    r"vegan|vegetarian|gluten|halal|kosher|allerg|nut.free|素食|清真|过敏|无麸质", re.I
)
_MEALS = (
    (r"\b(?:breakfast|brunch)\b|早餐|早饭", "Breakfast", 6 * 60, 12 * 60),
    (r"\blunch\b|午餐|午饭", "Lunch", 11 * 60, 16 * 60),
    (r"\b(?:dinner|supper)\b|晚餐|晚饭", "Dinner", 17 * 60, 24 * 60),
)
_DURATION = re.compile(r"\b\d+\s*(?:hours?|hrs?|minutes?|mins?)\b", re.I)
_SPECIFIC_MEAL = re.compile(
    r"\b(?:breakfast|brunch|lunch|dinner|supper)\b[^.!?\n]{0,60}\b(?:to|at|with|for)\b", re.I
)


def _meal(activity: Activity) -> tuple[str, int, int] | None:
    return next(
        (
            (label, start, end)
            for pattern, label, start, end in _MEALS
            if re.search(pattern, activity.title, re.I)
        ),
        None,
    )


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _observed_restaurants(records: list[ToolCallRecord]) -> list[dict]:
    places = [
        place
        for record in records
        if record.ok and record.name == "search_places"
        for place in record.fact_payload.get("places") or []
        if isinstance(place, dict)
        and place.get("name")
        and "restaurant" in (place.get("types") or [])
    ]
    # Same-name branches cannot safely use the current name-keyed hours dictionary.
    return [
        place
        for place in places
        if len({p.get("address") for p in places if p["name"] == place["name"]}) == 1
    ]


def _options(
    activity: Activity,
    weekday: str,
    hours: dict[str, list[str]],
    prices: dict[str, str],
    restaurants: list[dict],
    request: str,
    dietary_context: str,
) -> list[Activity]:
    if _BOOKED.search(" ".join([request, activity.title, activity.notes or ""])):
        return []
    options = []
    start, end = _minutes(activity.start_time), _minutes(activity.end_time)
    meal = _meal(activity)
    if not re.search(r"\b\d{1,2}:\d{2}\b", request):
        windows = opening_hours.parse(_match_known(activity, hours) or []).get(weekday, [])
        for open_at, close_at in windows:
            lower = max(open_at, 6 * 60, meal[1] if meal else 0)
            upper = min(close_at, 23 * 60 + 59, meal[2] if meal else 24 * 60) - (end - start)
            if lower > upper:
                continue
            shifted = max(lower, min(start, upper))
            if shifted == start:
                continue
            options.append(
                activity.model_copy(
                    update={
                        "start_time": _clock(shifted),
                        "end_time": _clock(shifted + end - start),
                    }
                )
            )
    old_name = _match_known(activity, {name: name for name in hours})
    if (
        activity.category != "food"
        or not old_name
        or contains_venue(request, old_name)
        or _SPECIFIC_MEAL.search(request)
        or _DIETARY.search(
            " ".join([request, dietary_context, activity.title, activity.notes or ""])
        )
    ):
        return options
    old_price = _match_known(activity, prices)
    for place in restaurants:
        name = place["name"]
        if name == old_name or not old_price or place.get("price_level") != old_price:
            continue
        descriptions = place.get("opening_hours") or []
        windows = opening_hours.parse(descriptions).get(weekday)
        if windows is None or not any(start >= left and end <= right for left, right in windows):
            continue
        options.append(
            activity.model_copy(
                update={
                    "title": f"{meal[0] if meal else 'Meal'} at {name}",
                    "location": f"{name}, {place['address']}" if place.get("address") else name,
                    "highlights": [],  # Old dishes/recommendations do not belong to the new venue.
                    "notes": "Closed venue replaced using observed opening hours. Cost remains an "
                    "estimate; verify menu prices and availability before booking.",
                }
            )
        )
        if len(options) >= MAX_CANDIDATES:
            break
    return options[:MAX_CANDIDATES]


def _backfill_prefix(
    changed: Itinerary,
    original: Itinerary,
    day_index: int,
    activity_index: int,
    hours: dict[str, list[str]],
    request: str,
) -> Itinerary | None:
    """Propose an earlier flexible prefix, never weaken the validator or route checks.

    Preserve transport durations, gaps, venue/content/cost and all following activities.
    Flexible visits can shorten to 30 min (rest 15), or their already shorter duration.
    Never touch accommodation, booked/named stops or explicit user times/durations.
    """
    if re.search(r"\b\d{1,2}:\d{2}\b", request) or _DURATION.search(request):
        return None
    result = changed.model_copy(deep=True)
    activities = result.days[day_index].activities
    before = original.days[day_index].activities
    for index in range(activity_index - 1, -1, -1):
        activity = activities[index]
        next_start = _minutes(activities[index + 1].start_time)
        old_gap = max(0, _minutes(before[index + 1].start_time) - _minutes(before[index].end_time))
        latest_end = next_start - old_gap
        if _minutes(activity.end_time) <= latest_end:
            break
        name = _match_known(activity, {name: name for name in hours})
        if (
            activity.category == "accommodation"
            or _BOOKED.search(" ".join([request, activity.title, activity.notes or ""]))
            or (name and contains_venue(request, name))
        ):
            return None
        duration = _minutes(activity.end_time) - _minutes(activity.start_time)
        minimum = (
            duration
            if activity.category == "transport"
            else min(duration, 15 if activity.category == "rest" else 30)
        )
        start = min(_minutes(activity.start_time), latest_end - minimum)
        meal = _meal(activity)
        if start < 6 * 60 or (meal and (start < meal[1] or latest_end > meal[2])):
            return None
        descriptions = _match_known(activity, hours)
        if descriptions and opening_hours.closed_reason(
            descriptions, result.days[day_index].date.strftime("%A").lower(), start, latest_end
        ):
            return None
        activities[index] = activity.model_copy(
            update={"start_time": _clock(start), "end_time": _clock(latest_end)}
        )
    changes = sum(
        (a.start_time, a.end_time) != (b.start_time, b.end_time)
        for new_day, old_day in zip(result.days, original.days, strict=True)
        for a, b in zip(new_day.activities, old_day.activities, strict=True)
    )
    return result if changes <= MAX_REFLOWED_ACTIVITIES else None


def hours_candidates(
    plan: Itinerary,
    hours: dict[str, list[str]],
    prices: dict[str, str],
    records: list[ToolCallRecord],
    *,
    scope: RevisionScope | None = None,
    request: str = "",
    dietary_context: str = "",
) -> Iterator[Itinerary]:
    """Up to four candidates; semantic meal times and explicit reservations stay protected."""
    targets = []
    for day_index, day in enumerate(plan.days):
        weekday = day.date.strftime("%A").lower()
        for activity_index, activity in enumerate(day.activities):
            if activity.category == "transport":
                continue
            descriptions = _match_known(activity, hours)
            if descriptions and opening_hours.closed_reason(
                descriptions, weekday, _minutes(activity.start_time), _minutes(activity.end_time)
            ):
                if scope and day_index in scope.locked_days:
                    return
                targets.append((day_index, activity_index, weekday))
    if not targets or len(targets) > MAX_CHANGED_ACTIVITIES:
        return
    candidates = [plan]
    restaurants = _observed_restaurants(records)
    for day_index, activity_index, weekday in targets:
        expanded = []
        for candidate in candidates:
            activity = candidate.days[day_index].activities[activity_index]
            for alternative in _options(
                activity, weekday, hours, prices, restaurants, request, dietary_context
            ):
                changed = candidate.model_copy(deep=True)
                changed.days[day_index].activities[activity_index] = alternative
                # A retime that already overlaps an unrelated stop must not consume the
                # bounded beam before joint observed-restaurant replacements are tried.
                # Other pending closures are expected; route heuristics are not proof.
                if any(
                    violation.code == "time_conflict"
                    for violation in validate_itinerary(changed, hours, prices).blocking
                ):
                    if alternative.start_time >= activity.start_time:
                        continue
                    changed = _backfill_prefix(
                        changed, plan, day_index, activity_index, hours, request
                    )
                    if changed is None or any(
                        violation.code == "time_conflict"
                        for violation in validate_itinerary(changed, hours, prices).blocking
                    ):
                        continue
                expanded.append(changed)
                if len(expanded) >= MAX_CANDIDATES:
                    break
            if len(expanded) >= MAX_CANDIDATES:
                break
        candidates = expanded
    yield from candidates
