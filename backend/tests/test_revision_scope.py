"""The revision boundary is deterministic and independent of model obedience."""

from datetime import timedelta

from app.agent.revision import resolve_revision_scope, revision_payload
from app.agent.schemas import Itinerary
from tests.fakes import ITINERARY_JSON


def test_editable_day_locks_every_other_day() -> None:
    scope = resolve_revision_scope("Add Grand Central Market on day 1. Leave day 2 unchanged.", 2)

    assert scope.editable_days == frozenset({0})
    assert scope.locked_days == frozenset({1})


def test_explicit_lock_without_an_editable_day_does_not_guess_the_target() -> None:
    scope = resolve_revision_scope("Improve the plan, but leave day 2 unchanged.", 3)

    assert scope.editable_days == frozenset()
    assert scope.locked_days == frozenset({1})


def test_date_move_unlocks_dates_but_not_budget_or_party_size() -> None:
    scope = resolve_revision_scope(
        "Move the entire trip to 2026-10-27 through 2026-10-28. Keep the budget and party size.",
        2,
    )

    assert {"start_date", "end_date"} <= scope.editable_frame
    assert "budget" not in scope.editable_frame
    assert "travelers" not in scope.editable_frame
    assert scope.locked_days == frozenset()


def test_long_revision_context_keeps_only_the_editable_day_in_full() -> None:
    base = Itinerary.model_validate_json(ITINERARY_JSON)
    first = base.days[0]
    many_days = []
    for index in range(30):
        activity = first.activities[0].model_copy(
            update={"title": f"Unique activity day {index + 1}"}
        )
        many_days.append(
            first.model_copy(
                update={"date": base.start_date + timedelta(days=index), "activities": [activity]}
            )
        )
    long_trip = base.model_copy(
        update={"end_date": base.start_date + timedelta(days=29), "days": many_days}
    )
    scope = resolve_revision_scope("Change lunch on day 30.", len(long_trip.days))

    compact = revision_payload(long_trip, scope)
    full = long_trip.model_dump_json()

    assert "Unique activity day 30" in compact
    assert "Unique activity day 1" not in compact
    assert len(compact) < len(full) / 2
