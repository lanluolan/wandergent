"""Request-owned constraints. Generated itinerary fields never update this snapshot.

Structured input is authoritative. The small text reader only recognizes explicit,
unambiguous forms; it does not claim to understand arbitrary natural language.
"""

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.timing import Arrival, HotelStay, Journey

TravelMode = Literal["WALK", "TRANSIT", "DRIVE", "FLIGHT", "TRAIN"]


class TripConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    budget: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=60)
    travelers: int | None = Field(default=None, ge=1, le=100)
    allowed_modes: list[TravelMode] | None = Field(default=None, min_length=1)
    lodging_arranged: bool | None = None
    weather_fallback_dates: list[date] | None = Field(default=None, max_length=60)
    arrival: Arrival | None = None
    hotel_stays: list[HotelStay] | None = Field(default=None, max_length=60)
    journeys: list[Journey] | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def consistent_dates(self) -> "TripConstraints":
        for collection in (self.hotel_stays or [], self.journeys or []):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError(
                    "hotel stay and journey IDs must be unique within their collection"
                )
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if self.start_date and self.end_date and self.days is not None:
            inclusive_days = (self.end_date - self.start_date).days + 1
            if self.days != inclusive_days:
                raise ValueError("days must match the inclusive date range")
        return self


def starts_new_trip(request: str) -> bool:
    return bool(re.match(r"\s*(?:new trip\b|start a new trip\b|新旅行|新的旅行)", request, re.I))


def _timing_from_text(request: str) -> dict:
    stamp = r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}(?::\d{2})?(?:[Zz]|[+-]\d{2}:\d{2})"
    values = {}
    arrival = re.search(
        rf"(?:\barrival|抵达)\s*[:：]\s*(?P<location>[^,，;\n]+)[,，]\s*(?P<at>{stamp})",
        request,
        re.I,
    )
    if arrival:
        try:
            values["arrival"] = Arrival.model_validate(arrival.groupdict())
        except ValueError:
            pass
    hotels = re.finditer(
        rf"\bhotel\s+(?P<id>[\w-]+)\s*:\s*(?P<hotel>[^,;\n]+),\s*"
        rf"check-in\s+(?P<check_in>{stamp}),\s*check-out\s+(?P<check_out>{stamp})",
        request,
        re.I,
    )
    stays = []
    for match in hotels:
        try:
            stays.append(HotelStay.model_validate(match.groupdict()))
        except ValueError:
            pass
    if stays:
        values["hotel_stays"] = stays
    legs = re.finditer(
        rf"\bjourney\s+(?P<id>[\w-]+)\s*:\s*(?P<mode>FLIGHT|TRAIN|DRIVE|TRANSIT),\s*"
        rf"(?P<origin>[^,;\n]+?)\s*(?:->|→)\s*(?P<destination>[^,;\n]+),\s*"
        rf"(?P<departure>{stamp})\s*(?:->|→)\s*(?P<arrival>{stamp})",
        request,
        re.I,
    )
    journeys = []
    for match in legs:
        try:
            payload = match.groupdict()
            payload["mode"] = payload["mode"].upper()
            journeys.append(Journey.model_validate(payload))
        except ValueError:
            pass
    if journeys:
        values["journeys"] = journeys
    return values


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
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b(?![Tt])", request)
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
    for clause in re.split(r"[.;\n]", text):
        lodging = r"(?:lodging|accommodation|hotel)"
        if re.search(
            rf"\b{lodging}\s+(?:is\s+)?not\s+(?:already\s+)?(?:arranged|booked|handled)\b"
            rf"|\b(?:book|find|choose)\s+(?:a\s+|the\s+)?{lodging}\b",
            clause,
        ):
            values["lodging_arranged"] = False
        elif not re.search(r"\b(?:not|never|assume|if|unless|maybe|might)\b", clause) and re.search(
            rf"\b{lodging}\s+(?:is\s+)?already\s+(?:arranged|booked|handled)\b"
            rf"|\bi\s+have\s+(?:already\s+)?booked\s+(?:a\s+|the\s+)?{lodging}\b",
            clause,
        ):
            values["lodging_arranged"] = True
    if currency and not values.get("currency"):
        values["currency"] = currency.upper()
    values.update(_timing_from_text(request))
    if confirmed is not None:
        values.update(confirmed.model_dump(exclude_unset=True))
    return TripConstraints.model_validate(values)
