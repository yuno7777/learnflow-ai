"""Token pricing catalog and USD cost estimation formulas.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file maintains the pricing lookup table (`PRICES`) mapping model names to
their USD cost per 1,000,000 prompt and completion tokens.
It provides:
1. `estimate_cost(model, input_tokens, output_tokens)`: Calculates the exact
   estimated dollar cost with high-precision `Decimal` math.
2. `is_priced(model)`: Checks if a model exists in the catalog.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `ai/gateway.py`: Calls `estimate_cost()` after every model completion and embedding
  to populate `llm_calls.estimated_cost_usd` and `usage_records.estimated_cost_usd`.
- `routers/analytics.py`: Aggregates estimated USD costs across organizations.
"""

from __future__ import annotations

from decimal import Decimal

# Static pricing dictionary: (input_price_per_1M, output_price_per_1M) in USD
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "text-embedding-3-small": (Decimal("0.02"), Decimal("0")),
    "text-embedding-3-large": (Decimal("0.13"), Decimal("0")),
    # Local & Mock models have $0 marginal API costs
    "llama3.1": (Decimal("0"), Decimal("0")),
    "nomic-embed-text": (Decimal("0"), Decimal("0")),
    "mock": (Decimal("0"), Decimal("0")),
    "mock-embed": (Decimal("0"), Decimal("0")),
}

# Conservative default estimate for custom or unlisted model identifiers
_DEFAULT = (Decimal("1.00"), Decimal("3.00"))


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Calculates estimated USD cost for a model call.

    Formula:
        Cost = ((input_tokens * price_in) + (output_tokens * price_out)) / 1,000,000

    Quantized to 6 decimal places (e.g. $0.000125).
    """
    price_in, price_out = PRICES.get(model, _DEFAULT)
    million = Decimal(1_000_000)
    cost = (Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out) / million
    return cost.quantize(Decimal("0.000001"))


def is_priced(model: str) -> bool:
    """Returns True if the model has an explicit entry in the pricing table."""
    return model in PRICES
