"""Restaurant priceRange midpoint budgets, without inventing missing prices."""

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


def price(low=20, high=40, currency="USD"):
    return {
        "start_price": {"currency": currency, "amount": low},
        "end_price": {"currency": currency, "amount": high},
    }


def codes(itinerary, prices):
    return [v.code for v in validate_itinerary(itinerary, None, prices).violations]


def test_restaurant_cost_below_midpoint_is_caught():
    for amount in (0, 1, 29.99):
        assert "understated_cost" in codes(plan(amount), {"Alinea": price()})


def test_midpoint_or_higher_satisfies_price_check():
    for amount in (30, 40):
        assert "understated_cost" not in codes(plan(amount), {"Alinea": price()})


def test_midpoint_uses_whole_party_and_trip_currency():
    itinerary = plan(30)
    itinerary.travelers = 2
    assert "understated_cost" in codes(itinerary, {"Alinea": price()})
    assert "understated_cost" not in codes(itinerary, {"Alinea": price(currency="EUR")})


def test_zero_price_range_does_not_prove_picnic_cost_wrong():
    assert "understated_cost" not in codes(plan(25), {"Alinea": price(0, 0)})
    assert "understated_cost" not in codes(plan(0), {"Alinea": price(0, 0)})


def test_missing_one_sided_invalid_or_unmatched_range_is_no_opinion():
    for prices in (
        {},
        {"Alinea": {}},
        {"Alinea": {"start_price": {"currency": "USD", "amount": 20}}},
        {"Alinea": price(40, 20)},
        {"Elsewhere": price()},
    ):
        assert "understated_cost" not in codes(plan(0), prices)


def test_place_range_does_not_price_transport_hotel_rest_or_admission():
    for category in ("transport", "accommodation", "rest", "sightseeing", "activity"):
        assert "understated_cost" not in codes(plan(0, category=category), {"Alinea": price()})


def test_repair_message_names_midpoint_venue_currency_and_party_size():
    report = validate_itinerary(plan(0), None, {"Alinea": price()})
    message = next(v.message for v in report.violations if v.code == "understated_cost")
    assert "priceRange midpoint" in message
    assert "Alinea" in message and "30.00 USD" in message


def reply(**place):
    return json.dumps({"ok": True, "places": [place]})


def test_range_is_harvested_for_restaurants_only_and_legacy_band_is_ignored():
    kept = {}
    harvest_place_prices(
        "search_places", reply(name="Alinea", types=["restaurant"], price_range=price()), kept
    )
    assert kept == {"Alinea": price()}
    harvest_place_prices(
        "search_places", reply(name="Hotel", types=["hotel"], price_range=price()), kept
    )
    harvest_place_prices(
        "search_places",
        reply(name="Legacy", types=["restaurant"], price_level="PRICE_LEVEL_EXPENSIVE"),
        kept,
    )
    assert kept == {"Alinea": price()}


def test_replayed_cache_hit_is_unwrapped():
    kept = {}
    inner = reply(name="Alinea", types=["restaurant"], price_range=price())
    harvest_place_prices("search_places", json.dumps({"repeat": True, "result": inner}), kept)
    assert kept == {"Alinea": price()}


def test_harvesting_never_raises_on_malformed_or_other_tool_results():
    kept = {}
    for payload in ("", "not json", "[]", '{"places": [null, 3]}', '{"places": [{}]}'):
        harvest_place_prices("search_places", payload, kept)
    harvest_place_prices(
        "get_weather_forecast",
        reply(name="Alinea", types=["restaurant"], price_range=price()),
        kept,
    )
    assert kept == {}
