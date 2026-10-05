from datetime import UTC, date, datetime

from app.agent import brief, opening_hours
from app.agent.orchestrator import harvest_place_hours
from app.agent.schedule_repair import hours_candidates
from app.agent.schemas import Itinerary
from app.agent.validation import validate_itinerary
from app.tools.maps import _current_hours, _to_place

NOW = datetime(2026, 10, 5, 1, tzinfo=UTC)


def point(day, hour, minute=0):
    return {"date": {"year": 2026, "month": 10, "day": day}, "hour": hour, "minute": minute}


def venue(periods):
    return {
        "displayName": {"text": "Museum"},
        "utcOffsetMinutes": -420,
        "regularOpeningHours": {"weekdayDescriptions": ["Monday: 09:00-17:00"]},
        "currentOpeningHours": {"periods": periods},
    }


def test_local_dates_and_special_hours_reach_validator_and_brief():
    import json

    raw = venue([{"open": point(5, 10), "close": point(5, 13)}])
    hours = [*raw["regularOpeningHours"]["weekdayDescriptions"], *_current_hours(raw, NOW)]
    observed = {}
    harvest_place_hours(
        "search_places",
        json.dumps({"places": [{"name": "Museum", "opening_hours": hours}]}),
        observed,
    )
    assert "2026-10-04: Closed" in hours  # Place local date, not UTC date.
    assert opening_hours.windows_for(observed["Museum"], date(2026, 10, 5)) == [(600, 780)]
    assert opening_hours.closed_reason(hours, "monday", 840, 900, date(2026, 10, 5))
    assert opening_hours.windows_for(hours, date(2026, 10, 12)) == [(540, 1020)]
    assert "2026-10-05 override 10:00-13:00" in brief.summarize_hours(hours)


def test_overnight_period_retains_next_mornings_hours():
    hours = _current_hours(venue([{"open": point(4, 22), "close": point(5, 2)}]), NOW)
    assert opening_hours.windows_for(hours, date(2026, 10, 4)) == [(1320, 1440)]
    assert opening_hours.windows_for(hours, date(2026, 10, 5)) == [(0, 120)]


def test_empty_periods_mean_closed_but_absent_or_malformed_data_is_unknown():
    assert len(_current_hours(venue([]), NOW)) == 7
    assert all(line.endswith("Closed") for line in _current_hours(venue([]), NOW))
    assert _current_hours({"currentOpeningHours": {}}, NOW) == []
    assert _current_hours(venue([{"open": point(5, 10)}]), NOW) == []
    assert _current_hours(venue([{"open": {}, "close": {}}]), NOW) == []
    raw = venue([])
    del raw["utcOffsetMinutes"]
    assert _current_hours(raw, NOW) == []


def test_always_open_sentinel_and_normalization():
    raw = venue([{"open": {"day": 0, "hour": 0, "minute": 0}}])
    assert all(line.endswith("00:00-24:00") for line in _current_hours(raw, NOW))
    normalized = _to_place(raw)
    assert normalized.opening_hours[0] == "Monday: 09:00-17:00"
    assert len(normalized.opening_hours) == 8


def test_validator_and_repair_use_date_override_instead_of_regular_hours():
    plan = Itinerary.model_validate(
        {
            "destination": "Chicago",
            "start_date": "2026-10-05",
            "end_date": "2026-10-05",
            "budget": 500,
            "days": [
                {
                    "date": "2026-10-05",
                    "summary": "Museum visit",
                    "activities": [
                        {
                            "title": "Museum",
                            "location": "Museum",
                            "start_time": "14:00",
                            "end_time": "15:00",
                            "estimated_cost": 20,
                        }
                    ],
                }
            ],
        }
    )
    hours = {"Museum": ["Monday: 09:00-17:00", "2026-10-05: 10:00-13:00"]}
    assert "outside_opening_hours" in {v.code for v in validate_itinerary(plan, hours).blocking}
    candidates = list(hours_candidates(plan, hours, {}, []))
    assert candidates
    assert candidates[0].days[0].activities[0].start_time == "12:00"
    assert validate_itinerary(candidates[0], hours).ok
