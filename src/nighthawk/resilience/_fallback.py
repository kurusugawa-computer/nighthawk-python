from __future__ import annotations

import enum
import functools
import inspect
import logging
import typing
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, overload

_logger = logging.getLogger("nighthawk.resilience")


class _Sentinel(enum.Enum):
    MISSING = enum.auto()


_MISSING = _Sentinel.MISSING


def _get_resolved_return_annotation(function: Callable[..., Any]) -> Any:
    """Return the resolved return-type annotation for *function*, or ``inspect.Parameter.empty``."""
    target = function.func if isinstance(function, functools.partial) else function
    try:
        hints = typing.get_type_hints(target)
        if "return" in hints:
            return hints["return"]
    except Exception:  # noqa: BLE001
        pass

    try:
        annotation = inspect.signature(function).return_annotation
        if annotation is inspect.Parameter.empty or isinstance(annotation, str):
            return inspect.Parameter.empty
        return annotation
    except (TypeError, ValueError):
        return inspect.Parameter.empty


def _maybe_set_merged_return_signature(
    wrapper: Callable[..., Any],
    first_function: Callable[..., Any],
    functions: tuple[Callable[..., Any], ...],
) -> None:
    """Set ``__signature__`` on *wrapper* if return types in *functions* differ."""
    return_annotations: list[Any] = []
    for function in functions:
        annotation = _get_resolved_return_annotation(function)
        if annotation is not inspect.Parameter.empty:
            return_annotations.append(annotation)

    unique = list(dict.fromkeys(return_annotations))

    if len(unique) <= 1:
        return

    try:
        merged = unique[0]
        for annotation in unique[1:]:
            merged = merged | annotation
    except TypeError:
        return

    signature = inspect.signature(first_function)
    wrapper.__signature__ = signature.replace(return_annotation=merged)  # type: ignore[union-attr]


@overload
def fallback[**P, R](
    *functions: Callable[P, Coroutine[Any, Any, R]],
    on: type[BaseException] | tuple[type[BaseException], ...] = ...,
) -> Callable[P, Coroutine[Any, Any, R]]: ...


@overload
def fallback[**P, R](
    *functions: Callable[P, Coroutine[Any, Any, R]],
    default: R,
    on: type[BaseException] | tuple[type[BaseException], ...] = ...,
) -> Callable[P, Coroutine[Any, Any, R]]: ...


@overload
def fallback[**P, R](
    *functions: Callable[P, R],
    on: type[BaseException] | tuple[type[BaseException], ...] = ...,
) -> Callable[P, R]: ...


@overload
def fallback[**P, R](
    *functions: Callable[P, R],
    default: R,
    on: type[BaseException] | tuple[type[BaseException], ...] = ...,
) -> Callable[P, R]: ...


def fallback(
    *functions: Callable[..., Any],
    default: Any = _MISSING,
    on: type[BaseException] | tuple[type[BaseException], ...] = Exception,
) -> Callable[..., Any]:
    """Create a fallback chain from multiple functions.

    Tries each function in order. The first successful result wins.
    If all functions fail and *default* is provided, returns *default*.
    If all functions fail and no *default* is provided, raises the last
    exception.

    Sync/async detection is based on the first function in the chain.
    In async mode, each individual function is checked for async-ness,
    allowing mixed sync/async fallback chains.

    Args:
        *functions: Functions to try in order. Must have compatible
            signatures.
        default: Value to return if all functions fail. If not provided,
            the last exception is raised.
        on: Exception type(s) that trigger fallback to the next function.
            Defaults to :class:`Exception`.

    Returns:
        A composed function that tries alternatives in order.

    Example::

        safe_classify = fallback(classify_gpt4, classify_mini, default="unknown")
        result = safe_classify(text)
    """
    if not functions:
        raise ValueError("fallback() requires at least one function")

    first_function = functions[0]

    if inspect.iscoroutinefunction(first_function):

        @wraps(first_function)
        async def async_fallback_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: BaseException | None = None
            for function in functions:
                try:
                    if inspect.iscoroutinefunction(function):
                        return await function(*args, **kwargs)
                    else:
                        return function(*args, **kwargs)
                except on as exception:
                    last_exception = exception
                    _logger.info(
                        "Fallback: %s failed with %s: %s, trying next",
                        getattr(function, "__name__", repr(function)),
                        type(exception).__name__,
                        exception,
                    )

            if not isinstance(default, _Sentinel):
                return default
            assert last_exception is not None
            raise last_exception

        _maybe_set_merged_return_signature(async_fallback_wrapper, first_function, functions)
        return async_fallback_wrapper

    @wraps(first_function)
    def sync_fallback_wrapper(*args: Any, **kwargs: Any) -> Any:
        last_exception: BaseException | None = None
        for function in functions:
            try:
                return function(*args, **kwargs)
            except on as exception:
                last_exception = exception
                _logger.info(
                    "Fallback: %s failed with %s: %s, trying next",
                    getattr(function, "__name__", repr(function)),
                    type(exception).__name__,
                    exception,
                )

        if not isinstance(default, _Sentinel):
            return default
        assert last_exception is not None
        raise last_exception

    _maybe_set_merged_return_signature(sync_fallback_wrapper, first_function, functions)
    return sync_fallback_wrapper
