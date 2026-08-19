"""Tell the model how far apart its candidate venues are, before it writes the schedule.

The plan used to be written blind. `search_places` returns coordinates and the run threw
them away, so when the model chose a start and end time for two activities it had no idea
whether they were next door or across the city. Live result: a 14-minute walk scheduled
with a **0-minute** gap, caught only afterwards by a Routes measurement and a repair
round -- which costs a whole extra generation to fix something the model would not have
written had it known.

This is the cheap half of the answer. Straight-line distance between two known points is
arithmetic, not an API call, so every venue the run already looked up can be described to
the model for free. It is deliberately presented as an *estimate*: it does not know about
rivers, one-way systems or the fact that the direct route is up a cliff. `get_travel_time`
remains the authority, and `transfers.py` still confirms the schedule afterwards.
"""

import math

#: Straight lines do not have street corners in them. Measured against a handful of city
#: pairs, actual walking routes run roughly a third longer than the crow flies.
STREET_FACTOR = 1.3

#: Unhurried city walking, with crossings and a map check.
WALK_KMH = 4.5

#: Past this, walking stops being a plan and starts being an ordeal.
WALKABLE_MINUTES = 30

#: Bounds on the block, because it is O(n^2) in the prompt. Truncation is always
#: announced -- a silently shortened list reads as "these are all the places".
MAX_VENUES = 12
MAX_PAIRS = 60

EARTH_RADIUS_KM = 6371.0


def haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Great-circle distance in kilometres between two (latitude, longitude) points."""
    lat1, lon1 = math.radians(first[0]), math.radians(first[1])
    lat2, lon2 = math.radians(second[0]), math.radians(second[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    inner = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(inner))


def walk_minutes(straight_km: float) -> int:
    """Rough on-foot minutes for a straight-line distance, rounded up to the minute."""
    return math.ceil(straight_km * STREET_FACTOR / WALK_KMH * 60)


def _describe(straight_km: float) -> str:
    minutes = walk_minutes(straight_km)
    if minutes > WALKABLE_MINUTES:
        return f"{straight_km:.1f} km apart -- too far to walk, allow transit or a taxi"
    return f"{straight_km:.1f} km apart, about {minutes} min on foot"


def render(points: dict[str, tuple[float, float]]) -> str | None:
    """The distance block to hand the model, or None when there is nothing to say."""
    named = list(points.items())[:MAX_VENUES]
    if len(named) < 2:
        return None

    pairs = [
        f"- {first} <-> {second}: {_describe(haversine_km(here, there))}"
        for index, (first, here) in enumerate(named)
        for second, there in named[index + 1 :]
    ]
    dropped_venues = len(points) - len(named)
    dropped_pairs = max(0, len(pairs) - MAX_PAIRS)
    pairs = pairs[:MAX_PAIRS]

    lines = [
        "How far apart the places you have looked up are, straight-line, with a rough "
        "walking time. Use this to space the day: two things 6 km apart cannot be "
        "scheduled back to back. These are estimates from coordinates, not measurements "
        "-- call get_travel_time for the pairs you actually put next to each other.",
        *pairs,
    ]
    if dropped_venues or dropped_pairs:
        lines.append(
            f"({dropped_venues} further venue(s) and {dropped_pairs} further pair(s) "
            "not listed; ask get_travel_time about anything missing.)"
        )
    return "\n".join(lines)
