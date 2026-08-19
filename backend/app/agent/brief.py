"""Distil what the run looked up into something the model can act on.

The facts were always there. `search_places` replies carry hours, price bands and
coordinates, and they sit in the conversation as raw JSON -- roughly 350 characters per
venue, `"ok":true,"error":null` on every object, seven separate weekday lines, thirteen
decimal places of latitude. A model composing three days has to hold ten of those and
cross-reference them while also choosing times. The information is sufficient and close
to unusable.

So this module changes no data and buys nothing new. It restates what the run already
paid for in the form the next decision needs: one line per venue, and the distances
between them. The measured live failure it targets is a 14-minute walk scheduled with a
0-minute gap, and a museum booked on the one day it is shut -- both written by a model
that had the answer in its context in a shape it could not use.
"""

from app.agent import opening_hours, proximity
from app.agent.opening_hours import WEEKDAYS

#: Mon-first, matching how opening hours read and how people think about a week.
_ORDER = list(WEEKDAYS)
_SHORT = {day: day[:3].capitalize() for day in _ORDER}

#: One line per venue plus the pair list is O(n^2); the cap keeps the block bounded.
#: Truncation is always announced -- a shortened list reads as the complete one.
MAX_VENUES = proximity.MAX_VENUES

PRICE_WORDS = {
    "PRICE_LEVEL_FREE": "free",
    "PRICE_LEVEL_INEXPENSIVE": "cheap",
    "PRICE_LEVEL_MODERATE": "mid-priced",
    "PRICE_LEVEL_EXPENSIVE": "expensive",
    "PRICE_LEVEL_VERY_EXPENSIVE": "very expensive",
}


def summarize_hours(descriptions: list[str]) -> str | None:
    """Seven weekday lines compressed to one, or None if nothing could be read.

    Runs of consecutive days sharing the same hours collapse ("Wed-Sun 11:00-17:00"),
    because that is how the week actually looks and because the one day that differs is
    the whole point -- a museum shut on Tuesday should be impossible to miss.
    """
    parsed = opening_hours.parse(descriptions)
    if not parsed:
        return None

    groups: list[tuple[list[str], str]] = []
    for day in _ORDER:
        windows = parsed.get(day)
        if windows is None:
            continue
        spec = (
            ", ".join(f"{_clock(start)}-{_clock(end)}" for start, end in windows)
            if windows
            else "closed"
        )
        if groups and groups[-1][1] == spec:
            groups[-1][0].append(day)
        else:
            groups.append(([day], spec))

    parts = []
    for days, spec in groups:
        label = _SHORT[days[0]] if len(days) == 1 else f"{_SHORT[days[0]]}-{_SHORT[days[-1]]}"
        parts.append(f"{label} {spec}")
    return "; ".join(parts) or None


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def render(
    points: dict[str, tuple[float, float]],
    hours: dict[str, list[str]],
    prices: dict[str, str],
) -> str | None:
    """The whole brief, or None when the run has not looked anything up yet."""
    names = list(dict.fromkeys([*points, *hours, *prices]))
    shown, dropped = names[:MAX_VENUES], len(names) - len(names[:MAX_VENUES])
    if not shown:
        return None

    lines = [
        "What you have verified so far. Plan from these -- anything not listed here you "
        "have not checked, so either search for it or say in notes that it is unverified."
    ]
    for name in shown:
        facts = []
        summary = summarize_hours(hours.get(name) or [])
        facts.append(summary if summary else "hours not published")
        band = PRICE_WORDS.get(prices.get(name, ""))
        if band:
            facts.append(band)
        lines.append(f"- {name}: {' | '.join(facts)}")

    distances = proximity.render({name: points[name] for name in shown if name in points})
    if distances:
        lines.append("")
        lines.append(distances)
    if dropped:
        lines.append(f"({dropped} further venue(s) not listed.)")
    return "\n".join(lines)
