"""Read Google's opening-hours text and decide whether a visit falls inside it.

Google returns hours as the strings it shows users -- "Monday: 11:00 AM - 5:00 PM",
"Tuesday: Closed", "Monday: Open 24 hours", and days with a break: "Monday: 11:00 AM -
2:00 PM, 5:00 - 9:00 PM". The API has a structured form too, but the descriptions are what
the planner already receives, and using them keeps one representation instead of two.

**Everything here refuses rather than guesses.** A day it cannot parse means "no opinion",
never "closed": inventing closures would defeat the check while looking rigorous.
"""

import re
from datetime import date, timedelta

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
        if not 1 <= int(match.group(1)) <= 12 or int(match.group(2) or 0) > 59:
            return None
        hour = int(match.group(1)) % 12
        minute = int(match.group(2) or 0)
        if match.group(3).lower() == "p":
            hour += 12
        return hour * 60 + minute

    match = _24H.match(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 24 or minute > 59 or (hour == 24 and minute != 0):
            return None
        return hour * 60 + minute
    return None


def _windows(spec: str) -> list[tuple[int, int]] | None:
    """Open intervals for one day, or None when the text cannot be read.

    An interval ending before it starts has crossed midnight ("5:00 PM - 2:00 AM") and is
    extended into the next day, preserving the next morning's small hours.
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

        # An opening time with no meridiem borrows one from the closing time, but not by
        # copying: "5:00 - 9:00 PM" is 17:00 and "11:00 - 2:00 PM" is 11:00, so copying
        # "PM" is right once and wrong once. The reading that works for both is the latest
        # one still before closing. It must happen before parsing, since "5:00" is valid
        # 24-hour text and would otherwise become a window that shuts nine hours early.
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
        if end == start:
            return None
        if end < start:
            end += 24 * 60
        windows.append((start, end))
    return windows or None


def parse(descriptions: list[str]) -> dict[str, list[tuple[int, int]]]:
    """Weekday or ISO date -> open intervals, skipping unreadable lines."""
    hours: dict[str, list[tuple[int, int]]] = {}
    for line in descriptions:
        name, _, spec = line.partition(":")
        weekday = name.strip().lower()
        if weekday not in WEEKDAYS:
            try:
                date.fromisoformat(weekday)
            except ValueError:
                continue
        windows = _windows(spec)
        if windows is not None:
            hours[weekday] = windows
    return hours


def windows_for(descriptions: list[str], visit_date: date) -> list[tuple[int, int]] | None:
    """Current date overrides take precedence only on the date Google observed."""
    hours = parse(descriptions)
    if visit_date.isoformat() in hours:
        return _daily_windows(hours[visit_date.isoformat()], None)
    previous = visit_date - timedelta(days=1)
    return _daily_windows(
        hours.get(visit_date.strftime("%A").lower()),
        hours.get(previous.isoformat(), hours.get(previous.strftime("%A").lower())),
    )


def _daily_windows(current, previous) -> list[tuple[int, int]] | None:
    if current is None:
        return None
    windows = [(start, min(end, 1440)) for start, end in current]
    windows.extend((0, end - 1440) for _, end in previous or [] if end > 1440)
    return _merge_windows(windows)


def _merge_windows(windows) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def closed_reason(
    descriptions: list[str],
    weekday: str,
    start_minute: int,
    end_minute: int,
    visit_date: date | None = None,
) -> str | None:
    """Why this visit does not fit the venue's hours, or None if it does.

    Returns None whenever there is no basis to object: unparseable text, a weekday the
    payload does not cover, or hours that simply contain the visit.
    """
    hours = parse(descriptions)
    if visit_date:
        windows = windows_for(descriptions, visit_date)
    else:
        name = weekday.lower()
        previous = WEEKDAYS[(WEEKDAYS.index(name) - 1) % 7] if name in WEEKDAYS else ""
        windows = _daily_windows(hours.get(name), hours.get(previous))
    if windows is None:
        return None
    if end_minute < start_minute:
        end_minute += 1440
        if visit_date:
            following = windows_for(descriptions, visit_date + timedelta(days=1))
        else:
            name = weekday.lower()
            following_name = WEEKDAYS[(WEEKDAYS.index(name) + 1) % 7]
            following = _daily_windows(hours.get(following_name), hours.get(name))
        if following is None:
            return None
        windows = _merge_windows(windows + [(a + 1440, b + 1440) for a, b in following])
    if not windows:
        return f"closed on {weekday.capitalize()}"
    if any(start_minute >= open_at and end_minute <= close_at for open_at, close_at in windows):
        return None

    readable = ", ".join(f"{_clock(open_at)}-{_clock(close_at)}" for open_at, close_at in windows)
    return f"open {readable} on {weekday.capitalize()}"


def _clock(minute: int) -> str:
    if minute > 1440:
        return f"{(minute // 60) % 24:02d}:{minute % 60:02d} (+1 day)"
    return f"{minute // 60:02d}:{minute % 60:02d}"
