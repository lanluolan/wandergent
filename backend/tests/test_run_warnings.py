"""The run-warning contract: a code the client renders, not a sentence it displays.

These are structural rather than behavioural. The behaviour is covered where the
warnings are produced (`test_tool_budget.py`, `test_orchestrator.py`); what is easy to
break silently is the contract itself -- a code added without a rendering, or a warning
and a violation arriving under the same name.
"""

from typing import get_args

from app.agent import results
from app.agent.results import RunWarning, RunWarningCode
from app.agent.validation import ViolationCode


def test_every_code_has_a_constructor() -> None:
    """A code with no constructor is a code some call site will spell by hand.

    The whole point of the type is that `detail` and `code` cannot drift apart, and
    they can only be held together in one place per code.
    """
    for code in get_args(RunWarningCode):
        factory = getattr(results, code, None)
        assert callable(factory), f"no constructor named {code!r} in app.agent.results"


def test_every_constructor_produces_its_own_code_and_a_usable_detail() -> None:
    """`detail` is the fallback a client falls back *to*. An empty one is a blank line."""
    built = [
        results.tool_calls_dropped(16, ["search_places", "search_places"], 3),
        results.tool_calls_spent(16),
        results.tool_rounds_spent(4),
        results.no_itinerary(),
    ]
    assert {warning.code for warning in built} == set(get_args(RunWarningCode))
    for warning in built:
        assert warning.detail.strip(), f"{warning.code} has no detail"


def test_dropped_calls_name_the_tools_once_each() -> None:
    """The model floods one tool: the warning says which, not the same name six times."""
    warning = results.tool_calls_dropped(16, ["search_places"] * 6, 6)

    assert warning.dropped_tools == ["search_places"]
    assert warning.dropped_calls == 6
    assert warning.budget == 16


def test_warning_codes_and_violation_codes_do_not_collide() -> None:
    """Two vocabularies, two lists, two renderings on the client.

    If a name appeared in both, a client mapping codes to sentences would have to know
    which list a code arrived in before it could render it -- which is precisely the
    coupling that splitting the two lists removes.
    """
    assert not set(get_args(RunWarningCode)) & set(get_args(ViolationCode))


def test_a_warning_serialises_with_its_parameters_present() -> None:
    """The client reads parameters, so absent ones have to be absent, not omitted."""
    payload = results.tool_calls_spent(16).model_dump()

    assert payload == {
        "code": "tool_calls_spent",
        "detail": "stopped calling tools after 16 calls",
        "budget": 16,
        "dropped_calls": None,
        "dropped_tools": [],
    }
    assert RunWarning.model_validate(payload).code == "tool_calls_spent"
