"""Shared fixtures.

The rate limiter is process-wide and its counters outlive a single test, so without this
the suite would start failing in whatever order happens to exhaust a limit first -- and it
would fail in a *different* test than the one that caused it. Resetting between tests
keeps each one independent, which is the property that makes a failure mean something.
"""

import pytest

from app.ratelimit import limiter


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    limiter.reset()
    yield
    limiter.reset()
