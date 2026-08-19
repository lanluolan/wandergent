"""Constraint-validation tests.

CLAUDE.md requires three counter-examples to be caught: over budget, time conflict, and
an unreasonable route. Those are the first three tests here and they are the acceptance
criteria for the layer; the rest guard the edges around them.
"""

from app.agent.schemas import Itinerary
from app.agent.validation import (
    MIN_TRANSFER_MINUTES,
    ValidationReport,
    Violation,
    validate_itinerary,
)


def build(days: list[dict], **overrides) -> Itinerary:
    """A single-day trip by default, so the accommodation rule stays out of the way.

    Most tests here are about one day's activities; the trip spanning a second date
    was incidental. Tests that need overnight stays override the dates explicitly.
    """
    payload = {
        "destination": "Chicago",
        "start_date": "2026-08-06",
        "end_date": "2026-08-06",
        "budget": 1000,
        "days": days,
    }
    payload.update(overrides)
    return Itinerary.model_validate(payload)


def day(date: str = "2026-08-06", **activities) -> dict:
    return {"date": date, "summary": "test day", **activities}


def activity(start: str, end: str, title: str, cost: float = 0.0, **extra) -> dict:
    return {
        "start_time": start,
        "end_time": end,
        "title": title,
        "estimated_cost": cost,
        **extra,
    }


# --- the three mandatory counter-examples -----------------------------------------


def test_catches_over_budget() -> None:
    itinerary = build(
        [
            day(
                activities=[
                    activity("09:00", "10:00", "Absurdly expensive afternoon tea", cost=1500.0)
                ]
            )
        ],
        budget=1000,
    )

    report = validate_itinerary(itinerary)

    assert not report.ok
    codes = [violation.code for violation in report.violations]
    assert "over_budget" in codes
    # The message has to carry the numbers, because it is fed back as repair input.
    message = next(v.message for v in report.violations if v.code == "over_budget")
    assert "1500" in message and "1000" in message


def test_catches_time_conflict() -> None:
    itinerary = build(
        [
            day(
                activities=[
                    activity(
                        "09:00",
                        "12:00",
                        "Art Institute of Chicago",
                        location="Art Institute of Chicago",
                    ),
                    activity("11:00", "13:00", "Lincoln Park Zoo", location="Lincoln Park Zoo"),
                ]
            )
        ]
    )

    report = validate_itinerary(itinerary)

    assert not report.ok
    assert "time_conflict" in [violation.code for violation in report.violations]


def test_catches_unreasonable_route() -> None:
    """Two places, back to back, with no time to travel between them."""
    itinerary = build(
        [
            day(
                activities=[
                    activity(
                        "09:00",
                        "11:00",
                        "Field Museum",
                        location="Field Museum",
                    ),
                    activity("11:00", "13:00", "Shedd Aquarium", location="Navy Pier"),
                ]
            )
        ]
    )

    report = validate_itinerary(itinerary)

    assert not report.ok
    violation = next(v for v in report.violations if v.code == "insufficient_transfer")
    assert str(MIN_TRANSFER_MINUTES) in violation.message


# --- edges around them -------------------------------------------------------------


def test_a_feasible_plan_passes() -> None:
    itinerary = build(
        [
            day(
                activities=[
                    activity(
                        "09:00",
                        "11:00",
                        "Field Museum",
                        cost=70,
                        location="Field Museum",
                    ),
                    activity("11:30", "13:00", "Lunch", cost=80, location="Millennium Park"),
                ]
            )
        ]
    )

    report = validate_itinerary(itinerary)

    assert report.ok
    assert report.violations == []


def test_an_explicit_transport_step_satisfies_the_transfer_rule() -> None:
    """A plan that says how you get there is not teleporting."""
    itinerary = build(
        [
            day(
                activities=[
                    activity(
                        "09:00",
                        "11:00",
                        "Field Museum",
                        location="Field Museum",
                    ),
                    activity(
                        "11:00",
                        "11:40",
                        "Subway to the aquarium",
                        category="transport",
                        location="Subway",
                    ),
                    activity("11:40", "13:00", "Shedd Aquarium", location="Navy Pier"),
                ]
            )
        ]
    )

    assert validate_itinerary(itinerary).ok


def transfer_codes(first_place: str, second_place: str) -> list[str]:
    itinerary = build(
        [
            day(
                activities=[
                    activity("09:00", "11:00", "Earlier", location=first_place),
                    activity("11:00", "12:00", "Later", location=second_place),
                ]
            )
        ]
    )
    return [v.code for v in validate_itinerary(itinerary).violations]


# These six pairs are real false positives from one live run against MiMo: every one is
# "visit X" followed by "eat near X", flagged as teleportation because the strings
# differ. The transfer rule has to tolerate how models actually name places.
def test_nearby_phrasing_is_not_a_teleport() -> None:
    assert transfer_codes("Millennium Park", "Millennium Park nearby diner") == []
    assert transfer_codes("Field Museum (Museum Campus)", "Museum Campus nearby restaurant") == []
    assert transfer_codes("Willis Tower", "Willis Tower nearby tea house") == []
    assert transfer_codes("Fulton Market", "Fulton Market District") == []
    assert transfer_codes("Magnificent Mile", "tea house inside the mall") == []
    assert transfer_codes("Chicago History Museum", "museum nearby restaurant") == []


def test_genuinely_distant_places_are_still_caught() -> None:
    """The forgiving rules must not swallow the counter-example they exist beside."""
    assert "insufficient_transfer" in transfer_codes("Field Museum", "Navy Pier")
    assert "insufficient_transfer" in transfer_codes("Empire State Building", "Central Park")


def test_unknown_location_is_not_treated_as_a_move() -> None:
    """A missing location is absence of evidence, not evidence of teleporting."""
    itinerary = build(
        [
            day(
                activities=[
                    activity("09:00", "11:00", "Visit", location="Field Museum"),
                    activity("11:00", "12:00", "Lunch"),
                ]
            )
        ]
    )

    assert validate_itinerary(itinerary).ok


def test_budget_absent_means_nothing_to_check() -> None:
    itinerary = build(
        [day(activities=[activity("09:00", "10:00", "Anything", cost=99999.0)])],
        budget=None,
    )

    assert [v.code for v in validate_itinerary(itinerary).violations] == []


def test_catches_day_outside_the_trip() -> None:
    itinerary = build([day(date="2026-09-01", activities=[activity("09:00", "10:00", "x")])])

    assert "day_out_of_range" in [v.code for v in validate_itinerary(itinerary).violations]


def test_catches_duplicate_days() -> None:
    itinerary = build(
        [
            day(activities=[activity("09:00", "10:00", "Morning")]),
            day(activities=[activity("14:00", "15:00", "Afternoon")]),
        ]
    )

    assert "duplicate_day" in [v.code for v in validate_itinerary(itinerary).violations]


def test_catches_unsociable_hours() -> None:
    itinerary = build([day(activities=[activity("04:00", "05:00", "Pre-dawn start for sunrise")])])

    assert "unsociable_hours" in [v.code for v in validate_itinerary(itinerary).violations]


def test_catches_an_overlong_day() -> None:
    itinerary = build(
        [
            day(
                activities=[
                    activity("06:00", "07:00", "Early"),
                    activity("22:30", "23:30", "Late"),
                ]
            )
        ]
    )

    assert "overlong_day" in [v.code for v in validate_itinerary(itinerary).violations]


def test_catches_an_empty_day() -> None:
    itinerary = build([{"date": "2026-08-06", "summary": "Empty", "activities": []}])

    assert "empty_day" in [v.code for v in validate_itinerary(itinerary).violations]


def test_activities_out_of_order_are_still_checked() -> None:
    """The model sometimes lists activities unsorted; overlaps must still surface."""
    itinerary = build(
        [
            day(
                activities=[
                    activity("11:00", "13:00", "Second", location="B"),
                    activity("09:00", "12:00", "First", location="A"),
                ]
            )
        ]
    )

    assert "time_conflict" in [v.code for v in validate_itinerary(itinerary).violations]


# --- specificity: the plan has to commit to real places ---------------------------
#
# Both rules below come from one live run: a three-day Los Angeles plan with no hotel
# at all, whose breakfast entry then read "the hotel or a nearby cafe" -- referring to
# lodging the plan had never chosen.


def overnight(days: list[dict], **overrides) -> Itinerary:
    """A trip with one night in it, which is what triggers the accommodation rule."""
    return build(days, start_date="2026-08-06", end_date="2026-08-07", **overrides)


def test_catches_a_multi_day_trip_with_nowhere_to_sleep() -> None:
    itinerary = overnight(
        [day(activities=[activity("09:00", "10:00", "Art Institute of Chicago")])]
    )

    violation = next(
        v for v in validate_itinerary(itinerary).violations if v.code == "missing_accommodation"
    )
    assert "1 night" in violation.message


def test_an_accommodation_activity_satisfies_the_rule() -> None:
    itinerary = overnight(
        [
            day(
                activities=[
                    activity(
                        "09:00",
                        "10:00",
                        "Art Institute of Chicago",
                        location="Art Institute of Chicago",
                    ),
                    activity(
                        "21:00",
                        "22:00",
                        "Check in at Hyatt Regency",
                        category="accommodation",
                        location="Hyatt Regency Chicago",
                    ),
                ]
            )
        ]
    )

    assert validate_itinerary(itinerary).ok


def test_saying_lodging_is_handled_also_satisfies_the_rule() -> None:
    """Some travellers already have a bed. Saying so is enough; silence is not."""
    itinerary = overnight(
        [day(activities=[activity("09:00", "10:00", "Art Institute of Chicago")])],
        notes=["Lodging is already arranged; no hotel needed."],
    )

    assert validate_itinerary(itinerary).ok


def test_a_single_day_trip_needs_no_bed() -> None:
    itinerary = build([day(activities=[activity("09:00", "10:00", "Art Institute of Chicago")])])

    assert "missing_accommodation" not in [v.code for v in validate_itinerary(itinerary).violations]


def hedge_codes(title: str, location: str | None = None) -> list[str]:
    itinerary = build([day(activities=[activity("09:00", "10:00", title, location=location)])])
    return [v.code for v in validate_itinerary(itinerary).violations]


def test_catches_a_plan_that_refuses_to_name_the_place() -> None:
    # Both taken verbatim from the live run that prompted the rule.
    assert "vague_venue" in hedge_codes("Dinner", "Grand Central Market or a similar food hall")
    assert "vague_venue" in hedge_codes("Breakfast", "the hotel or a nearby cafe")
    assert "vague_venue" in hedge_codes("Lunch", "some restaurant downtown")


def test_an_english_hedge_is_caught_whichever_article_it_wears() -> None:
    """One evasion, several spellings.

    Chinese hedges have no articles, so a substring list was enough. English ones do,
    and translating the app walked "or a similar food hall" straight past a list that
    contained "or similar" -- which is why this is a pattern now, and why the variants
    are pinned rather than left to whichever one someone happened to enumerate.
    """
    for phrasing in (
        "Grand Central Market or similar",
        "Grand Central Market or a similar market",
        "Grand Central Market or something similar",
        "a cafe or another nearby spot",
        "any restaurant in Silver Lake",
        "a museum of your choice",
        "dinner venue to be confirmed",
        "lunch spot TBD",
    ):
        assert "vague_venue" in hedge_codes("Meal", phrasing), phrasing


def test_named_places_are_left_alone() -> None:
    """The same bias as the transfer rule: a false alarm costs a wasted repair round."""
    assert hedge_codes("Dinner: deep dish", "Lou Malnati's (River North)") == []
    assert hedge_codes("Visit", "Los Angeles County Museum of Art") == []
    # "near X" alone legitimately places a named venue, so it must not trip the rule.
    assert hedge_codes("Lunch", "izakaya near Lincoln Park Zoo") == []
    # Words that merely contain a marker are not hedges: "Similan" is a real place and
    # "Anywhere Cafe" is a real name.
    assert hedge_codes("Dive trip", "Similan Islands") == []
    assert hedge_codes("Coffee", "Anywhere Cafe Shinsaibashi") == []


def test_repair_instructions_list_every_violation() -> None:
    itinerary = build(
        [
            day(
                activities=[
                    activity("09:00", "12:00", "A", cost=2000, location="Alpha"),
                    activity("11:00", "13:00", "B", location="Beta"),
                ]
            )
        ],
        budget=100,
    )

    instructions = validate_itinerary(itinerary).as_instructions()

    assert instructions.count("- ") >= 2


# --- the prompt and the validator agree on one number ------------------------------


async def test_the_transfer_minimum_the_model_is_told_is_the_one_it_is_judged_against() -> None:
    """The prompt used to say only "leave realistic travel time", so the model was
    guessing at a threshold the validator already knew. Now it is told the number -- and
    it is interpolated from `MIN_TRANSFER_MINUTES` rather than typed into the prompt, so
    changing the rule cannot leave the instruction behind. This pins that."""
    from app.agent.orchestrator import plan_trip
    from tests.fakes import ITINERARY_JSON, FakeLLM, completion

    llm = FakeLLM([completion(content=ITINERARY_JSON)])
    await plan_trip("3 days in Chicago", client=llm, model="test-model")

    prompt = llm.requests[0]["messages"][0]["content"]
    assert f"at least {MIN_TRANSFER_MINUTES} minutes" in prompt


# --- pace is the traveller's call, feasibility is not ------------------------------


def _report(*codes: str) -> ValidationReport:
    return ValidationReport(violations=[Violation(code=c, message=f"{c} happened") for c in codes])


def test_a_packed_day_does_not_make_a_plan_unsound() -> None:
    """Someone who says "pack it in" and gets a 13-hour day got what they asked for.
    Calling that a failure makes the agent argue with the person it works for."""
    assert _report("overlong_day", "unsociable_hours").ok


def test_a_locked_door_still_does() -> None:
    """Nobody asks to arrive somewhere shut. That is the plan contradicting the world."""
    assert not _report("outside_opening_hours").ok
    assert not _report("insufficient_transfer").ok
    assert not _report("time_conflict").ok
    assert not _report("over_budget").ok


def test_an_empty_day_is_a_defect_not_a_preference() -> None:
    """A day with nothing in it is the model failing to fill it, not a choice about pace."""
    assert not _report("empty_day").ok


def test_the_repair_round_is_not_spent_arguing_about_pace() -> None:
    """A repair costs a full generation. Spending it undoing an explicit wish is worse
    than spending it on nothing, because the model may comply and ruin the plan."""
    instructions = _report("overlong_day", "outside_opening_hours").as_instructions()

    assert "outside_opening_hours" in instructions
    assert "overlong_day" not in instructions


def test_advisory_findings_are_still_reported() -> None:
    """Not enforced is not the same as not mentioned -- a 13-hour day the traveller did
    not mean to ask for is exactly the thing worth telling them about."""
    report = _report("overlong_day", "outside_opening_hours")

    assert [v.code for v in report.advisory] == ["overlong_day"]
    assert [v.code for v in report.blocking] == ["outside_opening_hours"]
    assert len(report.violations) == 2
