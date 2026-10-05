"""Shared fixtures.

The rate limiter is process-wide and its counters outlive a single test, so without a reset
the suite fails in whatever order happens to exhaust a limit first -- and fails in a
*different* test than the one that caused it.

The databases are redirected for a blunter reason: otherwise a test going through the HTTP
layer writes into whatever `.env` points at, which is the developer's own running app.
`test_publishing_is_capped_per_account` left ten trips in the real community feed on every
run, 170 of them before anyone looked.

The LLM settings are pinned one level out: `Settings` reads `backend/.env`, so without this
the results depend on what a given developer has configured. The planning tests passed only
because a key was present, and emptying `.env` turned 35 of them red at once. A suite
documented as offline must not be able to tell.
"""

import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.ratelimit import limiter
from app.tools.cache import shared_tool_cache

TEST_TEMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest-work"


@pytest.fixture(autouse=True)
def _offline_mcp_service(monkeypatch):
    from app.agent import orchestrator, transfers
    from app.tools import registry

    async def fake_service(name, arguments, context=None):
        return await registry.execute_local_tool(name, arguments, context)

    @asynccontextmanager
    async def fake_session():
        yield None

    monkeypatch.setattr(registry, "call_mcp_tool", fake_service)
    monkeypatch.setattr(orchestrator, "research_session", fake_session)
    monkeypatch.setattr(transfers, "research_session", fake_session)


if os.name == "nt":

    @pytest.fixture
    def tmp_path():
        """Workspace-local temp path that remains usable under the Windows sandbox.

        Pytest creates its Windows base with mode 0700. In the restricted project runner
        that directory immediately becomes inaccessible even to the creating process. A
        normal project-local directory preserves the fixture contract without writing to
        the user's global temp folder. Other platforms keep pytest's native fixture.
        """
        TEST_TEMP_ROOT.mkdir(exist_ok=True)
        path = TEST_TEMP_ROOT / uuid4().hex
        path.mkdir()
        yield path
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    limiter.reset()
    shared_tool_cache.clear()
    yield
    limiter.reset()
    shared_tool_cache.clear()


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
    monkeypatch.setattr(settings, "google_maps_api_key", "")
    monkeypatch.setattr(settings, "brave_search_api_key", "")
    monkeypatch.setattr(settings, "otel_exporter_otlp_traces_endpoint", "")
    monkeypatch.setattr(settings, "trace_directory", "")
    monkeypatch.setattr(settings, "trace_model_prices", {})


@pytest.fixture(autouse=True)
def _isolated_databases(tmp_path, monkeypatch):
    """Point every store at this test's own directory, never at the real files.

    The three stores are module-level singletons, and the modules using them bound the
    instance at import -- so each must be replaced where it was imported *to*, not only
    where it was created. Missing a binding is invisible: the test passes and the write
    lands in the developer's database.

    Construction is free (SQLite opens lazily), so this costs nothing on tests that never
    touch a store. A test wanting its own still builds one and patches over this.
    """
    from app import auth, community, main, memory
    from app.auth.store import AuthStore
    from app.community.store import CommunityStore
    from app.feedback import FeedbackStore
    from app.memory.store import PreferenceStore
    from app.tools import memory as memory_tool

    accounts = AuthStore(tmp_path / "auth.db")
    monkeypatch.setattr(auth, "store", accounts)
    monkeypatch.setattr(main, "auth_store", accounts)

    shares = CommunityStore(tmp_path / "community.db")
    monkeypatch.setattr(community, "store", shares)
    monkeypatch.setattr(main, "community", shares)

    preferences = PreferenceStore(tmp_path / "memory.db")
    monkeypatch.setattr(memory, "store", preferences)
    monkeypatch.setattr(main, "memory_store", preferences)
    monkeypatch.setattr(memory_tool, "default_store", preferences)
    monkeypatch.setattr(main, "feedback_store", FeedbackStore(tmp_path / "feedback.db"))
