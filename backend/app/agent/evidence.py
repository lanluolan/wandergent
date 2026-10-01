"""Bind only exact venue identities to final activities; guesses stay unverified."""

from app.agent.results import ActivityEvidence, ToolCallRecord
from app.agent.schemas import Itinerary


def activity_evidence(
    itinerary: Itinerary | None, records: list[ToolCallRecord], routes: list[dict] | None = None
) -> list[ActivityEvidence]:
    if itinerary is None:
        return []
    venues = []
    for record in records:
        if record.ok and record.name == "search_places":
            for place in record.fact_payload.get("places") or []:
                if isinstance(place, dict) and place.get("name"):
                    venues.append((place, record.collected_at))
    result = []
    for day_index, day in enumerate(itinerary.days):
        for activity_index, activity in enumerate(day.activities):
            location = (activity.location or "").strip().casefold()
            matches = [
                (place, timestamp)
                for place, timestamp in venues
                if location
                in {
                    str(place["name"]).strip().casefold(),
                    str(place.get("address") or "").strip().casefold(),
                    f"{place['name']}, {place.get('address') or ''}".strip().casefold(),
                }
                and location
            ]
            evidence = ActivityEvidence(day_index=day_index, activity_index=activity_index)
            # Prefer the newest observation; never label a partial substring as verified.
            identities = {(m[0]["name"], m[0].get("address")) for m in matches}
            if matches and len(identities) == 1:
                place, timestamp = max(matches, key=lambda match: match[1] or "")
                evidence.source = "Google Places"
                evidence.collected_at = timestamp
                evidence.venue_verified = True
                evidence.hours_available = bool(place.get("opening_hours"))
                evidence.price_level_available = bool(place.get("price_level"))
            if activity_index and activity.category != "transport":
                earlier = sorted(
                    (
                        a
                        for a in day.activities
                        if a.end_time <= activity.start_time
                        and a.category != "transport"
                        and a.location
                    ),
                    key=lambda a: a.start_time,
                )
                previous = earlier[-1] if earlier else None
                match = next(
                    (
                        r
                        for r in reversed(routes or [])
                        if previous
                        and r["origin"] == previous.location
                        and r["destination"] == activity.location
                        and r.get("day") == str(day.date)
                        and r["seconds"] is not None
                    ),
                    None,
                )
                if match:
                    evidence.route_source = "Google Routes"
                    evidence.route_collected_at = match["collected_at"]
                    evidence.route_departure = match["departure"]
                    evidence.route_mode = match["mode"]
                    evidence.route_seconds = match["seconds"]
            result.append(evidence)
    return result
