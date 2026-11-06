"""Bound cleanup of a coding-agent process after request failure or cancellation."""

from __future__ import annotations

import asyncio
from contextlib import suppress


async def stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.communicate(), timeout=1.0)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(TimeoutError):
            await asyncio.wait_for(process.communicate(), timeout=1.0)
    except BaseException:
        with suppress(ProcessLookupError):
            process.kill()
        raise
