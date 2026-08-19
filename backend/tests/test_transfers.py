"""Tests for confirming suspected transfers against real travel times.

The point of this layer is that a *measurement* overrules a *guess*, in both
directions: a hop the string heuristic called impossible gets cleared when the walk is
short, and one it happened to flag stays flagged with the real number attached.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.agent import transfers
from app.agent.transfers import TRANSFER_MARGIN_MINUTES, confirm_transfers
from app.agent.validation import ValidationReport, Violation
from app.config import settings
from app.tools.maps import TravelTime

DAY = date(2026, 8, 6)


def transfer_violation(gap: int, origin: str = "A", destination: str = "B") -> Violation:
    return Violation(
        code="insufficient_transfer",
        day=DAY,
        origin=origin,
        destination=destination,
        gap_minutes=gap,
        message="heuristic said this looked tight",
    )


# Chicago in August. Every test in this file plans there, so the confirm pass gets a
# plausible offset instead of reaching for the network -- which it would otherwise do,
# since the key above is set and `local_utc_offset` is a real HTTP call.
CHICAGO = timedelta(hours=-5)


@pytest.fixture(autouse=True)
def _with_key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    async def _offset(place: str, on=None, **_):
        return CHICAGO

    monkeypatch.setattr(transfers, "local_utc_offset", _offset)


def fake_travel(seconds_by_mode: dict[str, int | None]):
    """Stand in for the Routes tool. None means that mode has no route."""

    async def _travel(origin: str, destination: str, mode: str = "WALK", **_) -> TravelTime:
        seconds = seconds_by_mode.get(mode)
        return TravelTime(
            ok=seconds is not None,
            origin=origin,
            destination=destination,
            mode=mode,
            seconds=seconds,
            error=None if seconds is not None else "no route",
        )

    return _travel


async def test_a_short_walk_clears_a_violation_the_heuristic_invented(monkeypatch) -> None:
    """The whole reason this exists: string matching cannot tell next-door from
    across-town, and it used to guess wrong six times in one plan."""
    monkeypatch.setattr(transfers, "get_travel_time", fake_travel({"WALK": 180, "DRIVE": 120}))

    report = await confirm_transfers(ValidationReport(violations=[transfer_violation(gap=10)]))

    assert report.ok


async def test_a_genuinely_long_hop_stays_flagged_with_the_real_number(monkeypatch) -> None:
    monkeypatch.setattr(transfers, "get_travel_time", fake_travel({"WALK": 3600, "DRIVE": 1500}))

    report = await confirm_transfers(ValidationReport(violations=[transfer_violation(gap=10)]))

    violation = report.violations[0]
    assert violation.code == "insufficient_transfer"
    # Driving is 25 minutes, so that is the number the repair instruction must carry.
    assert violation.needed_minutes == 25 + TRANSFER_MARGIN_MINUTES
    assert "25 minutes" in violation.message
    assert "10" in violation.message


async def test_driving_rescues_a_hop_that_is_only_a_long_walk(monkeypatch) -> None:
    """A long walk with a short taxi ride is a schedule with a taxi in it, not an
    infeasible one."""
    monkeypatch.setattr(transfers, "get_travel_time", fake_travel({"WALK": 5400, "DRIVE": 300}))

    report = await confirm_transfers(ValidationReport(violations=[transfer_violation(gap=15)]))

    assert report.ok


async def test_an_unmeasurable_pair_keeps_the_heuristics_word(monkeypatch) -> None:
    """Silence from the router is not evidence the schedule is fine."""
    monkeypatch.setattr(transfers, "get_travel_time", fake_travel({"WALK": None, "DRIVE": None}))

    report = await confirm_transfers(ValidationReport(violations=[transfer_violation(gap=5)]))

    assert not report.ok
    assert report.violations[0].needed_minutes is None


async def test_without_a_key_nothing_is_measured_and_nothing_changes(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_maps_api_key", "")

    async def _explode(*args, **kwargs):
        raise AssertionError("must not call the routes API without a key")

    monkeypatch.setattr(transfers, "get_travel_time", _explode)
    original = ValidationReport(violations=[transfer_violation(gap=5)])

    assert (await confirm_transfers(original)).violations == original.violations


async def test_other_violation_kinds_pass_straight_through(monkeypatch) -> None:
    async def _explode(*args, **kwargs):
        raise AssertionError("only transfer violations should be measured")

    monkeypatch.setattr(transfers, "get_travel_time", _explode)
    report = ValidationReport(
        violations=[Violation(code="over_budget", message="too dear", day=DAY)]
    )

    assert (await confirm_transfers(report)).violations == report.violations


async def test_one_failing_lookup_does_not_take_down_the_rest(monkeypatch) -> None:
    """One bad pair must not clear or crash the others."""
    calls: list[str] = []

    async def _travel(origin: str, destination: str, mode: str = "WALK", **_) -> TravelTime:
        calls.append(origin)
        if origin == "boom":
            raise RuntimeError("upstream exploded")
        return TravelTime(ok=True, origin=origin, destination=destination, mode=mode, seconds=120)

    monkeypatch.setattr(transfers, "get_travel_time", _travel)

    report = await confirm_transfers(
        ValidationReport(
            violations=[
                transfer_violation(gap=10, origin="boom"),
                transfer_violation(gap=10, origin="fine"),
            ]
        )
    )

    # The measurable one is cleared; the exploding one keeps its original verdict.
    assert [v.origin for v in report.violations] == ["boom"]


async def test_transit_is_preferred_when_it_exists(monkeypatch) -> None:
    """A city hop measured as a walk invents violations that are not there.

    Crossing town is 20 minutes by metro and 90 on foot; grading the schedule against
    the walk is what made the constraint layer pessimistic before transit was enabled.
    """
    asked: list[str] = []

    async def fake(origin, destination, mode="WALK", **kwargs):
        asked.append(mode)
        seconds = {"TRANSIT": 20 * 60, "WALK": 90 * 60, "DRIVE": 35 * 60}[mode]
        return TravelTime(
            ok=True, origin=origin, destination=destination, mode=mode, seconds=seconds
        )

    monkeypatch.setattr("app.agent.transfers.get_travel_time", fake)
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    report = ValidationReport(violations=[transfer_violation(gap=30)])
    confirmed = await confirm_transfers(report)

    assert set(asked) == {"TRANSIT", "WALK", "DRIVE"}
    # 20 + 5 margin <= 30 available, so the violation clears. On the walk alone it would
    # have survived and sent the agent off to repair a schedule that was already fine.
    assert confirmed.ok


async def test_a_region_without_transit_falls_back(monkeypatch) -> None:
    """Japan returns no transit route. That must degrade, not disable the check."""

    async def fake(origin, destination, mode="WALK", **kwargs):
        if mode == "TRANSIT":
            return TravelTime(
                ok=False, origin=origin, destination=destination, mode=mode, error="no route"
            )
        seconds = {"WALK": 90 * 60, "DRIVE": 40 * 60}[mode]
        return TravelTime(
            ok=True, origin=origin, destination=destination, mode=mode, seconds=seconds
        )

    monkeypatch.setattr("app.agent.transfers.get_travel_time", fake)
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    confirmed = await confirm_transfers(ValidationReport(violations=[transfer_violation(gap=10)]))

    # Falls back to the shorter of walk/drive -- 40 min -- and the violation survives.
    assert not confirmed.ok
    assert "40 minutes by drive" in confirmed.violations[0].message


async def test_the_message_names_the_mode_it_measured(monkeypatch) -> None:
    """ "25 minutes" means different things on foot and on a train; the repair
    instruction has to say which, or the model cannot judge the fix."""

    async def fake(origin, destination, mode="WALK", **kwargs):
        if mode != "TRANSIT":
            return TravelTime(
                ok=False, origin=origin, destination=destination, mode=mode, error="no route"
            )
        return TravelTime(
            ok=True, origin=origin, destination=destination, mode=mode, seconds=25 * 60
        )

    monkeypatch.setattr("app.agent.transfers.get_travel_time", fake)
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    confirmed = await confirm_transfers(ValidationReport(violations=[transfer_violation(gap=5)]))

    assert "by transit" in confirmed.violations[0].message
    assert confirmed.violations[0].needed_minutes == 30


# --- measuring at the hour on the plan -------------------------------------------
#
# A fixed 11:00 UTC reference was mid-morning in London and 05:00 in Chicago, so every
# American plan was measured on empty roads against a skeleton timetable. The whole
# purpose of this layer is that a measurement beats a guess, and a measurement taken at
# the wrong hour is just a better-dressed guess.


def test_the_departure_is_the_travellers_local_clock_not_utc() -> None:
    """18:00 in Chicago is 23:00 UTC. Ask about the wrong one and you get free roads."""
    moment = transfers._departure_instant(date(2099, 8, 6), 18 * 60, CHICAGO)

    assert moment is not None
    assert moment.hour == 23
    assert moment.date() == date(2099, 8, 6)


def test_a_past_trip_slides_forward_by_whole_weeks() -> None:
    """Google will not price yesterday's rush hour. Keep the weekday and the clock."""
    past = date(2020, 8, 6)  # a Thursday
    moment = transfers._departure_instant(past, 18 * 60, CHICAGO)

    assert moment is not None
    assert moment > datetime.now(UTC)
    # Both things a transit timetable depends on survive the slide.
    assert (moment + CHICAGO).weekday() == past.weekday()
    assert (moment + CHICAGO).hour == 18


def test_no_departure_time_means_no_claim_about_one() -> None:
    """An older saved violation has no `depart_at_minute`. Do not invent midnight."""
    assert transfers._departure_instant(date(2099, 8, 6), None, CHICAGO) is None
    assert transfers._departure_instant(None, 18 * 60, CHICAGO) is None


async def test_the_measurement_is_asked_for_the_hour_on_the_plan(monkeypatch) -> None:
    """End to end: the violation's own departure time reaches the Routes lookup."""
    asked: list[datetime | None] = []

    async def _travel(origin, destination, mode="WALK", *, depart_at=None, **_) -> TravelTime:
        asked.append(depart_at)
        return TravelTime(ok=True, origin=origin, destination=destination, mode=mode, seconds=600)

    monkeypatch.setattr(transfers, "get_travel_time", _travel)
    violation = transfer_violation(10).model_copy(update={"depart_at_minute": 17 * 60 + 30})

    await confirm_transfers(ValidationReport(violations=[violation]))

    assert asked and all(moment is not None for moment in asked)
    # 17:30 Chicago, whichever week it landed in after the slide forward.
    assert all((moment + CHICAGO).hour == 17 for moment in asked)  # type: ignore[operator]


async def test_an_unresolvable_timezone_still_measures(monkeypatch) -> None:
    """No offset is a reason to fall back, never a reason to stop measuring."""

    async def _no_offset(place: str, on=None, **_):
        return None

    monkeypatch.setattr(transfers, "local_utc_offset", _no_offset)
    monkeypatch.setattr(transfers, "get_travel_time", fake_travel({"WALK": 180, "DRIVE": 120}))

    report = await confirm_transfers(ValidationReport(violations=[transfer_violation(10)]))

    assert report.ok  # cleared by the 3-minute walk, exactly as with an offset
