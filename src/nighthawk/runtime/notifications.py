"""Await host notifications, bounding cleanup after interruption."""

from __future__ import annotations

import asyncio
import inspect

from .async_bridge import bound_interrupted_bridge_cleanup


async def await_notification(result: object, *, interrupted: bool = False, timeout: float = 1.0) -> None:
    if not inspect.isawaitable(result):
        return
    if not interrupted:
        await result
        return
    bound_interrupted_bridge_cleanup(timeout)
    task = asyncio.ensure_future(result)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    finally:
        if not task.done():
            task.cancel()
            task.add_done_callback(_consume_cleanup_result)


def _consume_cleanup_result(task: asyncio.Future[object]) -> None:
    if not task.cancelled():
        task.exception()
