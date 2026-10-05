from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.agent import opening_hours, proximity, transfers
from app.agent.brief import summarize_hours
from app.agent.validation import ValidationReport, Violation
from app.config import settings
from app.tools import maps


@pytest.mark.parametrize(
    ("day", "minute", "transition", "before", "after", "expected"),
    [
        (date(2026, 3, 8), 90, datetime(2026, 3, 8, 7, tzinfo=UTC), -5, -4, -5),
        (date(2026, 3, 8), 150, datetime(2026, 3, 8, 7, tzinfo=UTC), -5, -4, None),
        (date(2026, 3, 8), 210, datetime(2026, 3, 8, 7, tzinfo=UTC), -5, -4, -4),
        (date(2026, 11, 1), 90, datetime(2026, 11, 1, 6, tzinfo=UTC), -4, -5, None),
        (date(2026, 11, 1), 150, datetime(2026, 11, 1, 6, tzinfo=UTC), -4, -5, -5),
        (date(2026, 10, 4), 135, datetime(2026, 10, 3, 15, 30, tzinfo=UTC), 10.5, 11, None),
        (date(2026, 10, 4), 165, datetime(2026, 10, 3, 15, 30, tzinfo=UTC), 10.5, 11, 11),
    ],
)
async def test_dst_departure_is_unique_or_left_unverified(
    monkeypatch, day, minute, transition, before, after, expected
):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    async def geocode(*args):
        return maps.GeocodedPlace(ok=True, query="New York", latitude=40.7, longitude=-74)

    monkeypatch.setattr(maps, "_geocode_one", geocode)

    def respond(request):
        when = datetime.fromtimestamp(int(request.url.params["timestamp"]), UTC)
        offset = before if when < transition else after
        return httpx.Response(
            200, json={"status": "OK", "rawOffset": offset * 3600, "dstOffset": 0}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        actual = await maps.local_utc_offset("New York", day, minute=minute, client=client)
    assert actual == (timedelta(hours=expected) if expected is not None else None)


async def test_timezone_failure_does_not_guess_an_offset(monkeypatch):
    async def geocode(*args):
        return maps.GeocodedPlace(ok=True, query="venue", latitude=0, longitude=0)

    monkeypatch.setattr(maps, "_geocode_one", geocode)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    ) as client:
        assert await maps._timezone_for(client, "venue", date(2026, 3, 8), 150) is None


async def test_multi_city_transfers_use_each_origins_local_clock(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
    looked_up = []
    measured = []
    day = date(2099, 6, 1)

    async def offset(origin, on, *, minute):
        looked_up.append((origin, on, minute))
        return timedelta(hours=-4 if origin == "New York" else -7)

    async def route(origin, destination, mode, *, depart_at):
        measured.append((origin, depart_at))
        return maps.TravelTime(
            ok=True, origin=origin, destination=destination, mode=mode, seconds=60
        )

    monkeypatch.setattr(transfers, "local_utc_offset", offset)
    monkeypatch.setattr(transfers, "get_travel_time", route)
    findings = [
        Violation(
            code="transfer_unverified",
            day=day,
            origin=origin,
            destination="next stop",
            gap_minutes=15,
            depart_at_minute=10 * 60,
            message="check route",
        )
        for origin in ("New York", "Los Angeles", "New York")
    ]
    assert (await transfers.confirm_transfers(ValidationReport(violations=findings))).ok
    assert len(looked_up) == 2
    assert [(origin, moment.hour) for origin, moment in measured] == [
        ("New York", 14),
        ("Los Angeles", 17),
        ("New York", 14),
    ]


async def test_missing_departure_clock_never_clears_a_finding(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    async def unexpected(*args, **kwargs):
        raise AssertionError("do not substitute a reference departure")

    monkeypatch.setattr(transfers, "local_utc_offset", unexpected)
    monkeypatch.setattr(transfers, "get_travel_time", unexpected)
    report = ValidationReport(
        violations=[
            Violation(
                code="transfer_unverified",
                day=date(2099, 6, 1),
                origin="A",
                destination="B",
                gap_minutes=30,
                message="unknown departure",
            )
        ]
    )
    assert (await transfers.confirm_transfers(report)).violations == report.violations


@pytest.mark.parametrize("mode", ["DRIVE", "TRANSIT"])
async def test_stale_departure_does_not_query_a_different_date(monkeypatch, mode):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    def unexpected(request):
        raise AssertionError("do not request a proxy date")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        result = await maps.get_travel_time(
            "A", "B", mode, depart_at=datetime(2020, 1, 1, tzinfo=UTC), client=client
        )
    assert not result.ok and result.code == "no_coverage"


async def test_unavailable_future_transit_is_not_a_current_timetable(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
    result = await maps.get_travel_time(
        "A", "B", "TRANSIT", depart_at=datetime.now(UTC) + timedelta(days=101)
    )
    assert not result.ok and result.code == "no_coverage"


def test_overnight_hours_carry_into_closed_weekday_and_week_boundary():
    hours = ["Sunday: 22:00-02:00", "Monday: Closed"]
    assert opening_hours.windows_for(hours, date(2026, 10, 5)) == [(0, 120)]
    assert opening_hours.closed_reason(hours, "monday", 30, 90, date(2026, 10, 5)) is None
    assert opening_hours.closed_reason(hours, "monday", 90, 150, date(2026, 10, 5))
    assert opening_hours.closed_reason(hours, "sunday", 23 * 60, 60) is None
    assert "02:00 (+1 day)" in summarize_hours(hours)


def test_special_date_overrides_regular_overnight_carry():
    hours = ["Sunday: 22:00-02:00", "Monday: Closed", "2026-10-05: Closed"]
    assert opening_hours.windows_for(hours, date(2026, 10, 5)) == []
    assert opening_hours.closed_reason(hours, "monday", 30, 90, date(2026, 10, 5))


def test_missing_weekday_is_not_invented_by_the_hours_summary():
    summary = summarize_hours(["Monday: 09:00-17:00", "Wednesday: 09:00-17:00"])
    assert summary == "Mon 09:00-17:00; Wed 09:00-17:00"


@pytest.mark.parametrize("text", ["13:00 PM-14:00 PM", "24:30-25:00", "09:99-17:00", "09:00-09:00"])
def test_invalid_hours_remain_unknown(text):
    assert opening_hours.parse([f"Monday: {text}"]) == {}


def test_clusters_do_not_chain_distant_endpoints_or_depend_on_input_order():
    points = {"A": (0, 0), "B": (0, 0.01), "C": (0, 0.02), "far city": (40, 40)}
    groups = proximity.clusters(points)
    assert groups == [["A", "B"], ["C"], ["far city"]]
    assert groups == proximity.clusters(dict(reversed(list(points.items()))))
    block = proximity.render(points)
    assert "Nearby groups" in block and "not route verification" in block


def test_antipodal_coordinates_have_a_finite_distance():
    assert proximity.haversine_km((0, 0), (0, 180)) == pytest.approx(20015.1, abs=0.1)
