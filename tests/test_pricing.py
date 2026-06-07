"""Tests for the approximate cost estimator (pure, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2latex.pricing import cost_for_tokens, estimate_cost, load_prices


def test_estimate_known_model_is_deterministic() -> None:
    est = estimate_cost("gpt-4o-mini", 10)
    assert est.known_model is True
    assert est.pages == 10
    assert est.input_tokens == 30_000  # 10 pages * 3000
    assert est.output_tokens == 15_000  # 10 pages * 1500
    # input 30k @ $0.15/1M = 0.0045; output low 7.5k / high 30k @ $0.60/1M
    assert est.usd_low == pytest.approx(0.009)
    assert est.usd_high == pytest.approx(0.0225)
    assert est.usd_low < est.usd_mid < est.usd_high


def test_estimate_unknown_model_falls_back() -> None:
    est = estimate_cost("totally-made-up-model", 10)
    assert est.known_model is False
    assert est.usd_high > est.usd_low > 0  # priced via the fallback model


def test_estimate_with_explicit_prices() -> None:
    est = estimate_cost("custom", 5, prices={"custom": (1.0, 2.0)})
    assert est.known_model is True
    # input 15k @ $1/1M = 0.015; output low 3.75k / high 15k @ $2/1M
    assert est.usd_low == pytest.approx(15_000 / 1e6 * 1.0 + 3_750 / 1e6 * 2.0)
    assert est.usd_high == pytest.approx(15_000 / 1e6 * 1.0 + 15_000 / 1e6 * 2.0)


def test_load_prices_defaults_without_env() -> None:
    prices = load_prices(env={})
    assert prices["gpt-4o"] == (2.50, 10.00)
    assert "gpt-4o-mini" in prices


def test_load_prices_merges_env_override(tmp_path: Path) -> None:
    f = tmp_path / "prices.json"
    f.write_text(json.dumps({"custom": [1.0, 2.0], "gpt-4o": [9.0, 9.0]}), encoding="utf-8")

    prices = load_prices(env={"PDF2LATEX_PRICES": str(f)})

    assert prices["custom"] == (1.0, 2.0)  # new entry added
    assert prices["gpt-4o"] == (9.0, 9.0)  # existing entry overridden
    assert "gpt-4o-mini" in prices  # untouched defaults preserved


def test_cost_for_tokens_is_exact() -> None:
    # 1M prompt @ $0.15 + 1M completion @ $0.60 = $0.75
    assert cost_for_tokens("gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(0.75)
