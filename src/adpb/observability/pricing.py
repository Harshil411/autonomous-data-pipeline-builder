"""Token usage and dollar cost of a model call (requirement 22: "number of tokens used and
cost should be logged per run").

Prices are US dollars per million tokens, first-party Claude API list prices as of
November 2026. VT ARC and the offline scripted model are free. An *unknown* paid model is
priced at the most expensive known rate rather than as free, so a typo in a model id can
never make the cost budget silently stop working.
"""

from __future__ import annotations

from dataclasses import dataclass

# model id -> (input, output, cache read) in $ per 1M tokens
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
FREE_MODELS = {"vt-arc-llm", "scripted", "replay"}
# Cache writes cost 1.25x the input rate (5-minute cache).
CACHE_WRITE_MULTIPLIER = 1.25
_FALLBACK = max(PRICES.values())


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (self.input_tokens + self.output_tokens + self.cache_read_input_tokens
                + self.cache_creation_input_tokens)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


def cost_usd(model: str, usage: Usage) -> float:
    """Dollar cost of one call. Free models return 0; unknown paid models are over-priced."""
    if model in FREE_MODELS or model.startswith("vt-arc") or not model.startswith("claude"):
        # Non-Claude ids here are the free VT ARC models (e.g. gpt-oss-120b on ARC).
        return 0.0
    price_in, price_out, price_cache = PRICES.get(model, _FALLBACK)
    return (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + usage.cache_read_input_tokens * price_cache
        + usage.cache_creation_input_tokens * price_in * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
