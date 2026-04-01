from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from copy import copy
from functools import wraps
from typing import Any, Literal, cast

from opentelemetry.trace import get_current_span
from pydantic_ai.usage import RunUsage

from ..errors import NighthawkError
from ..runtime.scoping import get_current_usage_meter

_logger = logging.getLogger("nighthawk.resilience")

type BudgetLimitKind = Literal["tokens", "tokens_per_call", "cost", "cost_per_call"]

type CostFunction = Callable[[RunUsage], float]
type EstimateUsageFunction = Callable[..., RunUsage]


class BudgetExceededError(NighthawkError):
    """Raised when LLM token usage exceeds a configured budget."""

    def __init__(
        self,
        accumulated_usage: RunUsage,
        call_usage: RunUsage,
        limit_kind: BudgetLimitKind,
        limit_value: int | float,
    ) -> None:
        self.accumulated_usage = accumulated_usage
        self.call_usage = call_usage
        self.limit_kind = limit_kind
        self.limit_value = limit_value
        super().__init__(
            f"Budget exceeded: {limit_kind} limit {limit_value} "
            f"(accumulated {accumulated_usage.total_tokens} tokens, "
            f"call used {call_usage.total_tokens} tokens)"
        )


def _get_usage_snapshot() -> RunUsage | None:
    usage_meter = get_current_usage_meter()
    if usage_meter is None:
        return None
    return usage_meter.snapshot()


def _get_remaining_token_count(*, snapshot: RunUsage, tokens: int | None) -> int | None:
    if tokens is None:
        return None
    return tokens - snapshot.total_tokens


def _get_remaining_cost(*, snapshot: RunUsage, cost: float | None, cost_function: CostFunction | None) -> float | None:
    if cost is None or cost_function is None:
        return None
    return cost - cost_function(snapshot)


def _build_predicted_accumulated_usage(*, snapshot: RunUsage, call_usage: RunUsage) -> RunUsage:
    predicted_accumulated_usage = copy(snapshot)
    predicted_accumulated_usage.incr(call_usage)
    return predicted_accumulated_usage


def _raise_budget_exceeded(
    *,
    accumulated_usage: RunUsage,
    call_usage: RunUsage,
    limit_kind: BudgetLimitKind,
    limit_value: int | float,
) -> None:
    current_span = get_current_span()
    budget_exceeded_attribute_name_to_value: dict[str, str | int | float | bool] = {
        **accumulated_usage.opentelemetry_attributes(),
        "nighthawk.resilience.budget.limit_kind": limit_kind,
        "nighthawk.resilience.budget.limit_value": float(limit_value),
        "nighthawk.resilience.budget.call_input_tokens": call_usage.input_tokens,
        "nighthawk.resilience.budget.call_output_tokens": call_usage.output_tokens,
        "nighthawk.resilience.budget.call_total_tokens": call_usage.total_tokens,
    }
    current_span.add_event(
        "nighthawk.resilience.budget.exceeded",
        budget_exceeded_attribute_name_to_value,
    )
    error = BudgetExceededError(
        accumulated_usage=accumulated_usage,
        call_usage=call_usage,
        limit_kind=limit_kind,
        limit_value=limit_value,
    )
    _logger.warning("%s", error)
    raise error


def _compute_call_usage(before: RunUsage, after: RunUsage) -> RunUsage:
    return RunUsage(
        input_tokens=after.input_tokens - before.input_tokens,
        output_tokens=after.output_tokens - before.output_tokens,
        cache_read_tokens=after.cache_read_tokens - before.cache_read_tokens,
        cache_write_tokens=after.cache_write_tokens - before.cache_write_tokens,
    )


def _check_budget(
    *,
    after: RunUsage,
    call_usage: RunUsage,
    tokens: int | None,
    tokens_per_call: int | None,
    cost: float | None,
    cost_per_call: float | None,
    cost_function: CostFunction | None,
) -> None:
    if tokens_per_call is not None and call_usage.total_tokens > tokens_per_call:
        _raise_budget_exceeded(
            accumulated_usage=after,
            call_usage=call_usage,
            limit_kind="tokens_per_call",
            limit_value=tokens_per_call,
        )
    if tokens is not None and after.total_tokens > tokens:
        _raise_budget_exceeded(
            accumulated_usage=after,
            call_usage=call_usage,
            limit_kind="tokens",
            limit_value=tokens,
        )
    if cost_function is not None:
        if cost_per_call is not None and cost_function(call_usage) > cost_per_call:
            _raise_budget_exceeded(
                accumulated_usage=after,
                call_usage=call_usage,
                limit_kind="cost_per_call",
                limit_value=cost_per_call,
            )
        if cost is not None and cost_function(after) > cost:
            _raise_budget_exceeded(
                accumulated_usage=after,
                call_usage=call_usage,
                limit_kind="cost",
                limit_value=cost,
            )


def _pre_check(
    *,
    snapshot: RunUsage,
    tokens: int | None,
    cost: float | None,
    cost_function: CostFunction | None,
) -> None:
    if tokens is not None and snapshot.total_tokens >= tokens:
        _raise_budget_exceeded(
            accumulated_usage=snapshot,
            call_usage=RunUsage(),
            limit_kind="tokens",
            limit_value=tokens,
        )
    if cost_function is not None and cost is not None and cost_function(snapshot) >= cost:
        _raise_budget_exceeded(
            accumulated_usage=snapshot,
            call_usage=RunUsage(),
            limit_kind="cost",
            limit_value=cost,
        )


def _pre_check_with_estimate(
    *,
    snapshot: RunUsage,
    estimated_call_usage: RunUsage,
    tokens: int | None,
    tokens_per_call: int | None,
    cost: float | None,
    cost_per_call: float | None,
    cost_function: CostFunction | None,
) -> None:
    predicted_accumulated_usage = _build_predicted_accumulated_usage(
        snapshot=snapshot,
        call_usage=estimated_call_usage,
    )

    if tokens_per_call is not None and estimated_call_usage.total_tokens > tokens_per_call:
        _raise_budget_exceeded(
            accumulated_usage=predicted_accumulated_usage,
            call_usage=estimated_call_usage,
            limit_kind="tokens_per_call",
            limit_value=tokens_per_call,
        )

    remaining_token_count = _get_remaining_token_count(snapshot=snapshot, tokens=tokens)
    if remaining_token_count is not None and estimated_call_usage.total_tokens > remaining_token_count:
        assert tokens is not None
        _raise_budget_exceeded(
            accumulated_usage=predicted_accumulated_usage,
            call_usage=estimated_call_usage,
            limit_kind="tokens",
            limit_value=tokens,
        )

    if cost_function is None:
        return

    estimated_call_cost = cost_function(estimated_call_usage)
    if cost_per_call is not None and estimated_call_cost > cost_per_call:
        _raise_budget_exceeded(
            accumulated_usage=predicted_accumulated_usage,
            call_usage=estimated_call_usage,
            limit_kind="cost_per_call",
            limit_value=cost_per_call,
        )

    remaining_cost = _get_remaining_cost(snapshot=snapshot, cost=cost, cost_function=cost_function)
    if remaining_cost is not None and estimated_call_cost > remaining_cost:
        assert cost is not None
        _raise_budget_exceeded(
            accumulated_usage=predicted_accumulated_usage,
            call_usage=estimated_call_usage,
            limit_kind="cost",
            limit_value=cost,
        )


def _wrap_with_budget[**P, R](
    function: Callable[P, R],
    *,
    tokens: int | None,
    tokens_per_call: int | None,
    cost: float | None,
    cost_per_call: float | None,
    cost_function: CostFunction | None,
    estimate_usage: EstimateUsageFunction | None,
) -> Callable[P, R]:
    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            snapshot = _get_usage_snapshot()
            if snapshot is None:
                return await function(*args, **kwargs)

            _pre_check(
                snapshot=snapshot,
                tokens=tokens,
                cost=cost,
                cost_function=cost_function,
            )

            if estimate_usage is not None:
                estimated_call_usage = estimate_usage(args, kwargs)
                _pre_check_with_estimate(
                    snapshot=snapshot,
                    estimated_call_usage=estimated_call_usage,
                    tokens=tokens,
                    tokens_per_call=tokens_per_call,
                    cost=cost,
                    cost_per_call=cost_per_call,
                    cost_function=cost_function,
                )

            before = snapshot
            result = await function(*args, **kwargs)
            after = _get_usage_snapshot()
            if after is None:
                return result
            call_usage = _compute_call_usage(before, after)
            _check_budget(
                after=after,
                call_usage=call_usage,
                tokens=tokens,
                tokens_per_call=tokens_per_call,
                cost=cost,
                cost_per_call=cost_per_call,
                cost_function=cost_function,
            )
            return result

        return cast(Callable[P, R], async_wrapper)

    @wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        snapshot = _get_usage_snapshot()
        if snapshot is None:
            return function(*args, **kwargs)

        _pre_check(
            snapshot=snapshot,
            tokens=tokens,
            cost=cost,
            cost_function=cost_function,
        )

        if estimate_usage is not None:
            estimated_call_usage = estimate_usage(args, kwargs)
            _pre_check_with_estimate(
                snapshot=snapshot,
                estimated_call_usage=estimated_call_usage,
                tokens=tokens,
                tokens_per_call=tokens_per_call,
                cost=cost,
                cost_per_call=cost_per_call,
                cost_function=cost_function,
            )

        before = snapshot
        result = function(*args, **kwargs)
        after = _get_usage_snapshot()
        if after is None:
            return result
        call_usage = _compute_call_usage(before, after)
        _check_budget(
            after=after,
            call_usage=call_usage,
            tokens=tokens,
            tokens_per_call=tokens_per_call,
            cost=cost,
            cost_per_call=cost_per_call,
            cost_function=cost_function,
        )
        return result

    return cast(Callable[P, R], sync_wrapper)


class _BudgetHandle:
    """Budget handle usable as a decorator factory.

    Decorator form (sync and async)::

        budgeted_function = budget(tokens=10000)(my_function)
    """

    def __init__(
        self,
        *,
        tokens: int | None,
        tokens_per_call: int | None,
        cost: float | None,
        cost_per_call: float | None,
        cost_function: CostFunction | None,
        estimate_usage: EstimateUsageFunction | None,
    ) -> None:
        self._tokens = tokens
        self._tokens_per_call = tokens_per_call
        self._cost = cost
        self._cost_per_call = cost_per_call
        self._cost_function = cost_function
        self._estimate_usage = estimate_usage

    def __call__[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        """Wrap *function* with budget enforcement."""
        return _wrap_with_budget(
            function,
            tokens=self._tokens,
            tokens_per_call=self._tokens_per_call,
            cost=self._cost,
            cost_per_call=self._cost_per_call,
            cost_function=self._cost_function,
            estimate_usage=self._estimate_usage,
        )


def budget(
    *,
    tokens: int | None = None,
    tokens_per_call: int | None = None,
    cost: float | None = None,
    cost_per_call: float | None = None,
    cost_function: CostFunction | None = None,
    estimate_usage: EstimateUsageFunction | None = None,
) -> _BudgetHandle:
    """Create a budget enforcement transformer.

    Enforces token usage limits on wrapped functions. Requires an active :func:`~nighthawk.run` context with a :class:`~nighthawk.UsageMeter`. Outside a run context the transformer is a no-op.

    Recommended composition order::

        timeout -> budget -> vote -> retrying -> circuit_breaker -> fallback

    Args:
        tokens: Maximum cumulative tokens across all calls. Checked before and after each call.
        tokens_per_call: Maximum tokens for a single call. Checked after each call completes.
        cost: Maximum cumulative monetary cost. Requires *cost_function*.
        cost_per_call: Maximum monetary cost for a single call. Requires *cost_function*.
        cost_function: Callable that converts :class:`RunUsage` to a monetary cost (float). Required when *cost* or *cost_per_call* is set.
        estimate_usage: Optional callable that estimates the next call usage from positional/keyword arguments. When provided, over-limit calls fail fast before execution.

    Returns:
        A handle that wraps a function with budget enforcement.

    Raises:
        ValueError: If no limit is specified, or if *cost*/*cost_per_call* is set without *cost_function*.

    Example::

        from nighthawk.resilience import budget

        safe_classify = budget(tokens=50_000)(classify)
        result = safe_classify(text)
    """
    has_token_limit = tokens is not None or tokens_per_call is not None
    has_cost_limit = cost is not None or cost_per_call is not None
    if not has_token_limit and not has_cost_limit:
        raise ValueError("budget() requires at least one of: tokens, tokens_per_call, cost, cost_per_call")
    if has_cost_limit and cost_function is None:
        raise ValueError("budget() requires cost_function when cost or cost_per_call is set")
    return _BudgetHandle(
        tokens=tokens,
        tokens_per_call=tokens_per_call,
        cost=cost,
        cost_per_call=cost_per_call,
        cost_function=cost_function,
        estimate_usage=estimate_usage,
    )
