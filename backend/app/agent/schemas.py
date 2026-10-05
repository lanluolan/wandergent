"""Itinerary data model.

One model serves three consumers: what the LLM must emit, what the API hands the Android
client, and what the constraint layer checks.

Times are plain "HH:MM" strings because they are wall-clock local times, not instants.
Costs never come from the model's arithmetic -- day and trip totals are computed from the
activities, so it cannot claim a plan fits the budget by mis-adding.
"""

import json
import re
from datetime import UTC, date
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.agent.timing import TimingContext
from app.tools.place_summary import PlaceSummary

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

ACTIVITY_CATEGORIES = (
    "sightseeing",
    "food",
    "transport",
    "accommodation",
    "activity",
    "rest",
    "other",
)


def _validate_hhmm(value: str) -> str:
    """Reject anything that is not a 24-hour HH:MM wall-clock time."""
    if not TIME_PATTERN.match(value):
        raise ValueError(f"time must be HH:MM in 24-hour form, got {value!r}")
    return value


class Activity(BaseModel):
    """A single time-boxed item in a day."""

    start_time: str = Field(description="Local start time, HH:MM, 24-hour.")
    end_time: str = Field(description="Local end time, HH:MM, 24-hour.")
    title: str = Field(description="Short name of the activity.")
    category: str = Field(
        default="other",
        description=f"One of: {', '.join(ACTIVITY_CATEGORIES)}.",
    )
    location: str | None = Field(default=None, description="Place name or address.")
    travel_mode: Literal["WALK", "TRANSIT", "DRIVE", "FLIGHT", "TRAIN"] | None = Field(
        default=None, description="For transport activities, the actual mode of this leg."
    )
    indoor: bool | None = Field(
        default=None,
        description="True if the activity is indoors. Used to reshuffle plans around rain.",
    )
    estimated_cost: float = Field(
        default=0.0, ge=0, description="Estimated cost for the whole party, in the trip currency."
    )
    transport_base_cost: float | None = Field(
        default=None,
        ge=0,
        description=(
            "DRIVE fuel/rental estimate for the whole party, excluding tolls, in trip currency."
        ),
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Legacy field; return an empty list. Venue summaries are attached server-side.",
    )
    place_summary: PlaceSummary | None = Field(
        default=None,
        description="Server-provided Google editorial summary. Return null; never rewrite it.",
    )
    notes: str | None = Field(
        default=None,
        description=(
            "Caveats only: booking needed, closed on Mondays, cash only. "
            "Do not add unsupported dish or exhibit recommendations here."
        ),
    )
    start_at: AwareDatetime | None = Field(
        default=None,
        description="Full local start timestamp with UTC offset; required for timed trips.",
    )
    end_at: AwareDatetime | None = Field(
        default=None,
        description="Full local end timestamp with UTC offset, including arrival date.",
    )
    journey_id: str | None = Field(
        default=None, description="Request-owned long-distance journey ID."
    )
    hotel_stay_id: str | None = Field(default=None, description="Request-owned hotel stay ID.")
    lodging_action: Literal["check_in", "check_out", "stay"] | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        return _validate_hhmm(value)

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        """Fall back to 'other' rather than failing the whole plan on a stray label."""
        return value if value in ACTIVITY_CATEGORIES else "other"

    @model_validator(mode="after")
    def _end_after_start(self) -> "Activity":
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError("start_at and end_at must be supplied together")
        if self.journey_id and self.category != "transport":
            raise ValueError("journey_id requires a transport activity")
        if (self.hotel_stay_id is None) != (self.lodging_action is None):
            raise ValueError("hotel_stay_id and lodging_action must be supplied together")
        if self.hotel_stay_id and self.category != "accommodation":
            raise ValueError("hotel_stay_id requires an accommodation activity")
        if self.journey_id or self.hotel_stay_id:
            if self.start_at is None:
                raise ValueError("journey and hotel activities require absolute timestamps")
        if self.start_at is not None:
            if (
                self.start_at.strftime("%H:%M") != self.start_time
                or self.end_at.strftime("%H:%M") != self.end_time
            ):
                raise ValueError("local clock labels must match their absolute timestamps")
            if self.end_at.astimezone(UTC) <= self.start_at.astimezone(UTC):
                raise ValueError("end_at must follow start_at in UTC")
            return self
        if self.end_time <= self.start_time:
            raise ValueError(
                f"end_time {self.end_time} must be after start_time {self.start_time} "
                f"for activity {self.title!r}"
            )
        return self


class DayPlan(BaseModel):
    """One day of the trip."""

    date: date
    summary: str = Field(description="One line on the shape of the day.")
    weather: str | None = Field(
        default=None, description="Weather note for this day, from the weather tool."
    )
    activities: list[Activity] = Field(default_factory=list)
    fallback_options: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def estimated_cost(self) -> float:
        """Day total, derived from the activities rather than trusted from the model."""
        return round(sum(item.estimated_cost for item in self.activities), 2)


class TravelGuide(BaseModel):
    trip_summary: str = ""
    assumptions: list[str] = Field(default_factory=list)
    budget_notes: list[str] = Field(default_factory=list)
    ticket_notes: list[str] = Field(default_factory=list)
    transportation_notes: list[str] = Field(default_factory=list)
    food_notes: list[str] = Field(default_factory=list)
    free_paid_notes: list[str] = Field(default_factory=list)
    packing_checklist: list[str] = Field(default_factory=list)
    practical_cautions: list[str] = Field(default_factory=list)
    preparation_timeline: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class Itinerary(BaseModel):
    """A complete trip plan."""

    destination: str
    travel_guide: TravelGuide | None = None
    timing: TimingContext | None = Field(
        default=None, description="Server-owned travel timing context; return null."
    )
    start_date: date
    end_date: date
    travelers: int = Field(default=1, ge=1)
    # ISO 4217 code every cost here is estimated in. A fallback only: a request naming a
    # currency puts it in the prompt, and one that does not lets the model pick the
    # destination's. USD to match the demo destinations and the Android default.
    currency: str = Field(default="USD")
    budget: float | None = Field(
        default=None, ge=0, description="User's stated budget for the whole trip, if any."
    )
    days: list[DayPlan] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list, description="Caveats, assumptions, things to book ahead."
    )

    @computed_field
    @property
    def total_estimated_cost(self) -> float:
        """Trip total, derived from the day plans."""
        return round(sum(day.estimated_cost for day in self.days), 2)

    @model_validator(mode="after")
    def _dates_in_order(self) -> "Itinerary":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


# Keywords that describe the schema to a human rather than telling a model what to
# produce: `title` repeats the key, and `default` never applies to generation.
# `description` is **kept** -- it carries real constraints ("HH:MM, 24-hour"), and losing
# those trades prompt tokens for repair rounds.
_DROPPED_SCHEMA_KEYS = frozenset({"title", "default"})

# Maps whose keys are names from *our* model, not schema keywords.
_NAME_KEYED = frozenset({"properties", "$defs"})


def _prune(node: Any, inside_name_map: bool = False) -> Any:
    """Strip schema metadata, structurally.

    `title` is both a JSON Schema keyword *and* a field on Activity, so dropping it by
    name everywhere deletes `title` from the activity properties while leaving it in
    `required` -- a schema asking for a field it never describes. Metadata is therefore
    dropped only where the key is a schema keyword, never inside `properties` or `$defs`.
    """
    if isinstance(node, dict):
        if inside_name_map:
            return {key: _prune(value) for key, value in node.items()}
        return {
            key: _prune(value, inside_name_map=key in _NAME_KEYED)
            for key, value in node.items()
            if key not in _DROPPED_SCHEMA_KEYS
        }
    if isinstance(node, list):
        return [_prune(item) for item in node]
    return node


def itinerary_schema_json() -> str:
    """The itinerary schema as it goes into the prompt: pruned and minified.

    Ships on every LLM call in a run, so its size is multiplied by the call count.
    Pruning and minifying cuts it ~28% with no loss the model notices.
    """
    return json.dumps(
        _prune(Itinerary.model_json_schema()),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def strict_output_schema(model: type[BaseModel]) -> dict:
    schema = _prune(model.model_json_schema())

    def require_fields(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
                node["additionalProperties"] = False
            for value in node.values():
                require_fields(value)
        elif isinstance(node, list):
            for value in node:
                require_fields(value)

    require_fields(schema)
    return schema
