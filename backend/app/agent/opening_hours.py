"""Read Google's opening-hours text and decide whether a visit falls inside it.

Google returns hours as the strings it shows users -- "Monday: 11:00 AM - 5:00 PM",
"Tuesday: Closed", "Monday: Open 24 hours", and days with a break in the middle:
"Monday: 11:00 AM - 2:00 PM, 5:00 - 9:00 PM". There is a structured form in the API too,
but the descriptions are what the planner already receives and passing them through
unparsed keeps one representation instead of two.

**Everything here refuses rather than guesses.** A day it cannot parse returns "no
opinion", never "closed" -- the check exists to catch a plan sending someone to a locked
door, and inventing closures would do the opposite of that while looking rigorous.
"""

import re

# Google renders the range with an en dash; some locales and older payloads use a
# hyphen, and the space around it is not reliable either.
_RANGE_SPLIT = re.compile(r"\s*[–—-]\s*")
_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?[Mm]\.?$")
_24H = re.compile(r"^(\d{1,2}):(\d{2})$")
_MERIDIEM = re.compile(r"[AaPp]\.?[Mm]\.?\s*$")

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _minutes(value: str) -> int | None:
    """A clock time to minutes since midnight, in either 12- or 24-hour form."""
    text = value.strip().replace(" ", " ").replace("\xa0", " ")
    if not text:
        return None

    match = _TIME.match(text)
    if match:
        hour = int(match.group(1)) % 12
        minute = int(match.group(2) or 0)
        if match.group(3).lower() == "p":
            hour += 12
        return hour * 60 + minute

    match = _24H.match(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 24 or minute > 59:
            return None
        return hour * 60 + minute
    return None


def _windows(spec: str) -> list[tuple[int, int]] | None:
    """Open intervals for one day, or None when the text cannot be read.

    An interval that ends before it starts has crossed midnight -- "5:00 PM - 2:00 AM"
    -- and is extended to the end of the day rather than dropped. Visits are scheduled
    by wall clock within a single date, so the small hours of the next morning are not
    something this check can speak to anyway.
    """
    body = spec.strip()
    if not body:
        return None
    lowered = body.lower()
    if "closed" in lowered:
        return []
    if "24 hours" in lowered or "open 24" in lowered:
        return [(0, 24 * 60)]

    windows: list[tuple[int, int]] = []
    for part in body.split(","):
        halves = _RANGE_SPLIT.split(part.strip())
        if len(halves) != 2:
            return None
        start_text, end_text = (half.strip() for half in halves)

        end = _minutes(end_text)

        # An opening time with no meridiem takes one from the closing time -- but not
        # by copying it. "5:00 - 9:00 PM" is 17:00 and "11:00 - 2:00 PM" is 11:00, so
        # copying "PM" is right once and wrong once. The reading that works for both is
        # the latest one that still falls before closing. This has to happen before
        # parsing rather than as a fallback, because "5:00" is valid 24-hour text on its
        # own and would silently become a morning window that shuts nine hours early.
        if end is not None and not _MERIDIEM.search(start_text) and _MERIDIEM.search(end_text):
            candidates = [
                candidate
                for candidate in (_minutes(f"{start_text} {half}") for half in ("AM", "PM"))
                if candidate is not None and candidate < end
            ]
            start = max(candidates) if candidates else _minutes(start_text)
        else:
            start = _minutes(start_text)
        if start is None or end is None:
            return None
        if end <= start:
            end = 24 * 60
        windows.append((start, end))
    return windows or None


def parse(descriptions: list[str]) -> dict[str, list[tuple[int, int]]]:
    """Weekday name -> open intervals, skipping any line that cannot be read."""
    hours: dict[str, list[tuple[int, int]]] = {}
    for line in descriptions:
        name, _, spec = line.partition(":")
        weekday = name.strip().lower()
        if weekday not in WEEKDAYS:
            continue
        windows = _windows(spec)
        if windows is not None:
            hours[weekday] = windows
    return hours


def closed_reason(
    descriptions: list[str], weekday: str, start_minute: int, end_minute: int
) -> str | None:
    """Why this visit does not fit the venue's hours, or None if it does.

    Returns None whenever there is no basis to object: unparseable text, a weekday the
    payload does not cover, or hours that simply contain the visit.
    """
    hours = parse(descriptions)
    windows = hours.get(weekday.lower())
    if windows is None:
        return None
    if not windows:
        return f"closed on {weekday.capitalize()}"
    if any(start_minute >= open_at and end_minute <= close_at for open_at, close_at in windows):
        return None

    readable = ", ".join(f"{_clock(open_at)}-{_clock(close_at)}" for open_at, close_at in windows)
    return f"open {readable} on {weekday.capitalize()}"


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"
