"""Request-owned constraints. Generated itinerary fields never update this snapshot.

Structured input is authoritative. The small text reader only recognizes explicit,
unambiguous forms; it does not claim to understand arbitrary natural language.
"""

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TravelMode = Literal["WALK", "TRANSIT", "DRIVE"]


class TripConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    budget: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=60)
    travelers: int | None = Field(default=None, ge=1, le=100)
    allowed_modes: list[TravelMode] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def consistent_dates(self) -> "TripConstraints":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if self.start_date and self.end_date and self.days is not None:
            inclusive_days = (self.end_date - self.start_date).days + 1
            if self.days != inclusive_days:
                raise ValueError("days must match the inclusive date range")
        return self


def starts_new_trip(request: str) -> bool:
    return bool(re.match(r"\s*(?:new trip\b|start a new trip\b|新旅行|新的旅行)", request, re.I))


def resolve_constraints(
    request: str,
    *,
    previous: TripConstraints | None = None,
    confirmed: TripConstraints | None = None,
    currency: str = "",
) -> TripConstraints:
    """Keep prior request facts unless this request explicitly replaces them.

    An API client can clear a field with an explicit null in `confirmed`. Missing fields
    inherit. Dates in prose must be ISO; relative dates remain unconfirmed.
    """
    values = previous.model_dump() if previous and not starts_new_trip(request) else {}
    text = request.casefold()
    amount = re.search(
        r"(?:\bbudget\b|预算)\s*(?:is|of|to|:|为|改为)?\s*"
        r"(?P<prefix>\$|usd\s*)?(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)"
        r"\s*(?P<currency>usd|eur|gbp|cad|aud)?\b",
        text,
    )
    if amount:
        values["budget"] = float(amount["amount"].replace(",", ""))
        unit = amount["currency"] or ("USD" if amount["prefix"] else None)
        if unit:
            values["currency"] = unit.upper()
    duration = re.search(r"\b(\d+)\s+days?\b|([0-9]+)\s*天", text)
    if duration:
        values["days"] = int(duration[1] or duration[2])
        if values.get("start_date") and values.get("end_date"):
            values["end_date"] = values["start_date"] + timedelta(days=values["days"] - 1)
    party = re.search(r"\b(\d+)\s+(?:travelers|travellers|people|adults)\b|([0-9]+)\s*人", text)
    if party:
        values["travelers"] = int(party[1] or party[2])
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", request)
    if 1 <= len(dates) <= 2:
        try:
            start = date.fromisoformat(dates[0])
            end = date.fromisoformat(dates[1]) if len(dates) == 2 else None
        except ValueError:
            # Malformed text is not a confirmed date. Structured input rejects it.
            pass
        else:
            values["start_date"] = start
            if end is not None:
                values["end_date"] = end
                values["days"] = (end - start).days + 1
            elif values.get("days"):
                values["end_date"] = start + timedelta(days=values["days"] - 1)
    if re.search(r"\b(?:walking only|walk only|only walk|on foot only)\b|只步行", text):
        values["allowed_modes"] = ["WALK"]
    elif re.search(
        r"\b(?:no driving|no car|do not drive|public transport only|transit only)\b|不开车", text
    ):
        values["allowed_modes"] = ["WALK", "TRANSIT"]
    if currency and not values.get("currency"):
        values["currency"] = currency.upper()
    if confirmed is not None:
        values.update(confirmed.model_dump(exclude_unset=True))
    return TripConstraints.model_validate(values)
