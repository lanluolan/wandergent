"""Itinerary data model.

One model serves three consumers: it is what the LLM must emit, what the API hands
to the Android client, and what the Phase 3 constraint-validation layer will check.

Times are plain "HH:MM" strings because they are wall-clock local times, not
instants. Costs are never taken from the model's own arithmetic -- day and trip
totals are computed from the activities, so the LLM cannot claim a plan fits the
budget by mis-adding.
"""

import json
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

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
    indoor: bool | None = Field(
        default=None,
        description="True if the activity is indoors. Used to reshuffle plans around rain.",
    )
    estimated_cost: float = Field(
        default=0.0, ge=0, description="Estimated cost for the whole party, in the trip currency."
    )
    highlights: list[str] = Field(
        default_factory=list,
        description=(
            "2-4 concrete specifics: signature dishes at a restaurant, the exhibits worth "
            "the queue at a museum, the room type at a hotel. Named things, not adjectives."
        ),
    )
    notes: str | None = Field(
        default=None,
        description=(
            "Caveats only: booking needed, closed on Mondays, cash only. "
            "Recommendations belong in highlights, not here."
        ),
    )

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

    @computed_field
    @property
    def estimated_cost(self) -> float:
        """Day total, derived from the activities rather than trusted from the model."""
        return round(sum(item.estimated_cost for item in self.activities), 2)


class Itinerary(BaseModel):
    """A complete trip plan."""

    destination: str
    start_date: date
    end_date: date
    travelers: int = Field(default=1, ge=1)
    currency: str = Field(default="CNY")
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


# JSON Schema keywords that describe the schema to a human rather than telling a model
# what to produce. `title` is the humanised field name, which the key already says;
# `default` never applies because the model is generating, not filling gaps.
# `description` is deliberately **kept**: it carries real constraints ("HH:MM,
# 24-hour"), and losing those would trade prompt tokens for repair rounds.
_DROPPED_SCHEMA_KEYS = frozenset({"title", "default"})

# Maps whose keys are names from *our* model, not schema keywords.
_NAME_KEYED = frozenset({"properties", "$defs"})


def _prune(node: Any, inside_name_map: bool = False) -> Any:
    """Strip schema metadata, structurally.

    The subtlety that makes this worth a function: `title` is both a JSON Schema
    keyword *and* a field on Activity. Dropping the key by name everywhere silently
    deletes `title` from the activity properties while leaving it in `required` -- a
    schema that asks for a field it never describes. So metadata is only dropped where
    the key is a schema keyword, never inside a `properties` or `$defs` map.
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

    This string ships on every LLM call in a run, so its size is multiplied by the
    number of calls. Pruning and minifying cuts it ~28% with no loss of information
    the model needs.
    """
    return json.dumps(
        _prune(Itinerary.model_json_schema()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
