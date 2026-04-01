from __future__ import annotations

import asyncio
import inspect
import logging
import math
from collections import Counter
from collections.abc import Callable
from functools import wraps
from typing import Any, cast

_logger = logging.getLogger("nighthawk.resilience")


def plurality(results: list[Any]) -> Any:
    """Return the most common result (plurality vote).

    For hashable results, uses :class:`collections.Counter`.
    For unhashable results, falls back to equality comparison.

    Args:
        results: Non-empty list of results to vote on.

    Returns:
        The most common result.

    Raises:
        ValueError: If *results* is empty.
    """
    if not results:
        raise ValueError("plurality() requires at least one result")

    try:
        counter: Counter[Any] = Counter(results)
        return counter.most_common(1)[0][0]
    except TypeError:
        # Unhashable results: fall back to equality comparison.
        best_result = results[0]
        best_count = 0
        for candidate in results:
            count = sum(1 for other in results if other == candidate)
            if count > best_count:
                best_count = count
                best_result = candidate
        return best_result


def vote(
    *,
    count: int = 3,
    decide: Callable[[list[Any]], Any] = plurality,
    min_success: int | None = None,
):
    """Create a majority voting transformer.

    Calls the wrapped function *count* times and aggregates results
    using the *decide* function.

    For async functions, all calls execute concurrently via
    :func:`asyncio.gather`. For sync functions, calls execute
    sequentially.

    Args:
        count: Number of times to call the function.
        decide: Aggregation function. Receives ``list[T]``, returns ``T``.
            Defaults to :func:`plurality` (most common result).
        min_success: Minimum number of successful calls required.
            Defaults to ``ceil(count / 2)``. If fewer calls succeed,
            raises the last exception.

    Returns:
        A decorator that wraps a function with voting logic.

    Example::

        voting_classify = vote(count=3)(classify)
        label = voting_classify(text)
    """
    if count < 1:
        raise ValueError("vote count must be at least 1")

    effective_min_success = min_success if min_success is not None else math.ceil(count / 2)

    def decorator[**P, R](function: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tasks = [asyncio.create_task(_call_async(function, args, kwargs)) for _ in range(count)]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)

                results: list[Any] = []
                last_exception: BaseException | None = None
                for outcome in gathered:
                    if isinstance(outcome, BaseException):
                        last_exception = outcome
                        _logger.info("Vote: call to %s failed: %s", function.__name__, outcome)
                    else:
                        results.append(outcome)

                if len(results) < effective_min_success:
                    if last_exception is not None:
                        raise last_exception
                    raise RuntimeError(f"vote: {len(results)} successful calls, need at least {effective_min_success}")

                return decide(results)

            return cast(Callable[P, R], async_wrapper)

        @wraps(function)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            results: list[Any] = []
            last_exception: BaseException | None = None

            for _ in range(count):
                try:
                    results.append(function(*args, **kwargs))
                except Exception as exception:
                    last_exception = exception
                    _logger.info("Vote: call to %s failed: %s", function.__name__, exception)

            if len(results) < effective_min_success:
                if last_exception is not None:
                    raise last_exception
                raise RuntimeError(f"vote: {len(results)} successful calls, need at least {effective_min_success}")

            return decide(results)

        return cast(Callable[P, R], sync_wrapper)

    return decorator


async def _call_async(
    function: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    return await function(*args, **kwargs)
