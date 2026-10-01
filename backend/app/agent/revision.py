"""Deterministic boundaries for editing an existing itinerary.

The model proposes an edit. This module decides which parts it was allowed to edit and
restores everything else from the client-supplied itinerary after every model turn.
"""

import json
import re
from dataclasses import dataclass

from app.agent.schemas import DayPlan, Itinerary

_REMOVAL = re.compile(
    r"(?:^|[.!?\n])\s*(?:please\s+)?(?:remove|delete|drop)\s+"
    r"(?P<venue>[^.!?\n]{2,160}?)(?:\s+(?:from|on)\s+day\s+(?P<day>\d+))?"
    r"\s*(?=$|[.!?\n])",
    re.I,
)


@dataclass(frozen=True)
class VenueRemoval:
    """An explicit removal in this request, not a guessed persistent preference."""

    venue: str
    day_index: int | None = None


@dataclass(frozen=True)
class MealRequirement:
    """Literal requested specialty in an explicit day/meal edit; not menu verification."""

    day_index: int
    meal: str
    specialty: str


def resolve_meal_requirements(request: str) -> list[MealRequirement]:
    pattern = re.compile(
        r"(?:^|[.!?\n])\s*(?:please\s+)?change\s+day\s+(?P<day>\d+)\s+"
        r"(?P<meal>breakfast|brunch|lunch|dinner)\s+to\s+(?:an?\s+)?"
        r"(?P<specialty>[a-z0-9][a-z0-9' -]{0,59}?)\s+(?:place|restaurant)\b",
        re.I,
    )
    return [
        MealRequirement(int(match["day"]) - 1, match["meal"].lower(), match["specialty"].strip())
        for match in pattern.finditer(request)
        if match["specialty"].strip().lower()
        not in {"different", "another", "new", "nearby", "local", "cheaper", "less expensive"}
    ]


def resolve_removals(request: str) -> list[VenueRemoval]:
    """Recognize standalone 'Remove X [from day N]' clauses conservatively."""
    return [
        VenueRemoval(
            venue=match["venue"].strip().strip("\"'"),
            day_index=int(match["day"]) - 1 if match["day"] else None,
        )
        for match in _REMOVAL.finditer(request)
    ]


def contains_venue(text: str, venue: str) -> bool:
    """Case/whitespace insensitive literal names with Latin word boundaries."""
    needle = " ".join(venue.casefold().split())
    haystack = " ".join(text.casefold().split())
    if not needle:
        return False
    start = r"(?<![a-z0-9_])" if needle[0].isascii() and needle[0].isalnum() else ""
    end = r"(?![a-z0-9_])" if needle[-1].isascii() and needle[-1].isalnum() else ""
    return bool(re.search(start + re.escape(needle) + end, haystack))


def prune_removed_recommendations(
    plan: Itinerary, removals: list[VenueRemoval], *, locked_days: frozenset[int] = frozenset()
) -> Itinerary:
    """Drop complete excluded recommendation items, never rename or hide a real visit."""
    if not removals:
        return plan
    changed = plan.model_copy(deep=True)
    for day_index, day in enumerate(changed.days):
        if day_index in locked_days:
            continue
        names = [r.venue for r in removals if r.day_index is None or r.day_index == day_index]
        for activity in day.activities:
            activity.highlights = [
                item
                for item in activity.highlights
                if not any(contains_venue(item, name) for name in names)
            ]
    return changed


_DAY = re.compile(r"\bday\s+(\d+)\b", re.I)
_LOCKED_DAY = re.compile(
    r"\b(?:leave|keep)\s+day\s+(\d+)\s+(?:completely\s+)?(?:unchanged|exactly\s+as\s+it\s+is)",
    re.I,
)

_FRAME_PATTERNS = {
    "destination": re.compile(r"\b(?:destination|city)\b", re.I),
    "start_date": re.compile(r"\b(?:date|dates|move|shift|reschedule)\b|\d{4}-\d{2}-\d{2}", re.I),
    "end_date": re.compile(r"\b(?:date|dates|move|shift|reschedule)\b|\d{4}-\d{2}-\d{2}", re.I),
    "travelers": re.compile(r"\b(?:travellers?|travelers?|people|adults|party\s+size)\b", re.I),
    "currency": re.compile(
        r"\bcurrency\b|\b(?:switch|change|convert)\b[^.]{0,30}\b(?:USD|EUR|GBP|CAD|AUD)\b",
        re.I,
    ),
    "budget": re.compile(r"\bbudget\b", re.I),
}
_LOCKED_FRAME_PATTERNS = {
    "destination": re.compile(r"\b(?:keep|leave)\b[^.]{0,80}\b(?:destination|city)\b", re.I),
    "start_date": re.compile(r"\b(?:keep|leave)\b[^.]{0,80}\bdates?\b", re.I),
    "end_date": re.compile(r"\b(?:keep|leave)\b[^.]{0,80}\bdates?\b", re.I),
    "travelers": re.compile(
        r"\b(?:keep|leave)\b[^.]{0,80}\b(?:party\s+size|travellers?|travelers?)\b", re.I
    ),
    "currency": re.compile(r"\b(?:keep|leave)\b[^.]{0,80}\bcurrency\b", re.I),
    "budget": re.compile(r"\b(?:keep|leave)\b[^.]{0,80}\bbudget\b", re.I),
}


@dataclass(frozen=True)
class RevisionScope:
    """Fields and day indexes a revision may change.

    Empty ``locked_days`` means the request did not identify a narrow day scope. The
    model then sees the whole itinerary, while the trip frame remains locked unless the
    request explicitly names a frame field.
    """

    editable_days: frozenset[int]
    locked_days: frozenset[int]
    editable_frame: frozenset[str]

    @property
    def is_day_scoped(self) -> bool:
        return bool(self.locked_days)


def resolve_revision_scope(request: str, day_count: int) -> RevisionScope:
    """Read explicit ``day N`` edit/lock clauses without guessing ambiguous prose."""
    mentioned = {int(match.group(1)) - 1 for match in _DAY.finditer(request)}
    explicitly_locked = {int(match.group(1)) - 1 for match in _LOCKED_DAY.finditer(request)}
    valid = set(range(day_count))
    explicitly_locked &= valid
    editable = (mentioned - explicitly_locked) & valid

    if editable:
        locked = valid - editable
    else:
        locked = explicitly_locked

    editable_frame = frozenset(
        field
        for field, pattern in _FRAME_PATTERNS.items()
        if pattern.search(request) and not _LOCKED_FRAME_PATTERNS[field].search(request)
    )
    return RevisionScope(
        editable_days=frozenset(editable),
        locked_days=frozenset(locked),
        editable_frame=editable_frame,
    )


def revision_payload(previous: Itinerary, scope: RevisionScope) -> str:
    """Render only the old details the model needs for this edit.

    Locked days retain their index and date but omit activities. The server owns their
    exact content and restores it after every generation and repair turn.
    """
    if not scope.is_day_scoped:
        return previous.model_dump_json(indent=None)

    payload = previous.model_dump(mode="json", exclude_computed_fields=True)
    compact_days: list[dict] = []
    for index, day in enumerate(previous.days):
        if index in scope.locked_days:
            compact_days.append(
                {
                    "date": day.date.isoformat(),
                    "summary": "LOCKED: preserved server-side",
                    "weather": None,
                    "activities": [],
                    "locked": True,
                    "locked_estimated_cost": day.estimated_cost,
                }
            )
        else:
            compact_days.append(day.model_dump(mode="json", exclude_computed_fields=True))
    payload["days"] = compact_days
    payload["locked_estimated_cost"] = round(
        sum(previous.days[index].estimated_cost for index in scope.locked_days), 2
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def enforce_revision_scope(
    candidate: Itinerary | None,
    previous: Itinerary | None,
    scope: RevisionScope | None,
) -> Itinerary | None:
    """Restore every field the current request did not authorize changing."""
    if candidate is None or previous is None or scope is None:
        return candidate

    values = candidate.model_dump(mode="python", exclude_computed_fields=True)
    for field in _FRAME_PATTERNS:
        if field not in scope.editable_frame:
            values[field] = getattr(previous, field)

    if scope.locked_days:
        merged: list[DayPlan] = []
        for index, old_day in enumerate(previous.days):
            if index in scope.locked_days or index >= len(candidate.days):
                merged.append(old_day)
            else:
                merged.append(candidate.days[index])
        values["days"] = merged

    return Itinerary.model_validate(values)
