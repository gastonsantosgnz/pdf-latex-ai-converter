"""Tests for prompt/profile assembly (pure, no network)."""

from __future__ import annotations

import pytest

from pdf2latex.prompts import (
    BASE_SYSTEM_PROMPT,
    DENSE_PROFILE,
    build_system_prompt,
    build_user_text,
)


def test_default_profile_is_base_only() -> None:
    prompt = build_system_prompt("default")
    assert prompt == BASE_SYSTEM_PROMPT
    assert DENSE_PROFILE not in prompt


def test_dense_profile_appends_extra_rules() -> None:
    prompt = build_system_prompt("dense")
    assert prompt.startswith(BASE_SYSTEM_PROMPT)
    assert DENSE_PROFILE in prompt
    assert "photo-omitted" in prompt


def test_unknown_profile_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown profile"):
        build_system_prompt("nope")


def test_build_user_text_varies_by_profile() -> None:
    default_text = build_user_text("default")
    dense_text = build_user_text("dense")
    assert default_text != dense_text
    assert "blank-page" in default_text
    assert "photo" in dense_text.lower()
