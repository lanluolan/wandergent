"""Shared fixtures.

The rate limiter is process-wide and its counters outlive a single test, so without this
the suite would start failing in whatever order happens to exhaust a limit first -- and it
would fail in a *different* test than the one that caused it. Resetting between tests
keeps each one independent, which is the property that makes a failure mean something.

The LLM settings are pinned for the same reason, one level out: `Settings` reads
`backend/.env`, so without this the suite's results depend on what a particular developer
happens to have configured. That was not hypothetical -- the planning tests passed only
because a key was present, and emptying `.env` (now the normal deployment) turned 35 of
them red at once. A suite documented as offline must not be able to tell.
"""

import pytest

from app.config import settings
from app.ratelimit import limiter


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def _pinned_llm_settings(monkeypatch):
    """Give every test the same LLM config, whatever `.env` says.

    Fixed placeholders, not a real provider: nothing here reaches a model -- the tests
    that exercise planning patch the orchestrator out. Tests about the *absence* of a
    setting monkeypatch it back to empty themselves, which wins over this and is undone
    the same way.
    """
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-a-real-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.test/v1")
    monkeypatch.setattr(settings, "openai_model", "test-model")
    # Empty means the built-in provider list, which is what the tests assert against.
    # Left unpinned, an operator's narrowing in `.env` silently changes what they mean.
    monkeypatch.setattr(settings, "llm_byok_base_urls", "")
