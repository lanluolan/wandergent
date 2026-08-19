"""Deterministic checks for eval cases.

No LLM judge. Most eval harnesses need one because "is this output good?" has no
objective answer -- but this project already has a hard-constraint validator, an
itinerary with typed fields, and a record of which tools ran, so almost everything
worth asserting is a plain function of the result.

A check returns `None` when it passes and a failure reason when it does not, so the
report says *why* rather than just `False`.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.agent.results import PlanResult

CheckFn = Callable[[PlanResult], str | None]


@dataclass(frozen=True)
class Check:
    name: str
    fn: CheckFn

    def __call__(self, result: PlanResult) -> str | None:
        return self.fn(result)


#: A check that grades an *edit*: it sees the plan before and the plan after.
RevisionCheckFn = Callable[[PlanResult, PlanResult], str | None]


@dataclass(frozen=True)
class RevisionCheck:
    name: str
    fn: RevisionCheckFn

    def __call__(self, before: PlanResult, after: PlanResult) -> str | None:
        return self.fn(before, after)


def _scheduled_text(result: PlanResult) -> str:
    """What is actually on the schedule: activity titles, places and categories.

    Deliberately excludes day summaries, activity notes and trip notes. Those are
    *commentary about* the plan, and the first live eval run proved why that matters:
    a plan that correctly avoided hiking said so in its notes -- "已避免爬山（如南山、
    歌乐山）" -- and a keyword check over all text called that a violation. The claim
    being tested is "nothing hiking-related is scheduled", and only the schedule can
    answer it.
    """
    itinerary = result.itinerary
    if itinerary is None:
        return ""
    parts: list[str] = []
    for day in itinerary.days:
        for activity in day.activities:
            parts.extend(filter(None, [activity.title, activity.location, activity.category]))
            # Highlights are recommendations, not commentary: "try the hiking trail"
            # is something the plan proposes, so an exclusion has to reach it.
            parts.extend(activity.highlights)
    return " ".join(parts).lower()


def produced_a_plan() -> Check:
    def check(result: PlanResult) -> str | None:
        if result.itinerary is None:
            return f"no itinerary; warnings={[w.detail for w in result.warnings]}"
        return None

    return Check("produced a plan", check)


def feasible() -> Check:
    """The hard-constraint layer is the grader: zero unresolved violations.

    The failure text carries the *specifics*, not just the codes. A report reading
    "insufficient_transfer, insufficient_transfer, insufficient_transfer" says a rule
    fired three times and nothing about which hops or how tight -- which is a debugging
    session, not a finding. Same lesson the keyword checks learned on 2026-08-05.
    """

    def describe(violation) -> str:
        if violation.code != "insufficient_transfer":
            return f"{violation.code} ({violation.message})"
        needed = (
            f", needs {violation.needed_minutes}" if violation.needed_minutes is not None else ""
        )
        return (
            f"{violation.code} on {violation.day}: {violation.origin} -> "
            f"{violation.destination}, {violation.gap_minutes} min gap{needed}"
        )

    def check(result: PlanResult) -> str | None:
        report = result.validation
        if report is None:
            return "no validation report"
        if not report.ok:
            return " | ".join(describe(violation) for violation in report.violations)
        return None

    return Check("passes hard constraints", check)


def within_budget() -> Check:
    def check(result: PlanResult) -> str | None:
        itinerary = result.itinerary
        if itinerary is None:
            return "no itinerary"
        if itinerary.budget is None:
            return "the plan dropped the stated budget"
        if itinerary.total_estimated_cost > itinerary.budget:
            return f"{itinerary.total_estimated_cost:.0f} > {itinerary.budget:.0f}"
        return None

    return Check("within budget", check)


def days(expected: int) -> Check:
    def check(result: PlanResult) -> str | None:
        itinerary = result.itinerary
        if itinerary is None:
            return "no itinerary"
        if len(itinerary.days) != expected:
            return f"got {len(itinerary.days)} days, wanted {expected}"
        return None

    return Check(f"{expected} days", check)


def used_tool(name: str) -> Check:
    def check(result: PlanResult) -> str | None:
        called = [call.name for call in result.tool_calls]
        if name not in called:
            return f"tools called: {called or 'none'}"
        return None

    return Check(f"called {name}", check)


def avoids(*keywords: str) -> Check:
    """Nothing in the user-visible text mentions what the traveller ruled out.

    Multi-character keywords only: a bare 山 would flag 中山公园, which is a park.

    Failures quote the surrounding text, because a keyword match is not proof on its
    own -- a plan that says "no hiking involved" contains the word too, and a check
    that cannot tell those apart would send the next person chasing a bug that is not
    there.
    """

    def check(result: PlanResult) -> str | None:
        text = _scheduled_text(result)
        snippets = []
        for word in keywords:
            index = text.find(word.lower())
            if index >= 0:
                start = max(0, index - 25)
                snippets.append(f"{word}: ...{text[start : index + len(word) + 25]}...")
        if snippets:
            return " | ".join(snippets)
        return None

    return Check(f"avoids {'/'.join(keywords[:3])}", check)


def mentions_any(*keywords: str) -> Check:
    def check(result: PlanResult) -> str | None:
        text = _scheduled_text(result)
        if not any(word.lower() in text for word in keywords):
            return f"none of {list(keywords)} appeared"
        return None

    return Check(f"mentions {'/'.join(keywords[:3])}", check)


def has_accommodation() -> Check:
    """A trip with nights in it says where they are spent.

    The validator enforces this too, but `feasible()` only proves the violation was
    *resolved*. This asserts the plan actually contains a bed rather than an excuse
    in the notes, which is what a traveller needs.
    """

    def check(result: PlanResult) -> str | None:
        itinerary = result.itinerary
        if itinerary is None:
            return "no itinerary"
        if itinerary.end_date <= itinerary.start_date:
            return None
        stays = [
            activity.title
            for day in itinerary.days
            for activity in day.activities
            if activity.category == "accommodation"
        ]
        if not stays:
            return "no accommodation activity in a multi-night trip"
        return None

    return Check("books somewhere to sleep", check)


def highlights_on(category: str, coverage: float = 0.5) -> Check:
    """Enough activities of a kind carry concrete specifics.

    Not a hard constraint, deliberately: an itinerary without dish recommendations is
    still feasible, so failing validation over it would trigger repair rounds for a
    matter of degree. It belongs here instead, where it is measured rather than
    enforced. Coverage rather than "all", because some venues genuinely have nothing
    worth calling out and a check that fails on those is noise.
    """

    def check(result: PlanResult) -> str | None:
        itinerary = result.itinerary
        if itinerary is None:
            return "no itinerary"
        matching = [
            activity
            for day in itinerary.days
            for activity in day.activities
            if activity.category == category
        ]
        if not matching:
            return f"no {category} activities to check"
        with_detail = [activity for activity in matching if activity.highlights]
        if len(with_detail) / len(matching) < coverage:
            bare = [activity.title for activity in matching if not activity.highlights]
            return (
                f"{len(with_detail)}/{len(matching)} {category} activities have highlights; "
                f"missing on {bare[:4]}"
            )
        return None

    return Check(f"{category} carries specifics", check)


def at_most_llm_calls(limit: int) -> Check:
    """A cost regression is a failure, not just a number in a table."""

    def check(result: PlanResult) -> str | None:
        calls = result.usage.llm_calls
        if calls > limit:
            return f"{calls} calls, budget {limit}"
        return None

    return Check(f"<= {limit} LLM calls", check)


# --- revision checks ----------------------------------------------------------------
#
# An edit has two failure modes a single-plan check cannot see. It can fail to do what
# was asked, and it can do it by rewriting the whole trip -- which produces a perfectly
# valid itinerary that is nonetheless the wrong answer to "change the lunch". Grading
# both plans is the only way to tell those apart.


def _activity_keys(result: PlanResult) -> set[tuple[str, str]]:
    """Identity of each scheduled item: when it starts and what it is.

    Time and title together, because either alone is too loose -- a rewritten day keeps
    the times, and a re-timed day keeps the titles.
    """
    itinerary = result.itinerary
    if itinerary is None:
        return set()
    return {
        (activity.start_time, activity.title)
        for day in itinerary.days
        for activity in day.activities
    }


def after(check: Check) -> RevisionCheck:
    """Apply an ordinary check to the revised plan.

    This is what makes the eval express the actual promise of the feature: an edit is
    still budget-checked, still schedule-checked, still route-checked. `after(feasible())`
    is the assertion that a chat window cannot make.
    """
    return RevisionCheck(f"after edit: {check.name}", lambda _before, revised: check(revised))


def now_schedules(*words: str) -> RevisionCheck:
    """The edit actually landed: the revised schedule mentions what was asked for."""

    def check(before: PlanResult, revised: PlanResult) -> str | None:
        text = _scheduled_text(revised)
        if not text:
            return "no itinerary to inspect"
        if any(word.lower() in text for word in words):
            return None
        return f"none of {list(words)} appears in the revised schedule"

    return RevisionCheck(f"edit landed: {'/'.join(words)}", check)


def kept_most_activities(min_fraction: float = 0.6) -> RevisionCheck:
    """Most of the trip survived the edit untouched.

    The check that stops "revision" from meaning "regenerate". A model told to swap one
    lunch can produce a completely different, completely valid three days -- passing
    every other check while being the wrong answer. Observed live: swapping one lunch
    left 7 of 8 activities byte-identical, so a 0.6 floor has room for a knock-on
    retime or two without room for a rewrite.
    """

    def check(before: PlanResult, revised: PlanResult) -> str | None:
        original = _activity_keys(before)
        if not original:
            return "no original itinerary to compare against"
        kept = original & _activity_keys(revised)
        fraction = len(kept) / len(original)
        if fraction < min_fraction:
            lost = sorted(title for _, title in original - _activity_keys(revised))
            return (
                f"only {len(kept)}/{len(original)} activities survived "
                f"({fraction:.0%} < {min_fraction:.0%}); dropped {lost[:5]}"
            )
        return None

    return RevisionCheck(f">= {min_fraction:.0%} of activities kept", check)


def trip_frame_unchanged() -> RevisionCheck:
    """An edit to one activity must not move the trip.

    Destination, dates, party size, currency and budget are the frame the plan is
    checked against. A revision that quietly widened the budget would make
    `after(within_budget())` pass for the wrong reason.
    """

    FIELDS = ("destination", "start_date", "end_date", "travelers", "currency", "budget")

    def check(before: PlanResult, revised: PlanResult) -> str | None:
        old, new = before.itinerary, revised.itinerary
        if old is None or new is None:
            return "missing an itinerary to compare"
        drifted = [
            f"{field}: {getattr(old, field)!r} -> {getattr(new, field)!r}"
            for field in FIELDS
            if getattr(old, field) != getattr(new, field)
        ]
        if drifted:
            return "; ".join(drifted)
        return None

    return RevisionCheck("trip frame unchanged", check)
