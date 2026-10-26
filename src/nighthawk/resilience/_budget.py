from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Literal, cast

from opentelemetry.trace import get_current_span
from pydantic_ai.usage import RunUsage

from ..errors import NighthawkError
from ..runtime.scoping import _optional_usage_meter

_logger = logging.getLogger("nighthawk.resilience")

type BudgetLimitKind = Literal["tokens", "tokens_per_call", "cost", "cost_per_call"]

type CostFunction = Callable[[RunUsage], float]
type EstimateUsageFunction = Callable[[tuple[Any, ...], dict[str, Any]], RunUsage]


class BudgetExceededError(NighthawkError):
    """Raised when recorded or estimated usage exceeds a token or cost budget."""

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


@dataclass(frozen=True)
class _BudgetHandle:
    _tokens: int | None
    _tokens_per_call: int | None
    _cost: float | None
    _cost_per_call: float | None
    _cost_function: CostFunction | None
    _estimate_usage: EstimateUsageFunction | None

    def _check_budget(self, accumulated_usage: RunUsage, call_usage: RunUsage) -> None:
        if self._tokens_per_call is not None and call_usage.total_tokens > self._tokens_per_call:
            _raise_budget_exceeded(
                accumulated_usage=accumulated_usage,
                call_usage=call_usage,
                limit_kind="tokens_per_call",
                limit_value=self._tokens_per_call,
            )
        if self._tokens is not None and accumulated_usage.total_tokens > self._tokens:
            _raise_budget_exceeded(
                accumulated_usage=accumulated_usage,
                call_usage=call_usage,
                limit_kind="tokens",
                limit_value=self._tokens,
            )
        if self._cost_function is not None:
            if self._cost_per_call is not None and self._cost_function(call_usage) > self._cost_per_call:
                _raise_budget_exceeded(
                    accumulated_usage=accumulated_usage,
                    call_usage=call_usage,
                    limit_kind="cost_per_call",
                    limit_value=self._cost_per_call,
                )
            if self._cost is not None and self._cost_function(accumulated_usage) > self._cost:
                _raise_budget_exceeded(
                    accumulated_usage=accumulated_usage,
                    call_usage=call_usage,
                    limit_kind="cost",
                    limit_value=self._cost,
                )

    @contextmanager
    def _check_call(self, arguments: tuple[Any, ...], keyword_name_to_value: dict[str, Any]) -> Iterator[None]:
        meter = _optional_usage_meter()
        if meter is None:
            yield
            return

        before = meter.snapshot()
        if self._tokens is not None and before.total_tokens >= self._tokens:
            _raise_budget_exceeded(
                accumulated_usage=before,
                call_usage=RunUsage(),
                limit_kind="tokens",
                limit_value=self._tokens,
            )
        if self._cost_function is not None and self._cost is not None and self._cost_function(before) >= self._cost:
            _raise_budget_exceeded(
                accumulated_usage=before,
                call_usage=RunUsage(),
                limit_kind="cost",
                limit_value=self._cost,
            )
        if self._estimate_usage is not None:
            estimated_call_usage = self._estimate_usage(arguments, keyword_name_to_value)
            self._check_budget(before + estimated_call_usage, estimated_call_usage)

        yield

        after = meter.snapshot()
        self._check_budget(after, after - before)

    def __call__[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self._check_call(args, kwargs):
                    return await function(*args, **kwargs)

            return cast(Callable[P, R], async_wrapper)

        @wraps(function)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with self._check_call(args, kwargs):
                return function(*args, **kwargs)

        return sync_wrapper


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

    Checks recorded usage around function calls using an active :func:`~nighthawk.run` context with a :class:`~nighthawk.UsageMeter`.
    Outside a run context the transformer is a no-op.
    Post-call checks run only after normal return; agent runs that fail before metering may consume unrecorded usage.
    Checks use the meter active at invocation, including all usage fields in its per-call delta.
    Concurrent calls sharing that meter can contribute to the same measured delta.
    This transformer does not gate requests within an unfinished block or guarantee a strict spending cap.
    Use a separate host request capability for request-level admission without adding the meter total as another charge.

    Args:
        tokens: Cumulative recorded-token threshold. Checked before invocation and after normal return.
        tokens_per_call: Recorded-token delta threshold. Checked after normal return.
        cost: Cumulative recorded-cost threshold. Requires *cost_function*.
        cost_per_call: Recorded-cost delta threshold. Requires *cost_function*.
        cost_function: Callable that converts :class:`RunUsage` to a monetary cost (float). Required when *cost* or *cost_per_call* is set.
        estimate_usage: Optional callable receiving the positional-argument tuple and keyword-argument dictionary.
            Returns predicted usage for the next call; cumulative checks apply to the current usage plus that estimate.
            Rejects predicted over-limit work before execution, without reserving usage or guaranteeing actual cost.

    Returns:
        A handle that wraps a function with budget enforcement.

    Raises:
        ValueError: If no limit is specified, or if *cost*/*cost_per_call* is set without *cost_function*.
    """
    has_token_limit = tokens is not None or tokens_per_call is not None
    has_cost_limit = cost is not None or cost_per_call is not None
    if not has_token_limit and not has_cost_limit:
        raise ValueError("budget() requires at least one of: tokens, tokens_per_call, cost, cost_per_call")
    if has_cost_limit and cost_function is None:
        raise ValueError("budget() requires cost_function when cost or cost_per_call is set")
    return _BudgetHandle(
        _tokens=tokens,
        _tokens_per_call=tokens_per_call,
        _cost=cost,
        _cost_per_call=cost_per_call,
        _cost_function=cost_function,
        _estimate_usage=estimate_usage,
    )
