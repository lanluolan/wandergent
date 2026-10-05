from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.agent import orchestrator, transfers
from app.agent.constraints import TripConstraints, resolve_constraints
from app.agent.orchestrator import plan_trip
from app.agent.schemas import Activity, DayPlan, Itinerary
from app.agent.timing import (
    Arrival,
    HotelStay,
    Journey,
    TimingContext,
    check_timing,
    missing_nights,
)
from app.agent.validation import (
    ValidationReport,
    Violation,
    transfer_candidates,
    validate_itinerary,
)
from app.config import settings
from app.tools.registry import TOOL_FUNCTIONS
from app.tools.weather import DailyForecast, WeatherForecast
from tests.fakes import FakeLLM, completion


@pytest.fixture
def weather(monkeypatch):
    async def forecast(city, start_date, end_date, **kwargs):
        first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
        days = [
            DailyForecast(date=first + timedelta(days=i), condition="clear")
            for i in range((last - first).days + 1)
        ]
        return WeatherForecast(ok=True, city=city, days=days)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", forecast)


def activity(start, end, **kwargs):
    return Activity(
        title="Visit",
        location=kwargs.pop("location", "Tokyo"),
        start_time=start[11:16],
        end_time=end[11:16],
        start_at=start,
        end_at=end,
        **kwargs,
    )


def plan(*activities):
    dates = sorted({a.start_at.date() for a in activities})
    return Itinerary(
        destination="Tokyo",
        start_date=dates[0],
        end_date=dates[-1],
        budget=500,
        days=[
            DayPlan(
                date=day,
                summary="Travel",
                activities=[a for a in activities if a.start_at.date() == day],
            )
            for day in dates
        ],
    )


def codes(value, facts):
    return {issue["code"] for issue in check_timing(value, facts)}


def hotel():
    return HotelStay(
        id="h1",
        hotel="Tokyo",
        check_in="2026-11-02T15:00+09:00",
        check_out="2026-11-03T11:00+09:00",
    )


def flight(**kwargs):
    return Journey(
        id="j1",
        origin="Tokyo",
        destination="Los Angeles",
        mode="FLIGHT",
        departure="2026-11-02T01:00+09:00",
        arrival="2026-11-01T18:00-08:00",
        **kwargs,
    )


def journey_activity(leg):
    return activity(
        leg.departure.isoformat(),
        leg.arrival.isoformat(),
        category="transport",
        travel_mode=leg.mode,
        journey_id=leg.id,
    )


def test_date_line_and_overnight_times_are_ordered_in_utc():
    leg = flight()
    value = journey_activity(leg)
    assert value.end_at.date() < value.start_at.date()
    assert value.end_at.astimezone(UTC) > value.start_at.astimezone(UTC)
    assert codes(plan(value), TripConstraints(journeys=[leg])) == {"journey_time_estimated"}


@pytest.mark.parametrize("field", ["departure", "arrival"])
def test_journey_endpoints_require_offsets(field):
    payload = flight().model_dump(mode="json")
    payload[field] = "2026-11-02T12:00"
    with pytest.raises(ValidationError):
        Journey.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"arrival": "2026-11-01T06:00-08:00"},
        {"departure": "2026-11-03T01:00+09:00"},
    ],
)
def test_reversed_utc_journeys_are_rejected(changes):
    with pytest.raises(ValidationError):
        Journey.model_validate({**flight().model_dump(), **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"end_at": None},
        {"end_time": "19:00"},
        {"start_at": "2026-11-02T01:00"},
    ],
)
def test_activity_absolute_times_must_be_paired_aware_and_match_labels(changes):
    value = journey_activity(flight())
    with pytest.raises(ValidationError):
        Activity.model_validate({**value.model_dump(), **changes})


def test_arrival_buffer_cannot_be_ignored_or_replaced_with_local_clock_comparison():
    facts = TripConstraints(
        arrival=Arrival(at="2026-11-02T10:00+09:00", location="Tokyo", buffer_minutes=90)
    )
    early = activity("2026-11-02T11:00+09:00", "2026-11-02T11:30+09:00")
    ready = activity("2026-11-02T11:30+09:00", "2026-11-02T12:30+09:00")
    assert "arrival_conflict" in codes(plan(early), facts)
    assert not codes(plan(ready), facts)


def test_missing_absolute_times_block_timed_plans():
    value = plan(activity("2026-11-02T11:00+09:00", "2026-11-02T12:00+09:00"))
    value.days[0].activities[0] = Activity(title="Legacy", start_time="11:00", end_time="12:00")
    facts = TripConstraints(arrival=Arrival(at="2026-11-02T10:00+09:00", location="Tokyo"))
    assert "temporal_unverified" in codes(value, facts)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-11-02T14:00+09:00", "2026-11-02T14:30+09:00"),
        ("2026-11-02T15:00+09:00", "2026-11-02T15:10+09:00"),
    ],
)
def test_early_or_too_short_check_in_is_rejected(start, end):
    value = activity(
        start, end, category="accommodation", hotel_stay_id="h1", lodging_action="check_in"
    )
    assert "hotel_window_conflict" in codes(plan(value), TripConstraints(hotel_stays=[hotel()]))


def test_confirmed_early_check_in_and_checkout_window_are_honoured():
    stay = hotel().model_copy(update={"check_in": datetime.fromisoformat("2026-11-02T13:00+09:00")})
    check_in = activity(
        "2026-11-02T13:00+09:00",
        "2026-11-02T13:30+09:00",
        category="accommodation",
        hotel_stay_id="h1",
        lodging_action="check_in",
    )
    check_out = activity(
        "2026-11-03T10:45+09:00",
        "2026-11-03T11:00+09:00",
        category="accommodation",
        hotel_stay_id="h1",
        lodging_action="check_out",
    )
    value = plan(check_in, check_out)
    facts = TripConstraints(hotel_stays=[stay])
    assert validate_itinerary(value, constraints=facts).ok
    late = check_out.model_copy(
        update={"end_at": datetime.fromisoformat("2026-11-03T11:15+09:00"), "end_time": "11:15"}
    )
    assert "hotel_window_conflict" in codes(plan(check_in, late), facts)


def test_hotel_nights_are_checked_individually():
    value = plan(
        activity("2026-11-02T16:00+09:00", "2026-11-02T17:00+09:00"),
        activity("2026-11-04T12:00+09:00", "2026-11-04T13:00+09:00"),
    )
    facts = TripConstraints(hotel_stays=[hotel()], lodging_arranged=True)
    assert [str(day) for day in missing_nights(value, facts)] == ["2026-11-03"]
    assert "missing_accommodation" in {
        v.code for v in validate_itinerary(value, constraints=facts).blocking
    }


def test_cross_date_activities_cannot_overlap_in_utc():
    first = activity("2026-11-02T01:00+09:00", "2026-11-02T03:00+09:00")
    second = activity("2026-11-01T09:00-08:00", "2026-11-01T10:00-08:00")
    assert "time_conflict" in codes(plan(first, second), TripConstraints())


def test_a_dropped_or_moved_booking_cannot_pass():
    leg = flight(timing_confidence="confirmed")
    value = journey_activity(leg)
    moved = value.model_copy(
        update={"start_at": value.start_at.replace(hour=2), "start_time": "02:00"}
    )
    facts = TripConstraints(journeys=[leg])
    assert "journey_conflict" in codes(plan(moved), facts)
    other = activity("2026-11-02T11:00+09:00", "2026-11-02T12:00+09:00")
    assert "journey_conflict" in codes(plan(other), facts)


def test_journey_buffers_block_conflicting_local_activities():
    leg = flight(departure_buffer_minutes=120, arrival_buffer_minutes=60)
    ready = activity("2026-11-01T18:30-08:00", "2026-11-01T19:00-08:00")
    assert "journey_conflict" in codes(
        plan(journey_activity(leg), ready), TripConstraints(journeys=[leg])
    )


def test_insufficient_connection_is_detected_between_out_of_frame_journeys():
    first = flight(arrival_buffer_minutes=60)
    second = Journey(
        id="j2",
        origin="Los Angeles",
        destination="New York",
        mode="FLIGHT",
        departure="2026-11-01T18:30-08:00",
        arrival="2026-11-02T03:00-05:00",
    )
    value = plan(activity("2026-11-03T12:00-05:00", "2026-11-03T13:00-05:00"))
    assert "journey_conflict" in codes(value, TripConstraints(journeys=[first, second]))


def test_request_owned_timing_survives_revisions_and_explicit_clearing():
    previous = TripConstraints(
        arrival=Arrival(at="2026-11-02T10:00+09:00", location="Tokyo"),
        hotel_stays=[hotel()],
        journeys=[flight()],
    )
    assert resolve_constraints("Change lunch", previous=previous) == previous
    cleared = resolve_constraints(
        "Change lunch",
        previous=previous,
        confirmed=TripConstraints(arrival=None, hotel_stays=[], journeys=[]),
    )
    assert cleared.arrival is None and cleared.hotel_stays == [] and cleared.journeys == []
    assert resolve_constraints("new trip", previous=previous).arrival is None


def test_explicit_text_templates_parse_without_stealing_trip_dates():
    request = (
        "2026-11-01 to 2026-11-03; Arrival: Tokyo, 2026-11-02T10:00+09:00; "
        "Hotel h1: Tokyo, check-in 2026-11-02T15:00+09:00, check-out 2026-11-03T11:00+09:00; "
        "Journey j1: FLIGHT, Tokyo -> Los Angeles, 2026-11-02T01:00+09:00 -> 2026-11-01T18:00-08:00"
    )
    facts = resolve_constraints(request)
    assert (
        facts.start_date.isoformat() == "2026-11-01" and facts.end_date.isoformat() == "2026-11-03"
    )
    assert (
        facts.arrival.location == "Tokyo"
        and facts.hotel_stays[0].id == "h1"
        and facts.journeys[0].id == "j1"
    )


def test_cross_zone_local_transfer_gap_uses_utc_elapsed_time():
    first = activity("2026-11-02T10:00+01:00", "2026-11-02T11:00+01:00")
    second = activity("2026-11-02T12:00+02:00", "2026-11-02T13:00+02:00").model_copy(
        update={"location": "Next city"}
    )
    value = plan(first, second)
    report = transfer_candidates(value, validate_itinerary(value))
    candidate = next(v for v in report.violations if v.code == "insufficient_transfer")
    assert candidate.gap_minutes == 0
    assert candidate.departure_instant is not None and candidate.arrival_deadline is not None


def test_legacy_same_day_plans_remain_valid():
    item = Activity(title="Visit", start_time="10:00", end_time="11:00", location="Museum")
    value = Itinerary(
        destination="Tokyo",
        start_date="2026-11-02",
        end_date="2026-11-02",
        days=[DayPlan(date="2026-11-02", summary="Visit", activities=[item])],
    )
    assert validate_itinerary(value).ok


def test_overnight_train_covers_its_night_without_a_hotel():
    leg = Journey(
        id="overnight",
        origin="Tokyo",
        destination="Osaka",
        mode="TRAIN",
        departure="2026-11-02T22:00+09:00",
        arrival="2026-11-03T07:00+09:00",
    )
    value = plan(journey_activity(leg))
    value.end_date = date(2026, 11, 4)
    facts = TripConstraints(hotel_stays=[], journeys=[leg])
    assert missing_nights(value, facts) == [date(2026, 11, 3)]


def test_cross_zone_overnight_journey_covers_the_local_night():
    leg = Journey(
        id="eastbound",
        origin="Tokyo",
        destination="Sydney",
        mode="FLIGHT",
        departure="2026-11-02T23:00+09:00",
        arrival="2026-11-03T10:00+11:00",
    )
    value = plan(journey_activity(leg))
    value.end_date = date(2026, 11, 3)
    assert missing_nights(value, TripConstraints(hotel_stays=[], journeys=[leg])) == []


def test_saved_snapshot_enforces_arrival_during_standalone_validation():
    value = plan(activity("2026-11-02T10:00+09:00", "2026-11-02T11:00+09:00"))
    value.timing = TimingContext(arrival=Arrival(at="2026-11-02T10:00+09:00", location="Tokyo"))
    assert "arrival_conflict" in {v.code for v in validate_itinerary(value).blocking}


def test_model_copy_cannot_bypass_absolute_clock_consistency():
    item = activity("2026-11-02T10:00+09:00", "2026-11-02T11:00+09:00")
    value = plan(item)
    value.days[0].activities[0] = item.model_copy(update={"start_time": "09:00"})
    assert "constraint_mismatch" in codes(value, TripConstraints())


def test_duplicate_and_unknown_journey_references_are_blocking():
    leg = flight()
    item = journey_activity(leg)
    assert "journey_conflict" in codes(plan(item, item), TripConstraints(journeys=[leg]))
    unknown = item.model_copy(update={"journey_id": "unknown"})
    assert "constraint_mismatch" in codes(plan(unknown), TripConstraints(journeys=[leg]))


def test_multiday_visit_checks_intermediate_opening_days():
    item = activity("2026-11-02T10:00+09:00", "2026-11-04T11:00+09:00")
    value = plan(item.model_copy(update={"location": "Tokyo Museum"}))
    report = validate_itinerary(
        value,
        known_hours={
            "Tokyo Museum": ["Monday: Open 24 hours", "Tuesday: Closed", "Wednesday: Open 24 hours"]
        },
    )
    assert "outside_opening_hours" in {v.code for v in report.blocking}


def test_room_stay_uses_booking_window_instead_of_public_venue_hours():
    stay = hotel().model_copy(update={"hotel": "Tokyo Hotel"})
    value = plan(
        activity(
            "2026-11-02T15:00+09:00",
            "2026-11-03T10:00+09:00",
            location="Tokyo Hotel",
            category="accommodation",
            hotel_stay_id=stay.id,
            lodging_action="stay",
        )
    )
    report = validate_itinerary(
        value,
        constraints=TripConstraints(hotel_stays=[stay]),
        known_hours={"Tokyo Hotel": ["Monday: Closed"]},
    )
    assert "outside_opening_hours" not in {v.code for v in report.blocking}


def test_repair_reports_measured_arrival_in_destination_timezone():
    value = plan(activity("2026-11-02T10:00+01:00", "2026-11-02T11:00+01:00"))
    report = ValidationReport(
        violations=[
            Violation(
                code="insufficient_transfer",
                message="Measured gap",
                day=date(2026, 11, 2),
                origin="Origin city",
                destination="Destination city",
                depart_at_minute=660,
                needed_minutes=40,
                departure_instant="2026-11-02T11:00+01:00",
                arrival_deadline="2026-11-02T12:00+02:00",
            )
        ]
    )
    context = orchestrator._constraint_repair_context(
        {
            "itinerary": value,
            "constraints": TripConstraints(),
            "report": report,
        }
    )
    assert '"departure_local": "2026-11-02T11:00+01:00"' in context
    assert '"earliest_arrival_local": "2026-11-02T12:40+02:00"' in context


async def test_declared_endpoint_offset_mismatch_cannot_clear_transfer(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    async def offset(place, day, **kwargs):
        return timedelta(hours=1)

    async def unexpected_measure(*args, **kwargs):
        raise AssertionError("Do not measure with an unverified destination offset")

    monkeypatch.setattr(transfers, "local_utc_offset", offset)
    monkeypatch.setattr(transfers, "_measure", unexpected_measure)
    report = ValidationReport(
        violations=[
            Violation(
                code="insufficient_transfer",
                message="Measured gap",
                day=date(2026, 11, 2),
                origin="Origin city",
                destination="Destination city",
                gap_minutes=0,
                depart_at_minute=660,
                departure_instant="2026-11-02T11:00+01:00",
                arrival_deadline="2026-11-02T12:00+02:00",
            )
        ]
    )
    confirmed = await transfers.confirm_transfers(report)
    assert confirmed == report


async def test_repair_preserves_arrival_and_embeds_server_owned_context(weather):
    early = plan(activity("2026-11-02T10:00+09:00", "2026-11-02T11:00+09:00"))
    corrected = plan(activity("2026-11-02T11:00+09:00", "2026-11-02T12:00+09:00"))
    facts = TripConstraints(arrival=Arrival(at="2026-11-02T10:00+09:00", location="Tokyo"))
    llm = FakeLLM(
        [
            completion(content=early.model_dump_json()),
            completion(content=corrected.model_dump_json()),
        ]
    )
    result = await plan_trip(
        "Plan a day", constraints=facts, client=llm, model="test-model", today=date(2026, 11, 1)
    )
    assert result.validation.ok and result.constraints.arrival == facts.arrival
    assert result.itinerary.timing.arrival == facts.arrival
    assert len(llm.requests) == 2
    assert "arrival_conflict" not in {v.code for v in result.validation.blocking}


async def test_saved_timing_is_restored_when_previous_constraints_are_absent(weather):
    item = activity("2026-11-02T11:00+09:00", "2026-11-02T12:00+09:00")
    value = plan(item)
    value.timing = TimingContext(arrival=Arrival(at="2026-11-02T10:00+09:00", location="Tokyo"))
    llm = FakeLLM([completion(content=value.model_dump_json()) for _ in range(2)])
    result = await plan_trip(
        "Change the title", previous=value, client=llm, model="test-model", today=date(2026, 11, 1)
    )
    assert result.validation.ok
    assert result.constraints.arrival == value.timing.arrival
