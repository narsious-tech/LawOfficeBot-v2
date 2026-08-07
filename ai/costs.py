from __future__ import annotations


# Standard paid-tier USD prices per one million tokens.  Unknown models are
# reported without an invented cost until a rate is configured here.
MODEL_PRICES_USD: dict[str, tuple[float, float]] = {
    "gpt-5": (1.25, 10.00),
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = MODEL_PRICES_USD.get(str(model or "").strip().lower())
    if prices is None:
        return None
    input_price, output_price = prices
    return (
        max(0, int(input_tokens or 0)) * input_price
        + max(0, int(output_tokens or 0)) * output_price
    ) / 1_000_000
