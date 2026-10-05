"""Distil what the run looked up into something the model can act on.

The facts are already in the conversation, as raw `search_places` JSON: ~350 characters
per venue, `"ok":true,"error":null` on every object, seven weekday lines, thirteen decimal
places of latitude. A model composing three days must hold ten of those and
cross-reference them while choosing times -- sufficient information, close to unusable.

So this changes no data. It restates what the run already paid for in the shape the next
decision needs: one line per venue, plus the distances between them. The failures it
targets -- a 14-minute walk in a 0-minute gap, a museum booked on the day it is shut --
were both written by a model that had the answer in a form it could not use.
"""

from app.agent import opening_hours, proximity
from app.agent.opening_hours import WEEKDAYS
from app.tools.money import range_average

#: Mon-first, matching how opening hours read and how people think about a week.
_ORDER = list(WEEKDAYS)
_SHORT = {day: day[:3].capitalize() for day in _ORDER}

#: One line per venue plus the pair list is O(n^2); the cap keeps the block bounded.
#: Truncation is always announced -- a shortened list reads as the complete one.
MAX_VENUES = proximity.MAX_VENUES


def summarize_hours(descriptions: list[str]) -> str | None:
    """Seven weekday lines compressed to one, or None if nothing could be read.

    Consecutive days sharing hours collapse ("Wed-Sun 11:00-17:00"), which is how a week
    reads and which makes the one day that differs impossible to miss.
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
        if (
            groups
            and groups[-1][1] == spec
            and _ORDER.index(groups[-1][0][-1]) + 1 == _ORDER.index(day)
        ):
            groups[-1][0].append(day)
        else:
            groups.append(([day], spec))

    parts = []
    for days, spec in groups:
        label = _SHORT[days[0]] if len(days) == 1 else f"{_SHORT[days[0]]}-{_SHORT[days[-1]]}"
        parts.append(f"{label} {spec}")
    for day, windows in sorted(parsed.items()):
        if day in WEEKDAYS:
            continue
        spec = (
            ", ".join(f"{_clock(start)}-{_clock(end)}" for start, end in windows)
            if windows
            else "closed"
        )
        parts.append(f"{day} override {spec}")
    return "; ".join(parts) or None


def _clock(minute: int) -> str:
    return opening_hours._clock(minute)


def render(
    points: dict[str, tuple[float, float]],
    hours: dict[str, list[str]],
    prices: dict[str, dict],
    addresses: dict[str, str] | None = None,
    *,
    collected_at: str | None = None,
) -> str | None:
    """The whole brief, or None when the run has not looked anything up yet."""
    addresses = addresses or {}
    names = list(dict.fromkeys([*points, *hours, *prices, *addresses]))
    shown, dropped = names[:MAX_VENUES], len(names) - len(names[:MAX_VENUES])
    if not shown:
        return None

    lines = [
        "What you have verified so far. Plan from these -- anything not listed here you "
        "have not checked, so either search for it or say in notes that it is unverified."
    ]
    for name in shown:
        facts = []
        if addresses.get(name):
            facts.append(f"address {addresses[name]}")
        if name in points:
            latitude, longitude = points[name]
            facts.append(f"coordinates {latitude:.5f},{longitude:.5f}")
        summary = summarize_hours(hours.get(name) or [])
        facts.append(summary if summary else "hours not published")
        average = range_average(prices.get(name))
        if average:
            facts.append(
                f"restaurant per-person estimate {average.amount:.2f} {average.currency} "
                "(priceRange midpoint, not a quote)"
            )
        elif name in prices:
            facts.append("restaurant priceRange incomplete; midpoint unknown")
        lines.append(f"- {name}: {' | '.join(facts)}")

    freshness = "source=Google Places"
    if collected_at:
        freshness += f" | collected_at={collected_at}"
    lines.append(f"Fact provenance: {freshness}. Refresh on a new planning run.")

    distances = proximity.render({name: points[name] for name in shown if name in points})
    if distances:
        lines.append("")
        lines.append(distances)
    if dropped:
        lines.append(f"({dropped} further venue(s) not listed.)")
    return "\n".join(lines)
