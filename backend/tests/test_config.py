"""Config that has to be settled at boot, not on the first request."""

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_BASE_URL, DEFAULT_MODEL, Settings


def test_a_blank_value_means_unset_not_empty() -> None:
    """`pydantic-settings` would let the empty string win over the default.

    `OPENAI_BASE_URL=` in .env, or a repository variable that CI passes through unset,
    would otherwise leave the field blank and send every request to whatever the SDK
    falls back to.
    """
    assert Settings(openai_base_url="").openai_base_url == DEFAULT_BASE_URL
    assert Settings(openai_base_url="   ").openai_base_url == DEFAULT_BASE_URL
    assert Settings(openai_model="").openai_model == DEFAULT_MODEL


def test_an_endpoint_without_a_scheme_is_refused() -> None:
    with pytest.raises(ValidationError, match="http"):
        Settings(openai_base_url="api.openai.com/v1")


def test_the_trailing_slash_is_normalised() -> None:
    # So the value has one form in logs and in comparisons.
    assert Settings(openai_base_url="https://api.openai.com/v1/").openai_base_url == (
        "https://api.openai.com/v1"
    )
