"""The fast path has to survive the model being chatty.

The tool loop's closing turn is usually the itinerary already, and parsing it saves a
whole second generation. That saving is worth real money per run, and it was being lost
to a single line of preamble -- observed live: "I have everything I need to build the
itinerary." followed by a fenced block. These pin every wrapper shape seen in practice.
"""

import pytest

from app.agent.llm import parse_itinerary, strip_fences

BODY = (
    '{"destination":"Chicago","start_date":"2026-09-07","end_date":"2026-09-07",'
    '"days":[],"notes":[]}'
)


@pytest.mark.parametrize(
    ("shape", "raw"),
    [
        ("plain", BODY),
        ("fenced with language", f"```json\n{BODY}\n```"),
        ("fenced without language", f"```\n{BODY}\n```"),
        # The one that cost a second generation on a real run.
        ("preamble then fence", f"I have everything I need.\n\n```json\n{BODY}\n```"),
        ("preamble, no fence", f"Here you go: {BODY}"),
        ("trailing chatter", f"{BODY}\n\nLet me know if you want changes."),
        ("wrapped both ends", f"Sure!\n```json\n{BODY}\n```\nAnything else?"),
    ],
)
def test_every_wrapper_the_model_uses_still_parses(shape: str, raw: str) -> None:
    itinerary, errors = parse_itinerary(raw)
    assert itinerary is not None, f"{shape}: {errors}"
    assert itinerary.destination == "Chicago"


def test_a_reply_with_no_json_reports_what_the_model_said() -> None:
    """Extraction must not invent success, and the error has to stay diagnosable."""
    itinerary, errors = parse_itinerary("I could not build an itinerary for that.")

    assert itinerary is None
    assert errors


def test_an_unbalanced_object_is_left_alone() -> None:
    """A truncated reply has "{" and no "}". Returning it unchanged keeps the parser
    error about the real content, which is what the truncation check then reads."""
    cut_off = 'Here is the plan: {"destination":"Chicago","days":[{"date"'

    assert strip_fences(cut_off) == cut_off
