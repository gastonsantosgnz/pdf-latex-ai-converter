"""Approximate cost estimation for a conversion run.

This is intentionally simple and **approximate**: a small, configurable
per-model price table plus per-page token assumptions. Prices change often, so
the numbers below are best-effort and the estimate is always presented with a
disclaimer. Override the table without touching the code by pointing the
``PDF2LATEX_PRICES`` environment variable at a JSON file, e.g.::

    { "gpt-4o": [2.5, 10.0], "my-model": [1.0, 3.0] }

where each value is ``[input_usd_per_1M_tokens, output_usd_per_1M_tokens]``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# USD per 1,000,000 tokens, as (input, output). Best-effort snapshot; update via
# PDF2LATEX_PRICES rather than editing here if your account differs.
PRICES_LAST_UPDATED = "2025-06"
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}

# Model used to price an unknown model name (the project's default model).
FALLBACK_MODEL = "gpt-4o"

# Per-page token assumptions: a high-detail page image plus the (long) system
# prompt on input, and a page worth of LaTeX on output. The low/high band varies
# the output side, which is where real usage fluctuates the most.
DEFAULT_INPUT_TOKENS_PER_PAGE = 3000
DEFAULT_OUTPUT_TOKENS_PER_PAGE = 1500
_LOW_OUTPUT_FACTOR = 0.5
_HIGH_OUTPUT_FACTOR = 2.0

DISCLAIMER = (
    "Estimate only and approximate: actual cost depends on page complexity, the "
    "model and current provider prices. Override prices with PDF2LATEX_PRICES."
)


@dataclass(frozen=True)
class CostEstimate:
    """An approximate cost range for converting ``pages`` pages with ``model``."""

    model: str
    pages: int
    input_tokens: int
    output_tokens: int
    usd_low: float
    usd_high: float
    known_model: bool
    price_input_per_1m: float
    price_output_per_1m: float

    @property
    def usd_mid(self) -> float:
        return (self.usd_low + self.usd_high) / 2.0


def load_prices(env: dict[str, str] | None = None) -> dict[str, tuple[float, float]]:
    """Return the price table, merging any ``PDF2LATEX_PRICES`` JSON overrides."""
    env = os.environ if env is None else env
    prices = dict(DEFAULT_PRICES)
    path = env.get("PDF2LATEX_PRICES")
    if not path:
        return prices
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    for model, pair in raw.items():
        prices[model] = (float(pair[0]), float(pair[1]))
    return prices


def cost_for_tokens(model: str, prompt_tokens: int, completion_tokens: int, *, prices=None) -> float:
    """Exact USD cost for a known token count (used for the running tally)."""
    prices = prices or load_prices()
    in_per_1m, out_per_1m = prices.get(model) or prices.get(FALLBACK_MODEL, (0.0, 0.0))
    return prompt_tokens / 1e6 * in_per_1m + completion_tokens / 1e6 * out_per_1m


def estimate_cost(
    model: str,
    pages: int,
    *,
    input_tokens_per_page: int = DEFAULT_INPUT_TOKENS_PER_PAGE,
    output_tokens_per_page: int = DEFAULT_OUTPUT_TOKENS_PER_PAGE,
    prices: dict[str, tuple[float, float]] | None = None,
) -> CostEstimate:
    """Estimate the cost of converting ``pages`` pages with ``model``.

    Unknown models fall back to :data:`FALLBACK_MODEL` pricing and are flagged
    via ``known_model=False`` so the caller can warn.
    """
    prices = prices or load_prices()
    price = prices.get(model)
    known = price is not None
    in_per_1m, out_per_1m = price if known else prices.get(FALLBACK_MODEL, (0.0, 0.0))

    input_tokens = pages * input_tokens_per_page
    output_mid = pages * output_tokens_per_page

    def _usd(output_tokens: float) -> float:
        return input_tokens / 1e6 * in_per_1m + output_tokens / 1e6 * out_per_1m

    return CostEstimate(
        model=model,
        pages=pages,
        input_tokens=input_tokens,
        output_tokens=output_mid,
        usd_low=_usd(output_mid * _LOW_OUTPUT_FACTOR),
        usd_high=_usd(output_mid * _HIGH_OUTPUT_FACTOR),
        known_model=known,
        price_input_per_1m=in_per_1m,
        price_output_per_1m=out_per_1m,
    )
