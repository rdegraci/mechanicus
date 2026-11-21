"""OpenAI turn and session cost estimates from response usage."""

# USD per 1M tokens: input, cached_input, output (standard short-context list prices).
# Update when OpenAI published prices change.
_MODEL_PRICES = {
    "gpt-6.2-astra": (8.00, 0.40, 40.00),
    "gpt-6.2-sol": (2.00, 0.10, 10.00),
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.2": (1.75, 0.175, 14.00),
    "gpt-5.1": (1.25, 0.125, 10.00),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
}

_DEFAULT_PRICES = _MODEL_PRICES["gpt-6.2-sol"]

_session_total = 0.0
_session_turns = 0


def reset_session_cost():
    """Clear the running session total (call at REPL start)."""
    global _session_total, _session_turns
    _session_total = 0.0
    _session_turns = 0


def session_total():
    return _session_total


def session_turns():
    return _session_turns


def format_dollars(amount):
    """Format a USD amount for operator display."""
    value = float(amount)
    if value == 0:
        return "$0.000"
    if value < 0.001:
        return f"${value:.4f}"
    return f"${value:.3f}"


def format_turn_line(turn_cost, total=None):
    if total is None:
        total = _session_total
    return (
        f"[turn {format_dollars(turn_cost)} · session {format_dollars(total)}]"
    )


def prices_for_model(model):
    """Return (input, cached_input, output) USD per 1M tokens."""
    key = str(model or "").strip()
    if key in _MODEL_PRICES:
        return _MODEL_PRICES[key]
    # Snapshot ids like gpt-6.2-sol-2026-...
    for name, prices in _MODEL_PRICES.items():
        if key.startswith(name):
            return prices
    return _DEFAULT_PRICES


def usage_tokens(response):
    """Pull input/output/cached token counts from a Responses API result.

    Returns None when usage is missing.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
    cached = 0
    details = getattr(usage, "input_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    try:
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        cached = int(cached or 0)
    except (TypeError, ValueError):
        return None
    if cached > input_tokens:
        cached = input_tokens
    return {
        "input": input_tokens,
        "output": output_tokens,
        "cached": cached,
    }


def estimate_cost(model, tokens):
    """USD cost for one turn from token counts and model list prices."""
    if not tokens:
        return 0.0
    input_rate, cached_rate, output_rate = prices_for_model(model)
    cached = tokens.get("cached", 0) or 0
    billed_input = max(0, (tokens.get("input", 0) or 0) - cached)
    return (
        billed_input * input_rate / 1_000_000
        + cached * cached_rate / 1_000_000
        + (tokens.get("output", 0) or 0) * output_rate / 1_000_000
    )


def record_openai_response(model, response, *, print_line=True):
    """Add one OpenAI response to the session total. Return turn cost or None."""
    global _session_total, _session_turns
    tokens = usage_tokens(response)
    if tokens is None:
        return None
    turn = estimate_cost(model, tokens)
    _session_total += turn
    _session_turns += 1
    if print_line:
        print(format_turn_line(turn, _session_total))
    return turn


def print_cost_status():
    """Print `/cost` output."""
    print(f"Session estimate {format_dollars(_session_total)}")
    print(f"{_session_turns} OpenAI turns")


def print_session_estimate():
    """Print the quit-time session line."""
    print(f"Session estimate {format_dollars(_session_total)}")
