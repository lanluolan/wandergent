"""Durable memory: what the agent knows about a user between trips."""

from app.config import settings
from app.memory.store import Preference, PreferenceStore

#: Process-wide store. Tests build their own against a temp path.
store = PreferenceStore(settings.memory_db_path)

__all__ = ["Preference", "PreferenceStore", "store"]
