"""Reading Google's opening-hours text.

The bias here is the whole design: **anything unreadable is "no opinion", never
"closed"**. This check exists to stop a plan sending someone to a locked door, and a
parser that invented closures would do the opposite while looking rigorous.
"""

import pytest

from app.agent.opening_hours import closed_reason, parse

REAL = [
    # Verbatim from the live API for the Art Institute of Chicago.
    "Monday: 11:00 AM – 5:00 PM",
    "Tuesday: Closed",
    "Wednesday: 11:00 AM – 5:00 PM",
    "Thursday: 11:00 AM – 8:00 PM",
    "Friday: 11:00 AM – 5:00 PM",
    "Saturday: 11:00 AM – 5:00 PM",
    "Sunday: 11:00 AM – 5:00 PM",
]


def test_reads_the_shape_the_api_actually_returns() -> None:
    hours = parse(REAL)

    assert hours["monday"] == [(11 * 60, 17 * 60)]
    assert hours["tuesday"] == []  # closed
    assert hours["thursday"] == [(11 * 60, 20 * 60)]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Monday: Open 24 hours", [(0, 1440)]),
        ("Monday: 9:00 AM – 5:00 PM", [(540, 1020)]),
        ("Monday: 09:00 – 17:00", [(540, 1020)]),  # 24-hour locales
        ("Monday: 11:00 AM - 5:00 PM", [(660, 1020)]),  # plain hyphen
        # A lunch break, which many restaurants and small museums have.
        ("Monday: 11:00 AM – 2:00 PM, 5:00 – 9:00 PM", [(660, 840), (1020, 1260)]),
        # Meridiem only on the closing half; the opening half inherits it.
        ("Monday: 11:00 – 2:00 PM", [(660, 840)]),
    ],
)
def test_formats_seen_in_the_wild(line: str, expected: list[tuple[int, int]]) -> None:
    assert parse([line])["monday"] == expected


def test_a_range_over_midnight_runs_to_the_end_of_the_day() -> None:
    """A bar open until 2am must not read as "closes before it opens" and vanish."""
    assert parse(["Monday: 5:00 PM – 2:00 AM"])["monday"] == [(17 * 60, 24 * 60)]


@pytest.mark.parametrize(
    "line",
    [
        "Monday: hours vary",
        "Monday: by appointment",
        "Monday:",
        "Gibberish",
        "Monday: 11:00 AM –",
    ],
)
def test_unreadable_days_are_skipped_not_guessed(line: str) -> None:
    assert "monday" not in parse([line])


def test_a_visit_inside_the_hours_passes() -> None:
    assert closed_reason(REAL, "monday", 14 * 60 + 10, 17 * 60) is None


def test_a_visit_on_a_closed_day_is_caught() -> None:
    reason = closed_reason(REAL, "tuesday", 14 * 60, 16 * 60)

    assert reason == "closed on Tuesday"


def test_a_visit_before_opening_is_caught() -> None:
    """The exact failure the field mask was added for: 09:00 at a place that opens 11."""
    reason = closed_reason(REAL, "monday", 9 * 60, 12 * 60)

    assert reason is not None and "11:00-17:00" in reason


def test_a_visit_running_past_closing_is_caught() -> None:
    assert closed_reason(REAL, "monday", 16 * 60, 18 * 60) is not None


def test_no_hours_means_no_opinion() -> None:
    """Parks and viewpoints often publish none. Silence is not a closure."""
    assert closed_reason([], "monday", 9 * 60, 12 * 60) is None


def test_a_weekday_missing_from_the_payload_is_no_opinion() -> None:
    assert closed_reason(["Monday: 11:00 AM – 5:00 PM"], "sunday", 9 * 60, 12 * 60) is None


def test_unreadable_hours_never_produce_a_closure() -> None:
    assert closed_reason(["Monday: hours vary"], "monday", 3 * 60, 4 * 60) is None
