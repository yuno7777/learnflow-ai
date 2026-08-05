"""USD per 1M tokens. Used for cost estimation only — never for billing truth."""

from __future__ import annotations

from decimal import Decimal

# (input, output) per 1,000,000 tokens.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "text-embedding-3-small": (Decimal("0.02"), Decimal("0")),
    "text-embedding-3-large": (Decimal("0.13"), Decimal("0")),
    # Self-hosted: no marginal API cost, but keep the row so reports show 0 not "unknown".
    "llama3.1": (Decimal("0"), Decimal("0")),
    "nomic-embed-text": (Decimal("0"), Decimal("0")),
    "mock": (Decimal("0"), Decimal("0")),
    "mock-embed": (Decimal("0"), Decimal("0")),
}

_DEFAULT = (Decimal("1.00"), Decimal("3.00"))  # conservative guess for unlisted models


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = PRICES.get(model, _DEFAULT)
    million = Decimal(1_000_000)
    cost = (Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out) / million
    return cost.quantize(Decimal("0.000001"))


def is_priced(model: str) -> bool:
    return model in PRICES
