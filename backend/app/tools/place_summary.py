from typing import Literal

from pydantic import BaseModel, Field


class PlaceSummary(BaseModel):
    text: str = Field(min_length=1)
    source: Literal["editorialSummary"] | None = None
    language_code: str | None = None
    collected_at: str | None = None


def google_place_summary(raw: dict | None) -> PlaceSummary | None:
    if not isinstance(raw, dict):
        return None
    try:
        return PlaceSummary(
            text=raw.get("text"),
            source="editorialSummary",
            language_code=raw.get("languageCode"),
        )
    except ValueError:
        return None
