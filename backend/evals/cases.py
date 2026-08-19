"""The regression case set.

Each case is a real request plus what must be true of the answer. They are chosen to
cover the capabilities that previous changes actually broke -- budget arithmetic, the
tool loop, the constraint layer, memory recall -- not to be exhaustive.

**Destinations are outside mainland China, deliberately** (2026-08-10). The product
targets international trips, and the maps tools are backed by Google, whose POI data
inside mainland China is thin -- a Chengdu search returns real but obscure venues with
one or two reviews. Grading the agent on a market it does not serve, using a data source
that is weak there, measures the wrong thing twice over.

**Requests are in English** (2026-08-17), matching the app. They used to be Chinese,
from when the interface was: an eval should exercise the agent the way the product
does, and the language of the request decides the language of the plan, which the
keyword checks then read.

Cases tagged `smoke` are the default subset, because a full run costs real tokens.
"""

from dataclasses import dataclass, field

from evals.checks import (
    Check,
    RevisionCheck,
    after,
    at_most_llm_calls,
    avoids,
    days,
    feasible,
    has_accommodation,
    highlights_on,
    kept_most_activities,
    mentions_any,
    now_schedules,
    produced_a_plan,
    trip_frame_unchanged,
    used_tool,
    within_budget,
)

# Words that mean hiking. Long or multi-word on purpose, so they cannot match a place
# name that merely contains "hill" or "mount" -- "Mount Fuji Museum" is an indoor exhibit.
#
# Bare "climb" was here and was wrong (2026-08-17): it failed a Lisbon plan for "Arco da
# Rua Augusta -- climb for rooftop views", which is a lift and a staircase inside a
# monument. Nobody who says "no hiking" means that. A checker that manufactures failures
# is worse than one that misses some, because it sends the next person chasing a bug that
# is not there.
HIKING = ("hiking", "hike", "trekking", "trek", "mountain climb", "rock climb", "summit trail")


@dataclass(frozen=True)
class Case:
    id: str
    request: str
    checks: list[Check]
    tags: tuple[str, ...] = ()
    #: Run first and not graded -- used to put something into memory.
    setup_request: str | None = None
    #: Memory is per-user, so cases that touch it need their own id.
    user_id: str = ""
    #: A follow-up that **edits** the plan `request` produced, rather than replacing it.
    #: Graded by `revision_checks`, which see both plans -- the only way to tell "did
    #: what I asked" from "regenerated the trip and happened to include ramen".
    revise_request: str | None = None
    revision_checks: list[RevisionCheck] = field(default_factory=list)


CASES: list[Case] = [
    Case(
        id="budget-tight",
        # Tight for Chicago, but a competent planner can fit it: two nights of budget
        # lodging plus modest food and entry fees. The first port of this case asked for
        # 12000 JPY, which -- once accommodation became mandatory -- no plan could meet,
        # so it was testing the same thing as `impossible-budget` while pretending to
        # test budget discipline.
        request="3 days in Chicago, budget 900 USD, I like food and museums",
        tags=("smoke",),
        checks=[
            produced_a_plan(),
            feasible(),
            within_budget(),
            days(3),
            used_tool("get_weather_forecast"),
            # Was 4, from before the maps tools existed. Two runs since then measured
            # 5-7 calls (5,5,6,6 and 7,5,5,5), so 7 accommodates what the agent actually
            # does while still catching the doubling that adding tools could hide.
            at_most_llm_calls(7),
        ],
    ),
    Case(
        id="specifics",
        # From a real complaint about a live plan: three days with no hotel at all, a
        # breakfast entry reading "the hotel or a nearby cafe", and not one dish named
        # despite the traveller saying they came for the food.
        request="3 days in Bangkok, budget 15000 THB, I am here to eat, somewhere central to stay",
        tags=("smoke",),
        checks=[
            produced_a_plan(),
            feasible(),
            within_budget(),
            days(3),
            has_accommodation(),
            highlights_on("food"),
        ],
    ),
    Case(
        id="exclusions",
        # 200 EUR over two days in Lisbon is achievable with one night of lodging. A run
        # on 2026-08-10 came in at 218 -- the agent not trimming to fit, which is a real
        # weakness and stays red rather than being tuned away.
        request="2 days in Lisbon, budget 200 EUR, no hiking and no boat trips",
        tags=("smoke",),
        checks=[
            produced_a_plan(),
            feasible(),
            within_budget(),
            days(2),
            avoids(*HIKING),
            avoids("boat trip", "cruise", "ferry"),
        ],
    ),
    Case(
        id="memory-recall",
        # The exclusion is stated in the setup run and NOT repeated here.
        setup_request="3 days in New York, budget 10000, I do not hike, I like art galleries",
        request="2 days in Seoul, budget 4000",
        user_id="eval-memory-user",
        tags=("smoke",),
        checks=[
            produced_a_plan(),
            feasible(),
            avoids(*HIKING),
            mentions_any("museum", "gallery", "exhibition", "art"),
        ],
    ),
    Case(
        id="beyond-forecast-horizon",
        # Deliberately outside the 16-day window: the weather tool must degrade and the
        # plan must still come out.
        request="4 days in Boston three months from now, budget 800 USD, I like history",
        tags=("full",),
        checks=[
            produced_a_plan(),
            feasible(),
            days(4),
            used_tool("get_weather_forecast"),
        ],
    ),
    Case(
        id="opening-hours",
        # Monday is the day most US museums close. Before `regularOpeningHours` was in
        # the field mask the agent could not know that and nothing downstream checked
        # it, so a plan could send someone to a locked door and still pass validation.
        request="1 day in Boston on the first Monday of next month, budget 200 USD, museums",
        tags=("full",),
        checks=[
            produced_a_plan(),
            feasible(),
            days(1),
            used_tool("search_places"),
            mentions_any("museum", "gallery", "library", "aquarium"),
        ],
    ),
    Case(
        id="revision",
        # The feature that most distinguishes this from asking a chat window: an edit
        # runs the whole validate-repair-revalidate cycle again. Unit tests script the
        # model, so they cannot catch the prompt rule going soft -- only a live case can
        # tell "changed the lunch" from "rewrote the trip and put a po-boy in it".
        request="2 days in New Orleans, budget 400 USD, I like food",
        tags=("smoke",),
        checks=[
            produced_a_plan(),
            feasible(),
            days(2),
            within_budget(),
            has_accommodation(),
        ],
        revise_request="change day 2 lunch to a po-boy place, leave everything else alone",
        revision_checks=[
            after(produced_a_plan()),
            # The claim worth making: the edit is still checked. Not "the model said it
            # kept the budget" -- the same validator ran on the result.
            after(feasible()),
            after(within_budget()),
            after(days(2)),
            after(has_accommodation()),
            now_schedules("po-boy", "po' boy", "poboy"),
            kept_most_activities(0.6),
            trip_frame_unchanged(),
        ],
    ),
    Case(
        id="impossible-budget",
        # 30 USD for 3 days in Singapore is not feasible. The interesting property is
        # that the agent does not silently pretend otherwise: either it fits, or the
        # violation survives into the report. Both are honest; claiming to fit is not.
        request="3 days in Singapore, budget 30 USD",
        tags=("full",),
        checks=[produced_a_plan(), within_budget()],
    ),
]

CASES_BY_ID: dict[str, Case] = {case.id: case for case in CASES}


def select(tag: str | None = None, ids: list[str] | None = None) -> list[Case]:
    if ids:
        return [CASES_BY_ID[case_id] for case_id in ids]
    if tag:
        return [case for case in CASES if tag in case.tags]
    return list(CASES)
