"""Tests for the one thing a Google price *band* can honestly prove.

Every cost in a plan is the model's invention, which makes the budget check only as
sound as those inventions: a dinner entered at 0 lets an over-budget trip validate
cleanly. A band is not a price and cannot be turned into one -- so the only inference
drawn is that a venue Google charges for does not cost nothing.
"""

import json
from datetime import date

from app.agent.orchestrator import harvest_place_prices
from app.agent.schemas import Activity, DayPlan, Itinerary
from app.agent.validation import validate_itinerary

DAY = date(2026, 9, 7)


def plan(cost: float, title: str = "Dinner at Alinea", category: str = "food") -> Itinerary:
    return Itinerary(
        destination="Chicago",
        start_date=DAY,
        end_date=DAY,
        budget=500,
        days=[
            DayPlan(
                date=DAY,
                summary="one day",
                activities=[
                    Activity(
                        start_time="19:00",
                        end_time="21:00",
                        title=title,
                        location="Alinea",
                        estimated_cost=cost,
                        category=category,
                    )
                ],
            )
        ],
    )


def codes(itinerary: Itinerary, prices: dict[str, str]) -> list[str]:
    report = validate_itinerary(itinerary, None, prices)
    return [violation.code for violation in report.violations]


def test_a_priced_venue_budgeted_at_nothing_is_caught() -> None:
    assert "understated_cost" in codes(plan(0.0), {"Alinea": "PRICE_LEVEL_VERY_EXPENSIVE"})


def test_the_cheapest_paid_band_still_is_not_free() -> None:
    """INEXPENSIVE is a claim about scale, not about being free of charge."""
    assert "understated_cost" in codes(plan(0.0), {"Alinea": "PRICE_LEVEL_INEXPENSIVE"})


def test_any_estimate_at_all_satisfies_it() -> None:
    """The band cannot say whether 40 is the right number, so it does not try."""
    assert "understated_cost" not in codes(plan(40.0), {"Alinea": "PRICE_LEVEL_VERY_EXPENSIVE"})


def test_a_free_venue_with_a_cost_is_not_a_contradiction() -> None:
    """A picnic in a free park still costs what the picnic costs. Flagging this would
    manufacture violations out of perfectly sensible plans."""
    assert "understated_cost" not in codes(plan(25.0), {"Alinea": "PRICE_LEVEL_FREE"})
    assert "understated_cost" not in codes(plan(0.0), {"Alinea": "PRICE_LEVEL_FREE"})


def test_an_unspecified_band_is_no_opinion() -> None:
    assert "understated_cost" not in codes(plan(0.0), {"Alinea": "PRICE_LEVEL_UNSPECIFIED"})


def test_a_venue_that_was_never_looked_up_is_no_opinion() -> None:
    """Most activities never reach a search. Silence must not read as a verdict."""
    assert "understated_cost" not in codes(plan(0.0), {})
    assert "understated_cost" not in codes(plan(0.0), {"Lou Malnati's": "PRICE_LEVEL_MODERATE"})


def test_transport_is_exempt() -> None:
    """A price band describes a venue, and a train ride is not one."""
    assert "understated_cost" not in codes(
        plan(0.0, title="Train to Alinea", category="transport"),
        {"Alinea": "PRICE_LEVEL_EXPENSIVE"},
    )


def test_accommodation_is_exempt() -> None:
    """A four-night hotel is billed once and entered at 0 on the other three nights, and
    Google gives hotels price bands. Checking them would flag every multi-night trip."""
    assert "understated_cost" not in codes(
        plan(0.0, title="Second night at the hotel", category="accommodation"),
        {"Alinea": "PRICE_LEVEL_EXPENSIVE"},
    )


def test_resting_somewhere_already_paid_for_is_exempt() -> None:
    assert "understated_cost" not in codes(
        plan(0.0, title="Rest at Alinea", category="rest"),
        {"Alinea": "PRICE_LEVEL_EXPENSIVE"},
    )


def test_the_message_names_the_band_so_the_repair_can_act_on_it() -> None:
    report = validate_itinerary(plan(0.0), None, {"Alinea": "PRICE_LEVEL_VERY_EXPENSIVE"})
    message = next(v.message for v in report.violations if v.code == "understated_cost")

    assert "very expensive" in message
    assert "Alinea" in message


# --- harvesting the band off the tool reply ---------------------------------------


def reply(**place) -> str:
    return json.dumps({"ok": True, "query": "restaurants", "places": [place]})


def test_the_band_is_kept_off_a_search_reply() -> None:
    kept: dict[str, str] = {}
    harvest_place_prices(
        "search_places", reply(name="Alinea", price_level="PRICE_LEVEL_EXPENSIVE"), kept
    )

    assert kept == {"Alinea": "PRICE_LEVEL_EXPENSIVE"}


def test_a_replayed_cache_hit_is_unwrapped() -> None:
    """Identical calls are answered from the run cache, one level deeper. The data is
    just as good the second time and must not be lost to the wrapper."""
    kept: dict[str, str] = {}
    inner = reply(name="Alinea", price_level="PRICE_LEVEL_EXPENSIVE")
    harvest_place_prices("search_places", json.dumps({"repeat": True, "result": inner}), kept)

    assert kept == {"Alinea": "PRICE_LEVEL_EXPENSIVE"}


def test_harvesting_never_raises_on_anything_it_is_handed() -> None:
    """Bookkeeping must not be able to fail a plan."""
    kept: dict[str, str] = {}
    for payload in ("", "not json", "[]", '{"places": [null, 3]}', '{"places": [{}]}'):
        harvest_place_prices("search_places", payload, kept)
    harvest_place_prices("get_weather_forecast", reply(name="Alinea", price_level="X"), kept)

    assert kept == {}
