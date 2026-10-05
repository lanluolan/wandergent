from app.agent.schemas import Itinerary
from app.agent.validation import _match_known, _minutes, transport_mode
from app.tools.money import Money, range_average


def apply_observed_costs(
    plan: Itinerary | None,
    prices: dict[str, dict],
    routes: list[dict],
    *,
    locked_days: frozenset[int] = frozenset(),
) -> Itinerary | None:
    if plan is None:
        return None
    result = plan.model_copy(deep=True)

    def note(activity, message):
        if message not in (activity.notes or ""):
            activity.notes = " ".join(filter(None, [activity.notes, message]))

    for day_index, day in enumerate(result.days):
        if day_index in locked_days:
            continue
        for activity in day.activities:
            if activity.category != "food":
                continue
            observed = _match_known(activity, prices)
            average = range_average(observed)
            if average and average.currency == result.currency:
                expected = round(average.amount * result.travelers, 2)
                if activity.estimated_cost == expected:
                    continue
                activity.estimated_cost = expected
                note(
                    activity,
                    f"Restaurant budget uses Google priceRange midpoint "
                    f"{average.amount:.2f} {average.currency}/person × {result.travelers}; "
                    "an estimate, not a menu quote.",
                )
            elif observed:
                note(
                    activity,
                    "Restaurant priceRange is incomplete or in another currency; "
                    "cost remains an unverified estimate.",
                )
        ordered = sorted(day.activities, key=lambda item: item.start_time)
        stops = [
            (i, item)
            for i, item in enumerate(ordered)
            if item.category != "transport" and item.location
        ]
        for (left_index, left), (right_index, right) in zip(stops, stops[1:], strict=False):
            legs = [
                item
                for item in ordered[left_index + 1 : right_index]
                if item.category == "transport"
            ]
            # A route total cannot safely be split across multiple independently priced legs.
            if len(legs) != 1:
                continue
            leg = legs[0]
            mode = transport_mode(leg)
            matching = [
                route
                for route in routes
                if route.get("day") == str(day.date)
                and route.get("origin") == left.location
                and route.get("destination") == right.location
                and route.get("mode") == mode
                and route.get("departure_minute") == _minutes(left.end_time)
                and route.get("seconds") is not None
            ]
            if not matching:
                continue
            route = matching[-1]
            if mode == "TRANSIT":
                fare = route.get("transit_fare")
                if fare and fare.get("currency") == result.currency:
                    fare = Money.model_validate(fare)
                    leg.estimated_cost = round(fare.amount * result.travelers, 2)
                    note(
                        leg,
                        "Google Routes fare × traveller count; assumes one quoted fare "
                        "per person, without concession discounts.",
                    )
            elif mode == "DRIVE":
                tolls = route.get("toll_prices") or []
                if route.get("toll_prices_known", bool(tolls)) and all(
                    price.get("currency") == result.currency for price in tolls
                ):
                    total = sum(Money.model_validate(price).amount for price in tolls)
                    if leg.transport_base_cost is None:
                        leg.transport_base_cost = leg.estimated_cost
                    leg.estimated_cost = round(leg.transport_base_cost + total, 2)
                    note(
                        leg,
                        "Includes Google estimated tolls once for one vehicle; "
                        "fuel/rental base cost is separate and remains an estimate.",
                    )
    return result
