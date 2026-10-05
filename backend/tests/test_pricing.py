import httpx
import pytest

from app.agent.pricing import apply_observed_costs
from app.agent.schemas import Itinerary
from app.agent.validation import validate_itinerary
from app.config import settings
from app.tools.maps import _to_place, get_travel_time
from app.tools.money import google_money, range_average


def price(low=20, high=40, currency="USD"):
    return {
        "start_price": {"currency": currency, "amount": low},
        "end_price": {"currency": currency, "amount": high},
    }


def plan(mode="TRANSIT"):
    return Itinerary.model_validate(
        {
            "destination": "Chicago",
            "start_date": "2026-10-05",
            "end_date": "2026-10-05",
            "travelers": 3,
            "currency": "USD",
            "budget": 80,
            "days": [
                {
                    "date": "2026-10-05",
                    "summary": "Dinner",
                    "activities": [
                        {
                            "title": "Museum",
                            "location": "Museum",
                            "start_time": "16:00",
                            "end_time": "17:00",
                        },
                        {
                            "title": "Transfer",
                            "location": "Restaurant",
                            "category": "transport",
                            "travel_mode": mode,
                            "start_time": "17:00",
                            "end_time": "17:30",
                            "estimated_cost": 10,
                        },
                        {
                            "title": "Dinner",
                            "location": "Restaurant",
                            "category": "food",
                            "start_time": "18:00",
                            "end_time": "19:00",
                            "estimated_cost": 1,
                        },
                    ],
                }
            ],
        }
    )


def route(mode="TRANSIT", **changes):
    value = {
        "origin": "Museum",
        "destination": "Restaurant",
        "day": "2026-10-05",
        "mode": mode,
        "departure_minute": 17 * 60,
        "seconds": 600,
        "transit_fare": {"currency": "USD", "amount": 2.5},
        "toll_prices": [{"currency": "USD", "amount": 4}],
    }
    value.update(changes)
    return value


def test_money_units_nanos_and_missing_amount_not_confused_with_missing_currency():
    assert google_money({"currencyCode": "USD", "units": "2", "nanos": 500000000}).amount == 2.5
    assert google_money({"currencyCode": "USD"}).amount == 0
    for value in (
        {},
        {"currencyCode": "USD", "units": "NaN"},
        {"currencyCode": "USD", "units": "-1"},
        {"currencyCode": "USD", "nanos": 1.5},
        {"currencyCode": "USD", "nanos": 1000000000},
    ):
        assert google_money(value) is None


def test_places_normalize_range_and_do_not_read_legacy_price_level():
    place = _to_place(
        {
            "priceLevel": "PRICE_LEVEL_EXPENSIVE",
            "priceRange": {
                "startPrice": {"currencyCode": "USD", "units": "20"},
                "endPrice": {"currencyCode": "USD", "units": "40"},
            },
        }
    )
    assert place.price_range.average_price.amount == 30
    assert "price_level" not in place.model_dump()
    assert _to_place({"priceLevel": "PRICE_LEVEL_EXPENSIVE"}).price_range is None


def test_midpoint_requires_two_valid_bounds_in_same_currency_and_ignores_supplied_average():
    assert range_average({"start_price": {"currency": "USD", "amount": 20}}) is None
    assert range_average(price(40, 20)) is None
    mixed = price()
    mixed["end_price"]["currency"] = "EUR"
    assert range_average(mixed) is None
    assert (
        range_average({**price(), "average_price": {"currency": "USD", "amount": 1}}).amount == 30
    )


def test_restaurant_party_cost_and_transit_fare_reach_budget_and_are_idempotent():
    before = plan()
    priced = apply_observed_costs(before, {"Restaurant": price()}, [route()])
    assert before.days[0].activities[2].estimated_cost == 1
    assert priced.days[0].activities[2].estimated_cost == 90
    assert priced.days[0].activities[1].estimated_cost == 7.5
    assert priced.total_estimated_cost == 97.5
    assert "over_budget" in {v.code for v in validate_itinerary(priced).blocking}
    assert apply_observed_costs(priced, {"Restaurant": price()}, [route()]) == priced


def test_toll_is_per_vehicle_added_to_base_once_even_after_roundtrip():
    before = plan("DRIVE")
    priced = apply_observed_costs(before, {}, [route("DRIVE")])
    leg = priced.days[0].activities[1]
    assert leg.transport_base_cost == 10
    assert leg.estimated_cost == 14  # Not 10 + 4 * 3 people.
    resumed = Itinerary.model_validate_json(priced.model_dump_json())
    assert apply_observed_costs(resumed, {}, [route("DRIVE")]) == priced
    changed = apply_observed_costs(
        resumed, {}, [route("DRIVE", toll_prices=[{"currency": "USD", "amount": 6}])]
    )
    assert changed.days[0].activities[1].estimated_cost == 16
    no_tolls = apply_observed_costs(
        resumed, {}, [route("DRIVE", toll_prices=[], toll_prices_known=True)]
    )
    assert no_tolls.days[0].activities[1].estimated_cost == 10


def test_missing_and_foreign_currency_fees_do_not_become_free_or_invented_exchange_rates():
    before = plan()
    for observed in (
        route(transit_fare=None),
        route(transit_fare={"currency": "EUR", "amount": 2.5}),
    ):
        assert apply_observed_costs(before, {}, [observed]) == before
    priced = apply_observed_costs(before, {"Restaurant": price(currency="EUR")}, [])
    assert priced.days[0].activities[2].estimated_cost == 1
    assert "unverified estimate" in priced.days[0].activities[2].notes
    assert apply_observed_costs(before, {}, [], locked_days=frozenset({0})) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"day": "2026-10-06"},
        {"destination": "Another branch"},
        {"mode": "DRIVE"},
        {"departure_minute": 16 * 60},
    ],
)
def test_route_fees_only_bind_to_matching_leg_date_mode_and_departure(changes):
    before = plan()
    assert apply_observed_costs(before, {}, [route(**changes)]) == before


@pytest.mark.parametrize(
    "mode,advisory",
    [
        ("TRANSIT", {"transitFare": {"currencyCode": "USD", "units": "2", "nanos": 500000000}}),
        ("DRIVE", {"tollInfo": {"estimatedPrice": [{"currencyCode": "USD", "units": "4"}]}}),
    ],
)
async def test_google_route_request_and_response_include_fees(monkeypatch, mode, advisory):
    import json

    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

    def handler(request):
        mask = request.headers["X-Goog-FieldMask"]
        assert "transitFare" in mask and "tollInfo" in mask
        body = json.loads(request.content)
        if mode == "DRIVE":
            assert body["extraComputations"] == ["TOLLS"]
        else:
            assert "extraComputations" not in body
        return httpx.Response(
            200, json={"routes": [{"duration": "600s", "travelAdvisory": advisory}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await get_travel_time("Museum", "Restaurant", mode, client=client)
    assert result.ok
    if mode == "TRANSIT":
        assert result.transit_fare.amount == 2.5
    else:
        assert result.toll_prices[0].amount == 4


async def test_orchestrator_rechecks_budget_after_google_fares(monkeypatch):
    from datetime import timedelta

    from app.agent import orchestrator, transfers
    from app.agent.constraints import TripConstraints
    from app.observability import RunTrace, _trace, route_facts
    from app.tools.maps import TravelTime
    from app.tools.money import Money

    value = plan()
    value.budget = 20
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
    monkeypatch.setattr(orchestrator, "get_stream_writer", lambda: lambda _: None)

    async def offset(*args, **kwargs):
        return timedelta(hours=-5)

    async def measured(origin, destination, mode, **kwargs):
        return TravelTime(
            ok=True,
            origin=origin,
            destination=destination,
            mode=mode,
            seconds=600,
            transit_fare=Money(currency="USD", amount=10),
        )

    monkeypatch.setattr(transfers, "local_utc_offset", offset)
    monkeypatch.setattr(transfers, "get_travel_time", measured)
    state = {
        "itinerary": value,
        "constraints": TripConstraints(budget=20, travelers=3),
        "place_prices": {},
        "place_hours": {},
        "report": None,
    }
    token = _trace.set(RunTrace())
    try:
        outcome = await orchestrator.validate(state)
        assert outcome["itinerary"].days[0].activities[1].estimated_cost == 30
        assert "over_budget" in {v.code for v in outcome["report"].blocking}
        assert route_facts()[0]["departure_minute"] == 17 * 60
        assert route_facts()[0]["transit_fare"] == {"currency": "USD", "amount": 10}
    finally:
        _trace.reset(token)
