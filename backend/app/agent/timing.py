from datetime import UTC, datetime, time, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Arrival(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    at: AwareDatetime
    location: str = Field(min_length=1)
    buffer_minutes: int = Field(default=60, ge=0, le=1440)


class HotelStay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    hotel: str = Field(min_length=1)
    check_in: AwareDatetime
    check_out: AwareDatetime
    check_in_minutes: int = Field(default=30, ge=1, le=1440)
    check_out_minutes: int = Field(default=15, ge=1, le=1440)

    @model_validator(mode="after")
    def ordered(self):
        if self.check_out.astimezone(UTC) <= self.check_in.astimezone(UTC):
            raise ValueError("hotel check_out must follow check_in in UTC")
        return self


class Journey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    mode: Literal["FLIGHT", "TRAIN", "DRIVE", "TRANSIT"]
    departure: AwareDatetime
    arrival: AwareDatetime
    departure_buffer_minutes: int = Field(default=0, ge=0, le=1440)
    arrival_buffer_minutes: int = Field(default=0, ge=0, le=1440)
    timing_confidence: Literal["confirmed", "estimate"] = "estimate"

    @model_validator(mode="after")
    def ordered(self):
        if self.arrival.astimezone(UTC) <= self.departure.astimezone(UTC):
            raise ValueError("journey arrival must follow departure in UTC")
        return self


class TimingContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arrival: Arrival | None = None
    hotel_stays: list[HotelStay] | None = None
    journeys: list[Journey] | None = None


def check_timing(plan, constraints) -> list[dict]:
    arrival = constraints.arrival if constraints else None
    stays = {stay.id: stay for stay in (constraints.hotel_stays or [])} if constraints else {}
    journeys = {leg.id: leg for leg in (constraints.journeys or [])} if constraints else {}
    timed = bool(arrival or stays or journeys) or any(
        activity.start_at is not None or activity.travel_mode in {"FLIGHT", "TRAIN"}
        for day in plan.days
        for activity in day.activities
    )
    issues = []
    scheduled = []
    seen_journeys = set()
    hotel_actions = set()

    def issue(code, message, day=None):
        issues.append({"code": code, "message": message, "day": day})

    for day in plan.days:
        for activity in day.activities:
            if activity.travel_mode in {"FLIGHT", "TRAIN"} and not activity.journey_id:
                issue(
                    "temporal_unverified",
                    "Flights and long-distance trains require a request-owned journey_id.",
                    day.date,
                )
            if activity.start_at is None or activity.end_at is None:
                if timed:
                    issue(
                        "temporal_unverified",
                        f"On {day.date}, '{activity.title}' needs start_at and end_at "
                        "with UTC offsets to check arrival, lodging and journeys; do not guess.",
                        day.date,
                    )
                continue
            start = activity.start_at.astimezone(UTC)
            end = activity.end_at.astimezone(UTC)
            if (
                end <= start
                or activity.start_time != activity.start_at.strftime("%H:%M")
                or activity.end_time != activity.end_at.strftime("%H:%M")
            ):
                issue(
                    "constraint_mismatch",
                    "Absolute timestamps must be ordered and match local clock labels.",
                    day.date,
                )
                continue
            scheduled.append((start, end, day.date, activity))
            if activity.start_at.date() != day.date:
                issue(
                    "constraint_mismatch",
                    "An activity belongs on its local departure date.",
                    day.date,
                )
            if arrival and not activity.journey_id:
                earliest = arrival.at.astimezone(UTC) + timedelta(minutes=arrival.buffer_minutes)
                if start < earliest:
                    issue(
                        "arrival_conflict",
                        f"'{activity.title}' starts before arrival at {arrival.location} plus "
                        f"{arrival.buffer_minutes} minutes of buffer. "
                        f"Earliest start: {earliest.isoformat()}.",
                        day.date,
                    )
            if activity.journey_id:
                leg = journeys.get(activity.journey_id)
                if leg is None:
                    issue(
                        "constraint_mismatch",
                        "Journey references must name a request-owned journey.",
                        day.date,
                    )
                else:
                    if leg.id in seen_journeys:
                        issue(
                            "journey_conflict",
                            f"Journey {leg.id} is scheduled more than once.",
                            day.date,
                        )
                    seen_journeys.add(leg.id)
                    if (
                        activity.start_at.isoformat() != leg.departure.isoformat()
                        or activity.end_at.isoformat() != leg.arrival.isoformat()
                        or activity.travel_mode != leg.mode
                        or (activity.location or "").strip().casefold()
                        != leg.origin.strip().casefold()
                    ):
                        issue(
                            "journey_conflict",
                            f"Preserve journey {leg.id}'s origin, mode and local timestamps: "
                            f"{leg.departure.isoformat()} to {leg.arrival.isoformat()}. "
                            "Do not move a booking.",
                            day.date,
                        )
            if activity.hotel_stay_id:
                stay = stays.get(activity.hotel_stay_id)
                if stay is None:
                    issue(
                        "constraint_mismatch",
                        "Hotel references must name a request-owned stay.",
                        day.date,
                    )
                else:
                    earliest, latest = stay.check_in.astimezone(UTC), stay.check_out.astimezone(UTC)
                    action = activity.lodging_action
                    minimum = (
                        stay.check_in_minutes if action == "check_in" else stay.check_out_minutes
                    )
                    if (
                        start < earliest
                        or end > latest
                        or activity.start_at.utcoffset()
                        not in {stay.check_in.utcoffset(), stay.check_out.utcoffset()}
                        or activity.end_at.utcoffset()
                        not in {stay.check_in.utcoffset(), stay.check_out.utcoffset()}
                        or (activity.location or "").strip().casefold()
                        != stay.hotel.strip().casefold()
                        or (
                            action in {"check_in", "check_out"}
                            and (end - start).total_seconds() < minimum * 60
                        )
                    ):
                        issue(
                            "hotel_window_conflict",
                            f"'{activity.title}' must use {stay.hotel} within "
                            f"{stay.check_in.isoformat()} to {stay.check_out.isoformat()}; "
                            "early room access requires an explicitly changed check-in window.",
                            day.date,
                        )
                    key = (stay.id, action)
                    if action in {"check_in", "check_out"} and key in hotel_actions:
                        issue(
                            "hotel_window_conflict",
                            f"Duplicate {action} for stay {stay.id}.",
                            day.date,
                        )
                    hotel_actions.add(key)
            elif timed and activity.category == "accommodation":
                issue(
                    "temporal_unverified",
                    "Provide the hotel stay's check-in and check-out window "
                    "and hotel_stay_id before validating room availability.",
                    day.date,
                )

    scheduled.sort(key=lambda entry: entry[0])
    for left_index, left in enumerate(scheduled):
        for right in scheduled[left_index + 1 :]:
            if right[0] >= left[1]:
                break
            issue(
                "time_conflict",
                f"'{left[3].title}' and '{right[3].title}' overlap in UTC.",
                right[2],
            )
    for leg in journeys.values():
        reserved_start = leg.departure.astimezone(UTC) - timedelta(
            minutes=leg.departure_buffer_minutes
        )
        reserved_end = leg.arrival.astimezone(UTC) + timedelta(minutes=leg.arrival_buffer_minutes)
        for start, end, day, activity in scheduled:
            if activity.journey_id != leg.id and start < reserved_end and end > reserved_start:
                issue(
                    "journey_conflict",
                    f"'{activity.title}' overlaps journey {leg.id} or its buffers. "
                    f"UTC window: {reserved_start.isoformat()} to {reserved_end.isoformat()}.",
                    day,
                )
        if plan.start_date <= leg.departure.date() <= plan.end_date and leg.id not in seen_journeys:
            issue(
                "journey_conflict",
                f"Schedule request-owned journey {leg.id} on its departure date.",
            )
        if leg.timing_confidence == "estimate":
            issue(
                "journey_time_estimated",
                f"Journey {leg.id} uses estimated times, not a verified timetable.",
            )
    for stay in stays.values():
        for action, moment in (("check_in", stay.check_in), ("check_out", stay.check_out)):
            if (
                plan.start_date <= moment.date() <= plan.end_date
                and (stay.id, action) not in hotel_actions
            ):
                issue(
                    "hotel_window_conflict",
                    f"Schedule {action} for {stay.hotel} within the confirmed stay window.",
                )
    ordered_legs = sorted(journeys.values(), key=lambda leg: leg.departure.astimezone(UTC))
    for left, right in zip(ordered_legs, ordered_legs[1:], strict=False):
        earliest = left.arrival.astimezone(UTC) + timedelta(minutes=left.arrival_buffer_minutes)
        latest = right.departure.astimezone(UTC) - timedelta(minutes=right.departure_buffer_minutes)
        if earliest > latest:
            issue(
                "journey_conflict",
                f"Journeys {left.id} and {right.id} overlap or lack connection buffer.",
            )
    return issues


def missing_nights(plan, constraints) -> list:
    missing = []
    night = plan.start_date
    while night < plan.end_date:
        covered = any(
            stay.check_in.astimezone(UTC)
            <= datetime.combine(night + timedelta(days=1), time(), stay.check_in.tzinfo).astimezone(
                UTC
            )
            < stay.check_out.astimezone(UTC)
            for stay in constraints.hotel_stays or []
        )
        covered = covered or any(
            leg.departure.astimezone(UTC)
            <= datetime.combine(night + timedelta(days=1), time(), zone).astimezone(UTC)
            < leg.arrival.astimezone(UTC)
            for leg in constraints.journeys or []
            for zone in (leg.departure.tzinfo, leg.arrival.tzinfo)
        )
        if not covered:
            missing.append(night)
        night += timedelta(days=1)
    return missing
