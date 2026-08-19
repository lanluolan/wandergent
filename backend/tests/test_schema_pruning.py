"""The pruned schema that ships in every prompt.

Its size is multiplied by the number of LLM calls in a run, so it is worth trimming --
but only of things the model does not need. The first attempt at this deleted a field.
"""

import json

from app.agent.schemas import Itinerary, itinerary_schema_json


def schema() -> dict:
    return json.loads(itinerary_schema_json())


def test_the_activity_title_field_survives() -> None:
    """Regression: `title` is a schema keyword *and* one of our field names.

    Dropping the key by name everywhere removed `title` from the activity properties
    while leaving it in `required` -- a schema demanding a field it never describes.
    """
    activity = schema()["$defs"]["Activity"]

    assert "title" in activity["properties"]
    assert "title" in activity["required"]
    assert activity["properties"]["title"]["type"] == "string"


def test_every_required_field_is_described() -> None:
    """The general form of the bug above, across the whole schema."""
    document = schema()
    definitions = {"Itinerary": document, **document.get("$defs", {})}

    for name, definition in definitions.items():
        properties = definition.get("properties", {})
        for required in definition.get("required", []):
            assert required in properties, f"{name}.{required} is required but not described"


def test_descriptions_are_kept() -> None:
    """They carry constraints; dropping them trades prompt tokens for repair rounds."""
    start_time = schema()["$defs"]["Activity"]["properties"]["start_time"]

    assert "HH:MM" in start_time["description"]


def test_metadata_that_only_helps_humans_is_gone() -> None:
    document = schema()

    # The top-level model title ("Itinerary") is redundant with the prompt around it.
    assert "title" not in document
    assert "default" not in json.dumps(document["properties"]["travelers"])


def test_it_is_meaningfully_smaller_than_the_raw_schema() -> None:
    raw = json.dumps(Itinerary.model_json_schema(), ensure_ascii=False)
    pruned = itinerary_schema_json()

    assert len(pruned) < len(raw) * 0.8, f"only saved {1 - len(pruned) / len(raw):.0%}"


def test_it_still_parses_as_json() -> None:
    assert isinstance(schema(), dict)
