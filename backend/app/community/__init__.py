"""Community feed: the process-wide store, mirroring `app.memory`."""

from app.community.store import CommunityStore, PublishRequest, SharedPlan, SharedPlanSummary
from app.config import settings

store = CommunityStore(settings.community_db_path)

__all__ = ["CommunityStore", "PublishRequest", "SharedPlan", "SharedPlanSummary", "store"]
