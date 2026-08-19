"""App config. All secrets / external service URLs come from env vars or .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read config from env vars / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Wandergent"
    debug: bool = False

    # LLM. The endpoint is OpenAI-compatible; these defaults are what the project
    # actually runs against, not the SDK's factory values -- a default that names a
    # provider we do not use is a lie the next reader has to discover the hard way.
    # No default for the key: a secret must be supplied, never silently defaulted.
    openai_api_key: str = ""
    openai_base_url: str = "https://api.xiaomimimo.com/v1"
    openai_model: str = "mimo-v2.5-pro"

    # Model routing. The first turn of a run only has to read the request and choose
    # tool arguments -- it never writes the itinerary, because that turn is forced to
    # emit a tool call. So it can run on a cheaper model.
    #
    # Empty disables routing, which is the safe default: the model name is
    # endpoint-specific, and a wrong one would fail every request. Set it in .env.
    fast_model: str = ""

    # Maps. Server-side only -- the app never sees this key, it receives rendered map
    # images from us. Empty disables the maps tools rather than failing: the weather
    # tool and the rest of the plan still work without them.
    google_maps_api_key: str = ""

    # A second key, for the one thing that cannot stay server-side: the interactive map
    # page runs the Maps JavaScript API in the user's WebView, so whatever key it uses
    # is delivered to the device. Restrict this one to Maps JavaScript API alone, so a
    # leak cannot be spent on Places or Routes -- the expensive SKUs.
    #
    # Empty falls back to the server key and logs a warning on every request: fine for a
    # dev device, not fine in production. Empty *and* no server key disables the
    # interactive map, and the client keeps the static one.
    google_maps_browser_key: str = ""

    @property
    def maps_browser_key(self) -> str:
        """The key the map page ships with, preferring the restricted one."""
        return self.google_maps_browser_key or self.google_maps_api_key

    # Hard rule: every external call gets a timeout, so these are not optional.
    tool_timeout_seconds: float = 8.0
    llm_timeout_seconds: float = 60.0

    # Room for one reply. The endpoint's own default (4k) truncates a multi-day
    # itinerary mid-JSON, which reads downstream as a syntax error. Raise this before
    # blaming the model for malformed output on long trips.
    llm_max_output_tokens: int = 16384

    # Bound on the function-calling loop, so a confused model cannot spin forever.
    max_tool_rounds: int = 4

    # User memory. SQLite for now; Phase 4 moves it into PostgreSQL behind the same
    # PreferenceStore interface.
    memory_db_path: str = "wandergent-memory.db"
    # Shared itineraries. Its own file rather than a second schema inside the
    # memory database: each store owns its file, so either can move to Postgres
    # on its own schedule.
    community_db_path: str = "wandergent-community.db"
    # Accounts and live sessions. Separate again: this is the one store whose
    # contents are credentials, and keeping it in its own file makes "back this up
    # differently" and "move this first" possible without touching the others.
    auth_db_path: str = "wandergent-auth.db"

    # The ceiling on what this service may spend in a day, counted in planning
    # runs because that is the only thing here that costs money -- several LLM
    # calls plus Places and Routes quota each.
    #
    # A *setting* rather than a constant because it is a budget, and only the
    # person paying knows the number. The default is deliberately small: the
    # failure mode of too low is a refused request, and of too high is a bill.
    max_plans_per_day: int = 200

    # Mail, for password reset. Empty host means "not configured", and the reset
    # code is written to the log instead of sent -- which makes the flow work on a
    # laptop and is announced loudly at boot, because a server that believes it is
    # emailing people while printing their codes to stdout is a security problem.
    #
    # No default for the password, same rule as every other secret.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Shown in the reset email so the recipient can tell which service sent it.
    # Someone who did not request a reset needs to know what to ignore.
    public_name: str = "Wandergent"

    # Filled in later stages (placeholders, not required now).
    database_url: str = ""
    redis_url: str = ""


settings = Settings()
