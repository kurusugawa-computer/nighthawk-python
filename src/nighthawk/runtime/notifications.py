"""Await each owned notification once, including its cancellation cleanup."""

from __future__ import annotations

import asyncio
import inspect

from .interruption import chain_cleanup_errors


async def await_notification(result: object, *, interrupted: bool = False, timeout: float = 1.0) -> None:
    if not inspect.isawaitable(result):
        return
    task = asyncio.ensure_future(result)
    primary: BaseException | None = None
    try:
        if interrupted:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        else:
            await asyncio.shield(task)
        return
    except BaseException as exception:
        primary = exception
    # A notification can already be unwinding its own cancellation. Cancelling
    # that Task again would interrupt the cleanup we promise to join.
    if not task.done() and (not isinstance(task, asyncio.Task) or not task.cancelling()):
        task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Shield repeated caller cancellation without cancelling the callback again.
            continue
        except BaseException:
            break
    try:
        task.result()
    except BaseException as exception:
        if exception is not primary and not isinstance(exception, asyncio.CancelledError):
            chain_cleanup_errors(primary, exception)
    raise primary
