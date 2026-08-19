"""A reply cut off at the output limit must be reported as length, not syntax.

Observed live: a plan whose activities each carried highlights plus a street address
came back as `..."cost":6,"highlights["` and, on the retry, stopped mid-array at column
4678. Both were reported to the model as JSON syntax errors, so the repair round was
spent adding punctuation to a document whose real problem was that it did not fit.
"""

from datetime import date

from app.agent.llm import TRUNCATION_ERROR, Turn, parse_turn
from app.agent.orchestrator import plan_trip
from app.config import settings
from tests.fakes import ITINERARY_JSON, FakeLLM, completion

TODAY = date(2026, 8, 5)

# Where the live failure stopped: inside an activity, right after a key.
CUT_OFF = ITINERARY_JSON[:400]


def test_a_cut_off_turn_is_blamed_on_length() -> None:
    turn = Turn(content=CUT_OFF, finish_reason="length")

    itinerary, errors = parse_turn(turn)

    assert itinerary is None
    assert errors == TRUNCATION_ERROR
    assert "shorter" in errors


def test_a_finished_turn_that_is_malformed_still_reports_the_syntax_error() -> None:
    """Truncation is not the excuse for every parse failure -- only for cut-off ones."""
    turn = Turn(content='{"destination": "Chicago",}', finish_reason="stop")

    itinerary, errors = parse_turn(turn)

    assert itinerary is None
    assert errors != TRUNCATION_ERROR
    assert not turn.truncated


def test_an_endpoint_that_omits_finish_reason_still_works() -> None:
    """Not every OpenAI-compatible endpoint sends one; absence must not mean truncated."""
    turn = Turn(content=CUT_OFF)

    _, errors = parse_turn(turn)

    assert errors != TRUNCATION_ERROR


async def test_the_repair_round_is_told_to_shorten_not_to_fix_punctuation() -> None:
    llm = FakeLLM(
        [
            completion(content=CUT_OFF, finish_reason="length"),
            completion(content=ITINERARY_JSON),
        ]
    )

    result = await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert result.itinerary is not None
    repair = llm.requests[1]["messages"][-1]["content"]
    assert TRUNCATION_ERROR in repair
    # The specific thing that went wrong live: the model was handed a column number.
    assert "column" not in repair


async def test_every_turn_asks_for_room_to_finish() -> None:
    """The detection above is the backstop; the cap is the fix. Both must be in place."""
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    await plan_trip("2 days in Chicago", client=llm, model="test-model", today=TODAY)

    assert llm.requests[0]["max_tokens"] == settings.llm_max_output_tokens
    assert settings.llm_max_output_tokens >= 8192
