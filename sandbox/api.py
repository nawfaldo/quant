"""Small public API for notebooks, scripts, and tests."""

from __future__ import annotations

from typing import Any


def list_strategies() -> tuple[str, ...]:
    """Return registered strategy names in display order."""
    from sandbox import strategies

    return tuple(sorted(strategies.REGISTRY))


def get_strategy(name: str):
    """Return a registered strategy by its human-readable name."""
    from sandbox import strategies

    return strategies.get(name)


def _strategy(value):
    return get_strategy(value) if isinstance(value, str) else value


def backtest(strategy, params: dict[str, Any] | None = None, **kwargs):
    """Backtest a strategy name or instance.

    Returns ``(statistics, trades)``. ``kwargs`` are forwarded to the reusable
    search engine, which makes it easy to supply in-memory bars in tests.
    """
    from sandbox import search

    return search.backtest(_strategy(strategy), params=params, **kwargs)


def sweep(strategy, overrides: dict[str, Any] | None = None, **kwargs):
    """Sweep a strategy name or instance without mutating its registration."""
    from sandbox import search

    configured = _strategy(strategy).configured(overrides)
    return search.sweep(configured, **kwargs)
