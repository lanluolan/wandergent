"""Tests for distilling what the run verified into something the model can act on.

No new data and no new API calls -- the facts were already in the conversation, as
roughly 350 characters of JSON per venue with seven separate weekday lines. Sufficient
and close to unusable. Two live failures came from a model that had the answer in its
context in a shape it could not use: a museum booked on the one day it is shut, and a
14-minute walk scheduled with a 0-minute gap.
"""

from app.agent.brief import render, summarize_hours

ART_INSTITUTE_HOURS = [
    "Monday: 11:00 AM – 5:00 PM",
    "Tuesday: Closed",
    "Wednesday: 11:00 AM – 5:00 PM",
    "Thursday: 11:00 AM – 8:00 PM",
    "Friday: 11:00 AM – 5:00 PM",
    "Saturday: 11:00 AM – 5:00 PM",
    "Sunday: 11:00 AM – 5:00 PM",
]
ART_INSTITUTE = (41.8796, -87.6224)
ALINEA = (41.9134, -87.6486)


def test_the_closed_day_is_impossible_to_miss() -> None:
    """It is the single most consequential fact about a venue and it used to be line
    two of seven inside a JSON blob."""
    summary = summarize_hours(ART_INSTITUTE_HOURS)

    assert summary is not None
    assert "Tue closed" in summary


def test_identical_days_collapse_into_a_run() -> None:
    """ "Fri-Sun 11:00-17:00" is how a week actually reads, and it leaves room for the
    day that differs to stand out."""
    summary = summarize_hours(ART_INSTITUTE_HOURS)

    assert summary is not None
    assert "Fri-Sun 11:00-17:00" in summary
    assert "Thu 11:00-20:00" in summary


def test_hours_that_cannot_be_read_produce_no_claim() -> None:
    """Same rule as the constraint check: absence of hours is never a closure."""
    assert summarize_hours([]) is None
    assert summarize_hours(["nonsense", "Someday: whenever"]) is None


def test_a_venue_with_no_hours_says_so_rather_than_going_quiet() -> None:
    """Silence would read as "open whenever"; parks and viewpoints often have no hours."""
    block = render({"Alinea": ALINEA}, {}, {})

    assert block is not None
    assert "hours not published" in block


def test_the_price_band_rides_along_when_there_is_one() -> None:
    block = render({}, {}, {"Alinea": "PRICE_LEVEL_VERY_EXPENSIVE"})

    assert block is not None
    assert "very expensive" in block


def test_the_brief_carries_the_distances_too() -> None:
    """One block, not two: the model should not have to join them itself."""
    block = render({"Art Institute": ART_INSTITUTE, "Alinea": ALINEA}, {}, {})

    assert block is not None
    assert "too far to walk" in block


def test_a_venue_known_only_by_its_hours_still_appears() -> None:
    """The three harvests are independent -- a place can arrive with hours and no
    coordinates. Listing only the ones with points would silently lose it."""
    block = render({}, {"Art Institute": ART_INSTITUTE_HOURS}, {})

    assert block is not None
    assert "Art Institute" in block


def test_nothing_looked_up_means_no_brief() -> None:
    assert render({}, {}, {}) is None


def test_the_brief_says_what_it_does_not_cover() -> None:
    """Read as a complete list it would license "I checked everything"."""
    block = render({"Alinea": ALINEA}, {}, {})

    assert block is not None
    assert "not listed here you have not checked" in block


def test_truncation_is_announced() -> None:
    many = {f"venue {n}": (41.88 + n * 0.01, -87.62) for n in range(20)}
    block = render(many, {}, {})

    assert block is not None
    assert "further venue(s) not listed" in block
