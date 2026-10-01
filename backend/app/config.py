"""App config. All secrets / external service URLs come from env vars or .env."""

from math import isfinite
from urllib.parse import urlsplit

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# What this project actually runs on. Named so the blank-means-unset validator can hand
# back the same value the field defaults to.
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6-luna"


class Settings(BaseSettings):
    """Read config from env vars / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Wandergent"
    debug: bool = False

    # Optional OTLP JSON collector endpoint, including /v1/traces. No content export.
    otel_exporter_otlp_traces_endpoint: str = ""
    trace_directory: str = ""
    # Per-model [uncached input, cached input, output] USD per million tokens.
    trace_model_prices: dict[str, list[float]] = {}

    @field_validator("otel_exporter_otlp_traces_endpoint")
    @classmethod
    def _valid_trace_endpoint(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OTLP endpoint must be an HTTP URL without credentials or query")
        return value

    @field_validator("trace_model_prices")
    @classmethod
    def _valid_trace_prices(cls, value: dict[str, list[float]]) -> dict[str, list[float]]:
        if any(
            len(prices) != 3 or any(p < 0 or not isfinite(p) for p in prices)
            for prices in value.values()
        ):
            raise ValueError("TRACE_MODEL_PRICES needs three non-negative rates per model")
        return value

    # LLM, on an OpenAI-compatible endpoint. Defaults name what we actually run against,
    # never the SDK's factory values. The key has no default -- a secret is supplied or
    # missing, never guessed. The account is the operator's; callers cannot send one of
    # their own (docs/decisions.md).
    openai_api_key: str = ""
    openai_base_url: str = DEFAULT_BASE_URL
    openai_model: str = DEFAULT_MODEL

    @field_validator("openai_base_url", "openai_model", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: str, info: ValidationInfo) -> str:
        """Empty means absent. `pydantic-settings` would let `OPENAI_BASE_URL=` override
        the default and send every request to the SDK's fallback host."""
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("openai_base_url")
    @classmethod
    def _endpoint_must_be_an_endpoint(cls, value: str) -> str:
        """A missing scheme is a typo, caught at boot rather than as a per-request
        connection error naming neither the setting nor the cause. Trailing slash is
        normalised so the value has one form in logs."""
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError(
                f"OPENAI_BASE_URL must start with http:// or https://, got {cleaned!r}"
            )
        return cleaned

    # Model routing: the first turn is forced to emit a tool call, so it never writes the
    # itinerary and can run cheaper. Empty disables routing -- the safe default, since a
    # model name is endpoint-specific and a wrong one fails every request.
    fast_model: str = ""

    # Maps, server-side only: the app never sees this key, it receives rendered images.
    # Empty disables the maps tools rather than failing the run.
    google_maps_api_key: str = ""

    # The interactive map page runs the Maps JavaScript API in the user's WebView, so its
    # key reaches the device. Restrict this one to Maps JavaScript API alone, so a leak
    # cannot be spent on Places or Routes. Empty falls back to the server key with a
    # per-request warning; empty and no server key leaves the client the static map.
    google_maps_browser_key: str = ""

    @property
    def maps_browser_key(self) -> str:
        """The key the map page ships with, preferring the restricted one."""
        return self.google_maps_browser_key or self.google_maps_api_key

    # Hard rule: every external call gets a timeout, so these are not optional.
    tool_timeout_seconds: float = 8.0
    llm_timeout_seconds: float = 60.0

    # Room for one reply. The endpoint's own default (4k) truncates a multi-day itinerary
    # mid-JSON, which reads downstream as a syntax error.
    llm_max_output_tokens: int = 16384

    # Bound on the function-calling loop, so a confused model cannot spin forever.
    max_tool_rounds: int = 4

    # One file per store, so each can move to Postgres on its own schedule -- and the one
    # holding credentials can be backed up differently.
    memory_db_path: str = "wandergent-memory.db"
    community_db_path: str = "wandergent-community.db"
    auth_db_path: str = "wandergent-auth.db"
    feedback_db_path: str = "wandergent-feedback.db"

    # The only ceiling on the daily bill, counted in planning runs because they are the
    # one thing here that costs money. A setting, not a constant: only the person paying
    # knows the number.
    max_plans_per_day: int = 200

    # Mail, for password reset. Empty host means not configured: the reset code goes to
    # the log instead of an inbox, which works on a laptop and is announced loudly at
    # boot. No default for the password, same rule as every other secret.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Shown in the reset email so a recipient who did not ask for one knows what to
    # ignore.
    public_name: str = "Wandergent"


settings = Settings()
