"""Composable function transformers for production resilience.

Each transformer takes a callable and returns a new callable with the same
signature.  Transformers auto-detect sync/async and compose by nesting
(innermost executes first).  Recommended order: ``timeout`` → ``budget`` → ``vote`` → ``retrying`` → ``circuit_breaker`` → ``fallback``.

Import directly from this module::

    from nighthawk.resilience import retrying, fallback, vote, timeout, budget, circuit_breaker

These primitives are **not** re-exported from the top-level ``nighthawk``
namespace.  See [Patterns: Resilience patterns](https://kurusugawa-computer.github.io/nighthawk-python/patterns/#resilience-patterns)
for usage patterns and composition examples.
"""

from __future__ import annotations

from ._budget import BudgetExceededError, BudgetLimitKind, CostFunction, budget
from ._circuit_breaker import CircuitOpenError, CircuitState, circuit_breaker
from ._fallback import fallback
from ._retry import retrying
from ._timeout import timeout
from ._vote import plurality, vote

__all__ = [
    "BudgetExceededError",
    "BudgetLimitKind",
    "CircuitOpenError",
    "CircuitState",
    "CostFunction",
    "budget",
    "circuit_breaker",
    "fallback",
    "plurality",
    "retrying",
    "timeout",
    "vote",
]
