"""Tell the model how far apart its candidate venues are, before it writes the schedule.

Without this the schedule is written blind: `search_places` returns coordinates, and a
model choosing times with no idea whether two venues are next door or across the city
writes a 14-minute walk into a 0-minute gap -- caught only afterwards by a Routes
measurement and a repair round, at the cost of a whole extra generation.

The cheap half of the answer: straight-line distance between two known points is
arithmetic, not an API call, so every venue the run looked up can be described for free.
Presented as an *estimate* -- it knows nothing of rivers, one-way systems or a direct
route up a cliff. `get_travel_time` is the authority and `transfers.py` still confirms.
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
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, max(0.0, inner))))


def walk_minutes(straight_km: float) -> int:
    """Rough on-foot minutes for a straight-line distance, rounded up to the minute."""
    return math.ceil(straight_km * STREET_FACTOR / WALK_KMH * 60)


def _describe(straight_km: float) -> str:
    minutes = walk_minutes(straight_km)
    if minutes > WALKABLE_MINUTES:
        return f"{straight_km:.1f} km apart -- too far to walk, allow transit or a taxi"
    return f"{straight_km:.1f} km apart, about {minutes} min on foot"


def clusters(points: dict[str, tuple[float, float]]) -> list[list[str]]:
    groups: list[list[str]] = []
    for name in sorted(points):
        nearby = next(
            (
                group
                for group in groups
                if all(
                    walk_minutes(haversine_km(points[name], points[other])) <= WALKABLE_MINUTES
                    for other in group
                )
            ),
            None,
        )
        if nearby is None:
            groups.append([name])
        else:
            nearby.append(name)
    return groups


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
    groups = clusters(dict(named))
    if len(groups) > 1:
        lines.append(
            "Nearby groups (coordinate estimates, not route verification): "
            + "; ".join(", ".join(group) for group in groups)
            + ". Keep each group together where opening hours and reservations allow; "
            "avoid returning between groups. Measure actual transfers with get_travel_time."
        )
    if dropped_venues or dropped_pairs:
        lines.append(
            f"({dropped_venues} further venue(s) and {dropped_pairs} further pair(s) "
            "not listed; ask get_travel_time about anything missing.)"
        )
    return "\n".join(lines)
