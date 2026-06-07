"""Tests for prompt assembly (pure, no network)."""

from __future__ import annotations

from pdf2latex.prompts import (
    BASE_SYSTEM_PROMPT,
    DENSE_RULES,
    build_system_prompt,
    build_user_text,
)


def test_system_prompt_is_base_plus_dense_rules() -> None:
    prompt = build_system_prompt()
    assert prompt.startswith(BASE_SYSTEM_PROMPT)
    assert DENSE_RULES in prompt
    # The robust conversion always handles photos and figures.
    assert "photo-omitted" in prompt


def test_base_prompt_has_compile_safety_rules() -> None:
    # The rules that prevent the most common pdflatex failures must be present.
    assert "MUST COMPILE" in BASE_SYSTEM_PROMPT
    assert "Missing $" in BASE_SYSTEM_PROMPT
    assert "Extra alignment tab" in BASE_SYSTEM_PROMPT


def test_base_prompt_guards_against_page_overflow() -> None:
    # The layout rule that stops a whole page being trapped in one unbreakable box.
    assert "Page layout" in BASE_SYSTEM_PROMPT
    assert "Overfull" in BASE_SYSTEM_PROMPT
    assert "minipage" in BASE_SYSTEM_PROMPT


def test_user_text_is_single_and_robust() -> None:
    text = build_user_text()
    assert "blank-page" in text
    assert "photo" in text.lower()
