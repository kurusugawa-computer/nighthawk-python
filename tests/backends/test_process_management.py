import asyncio
import sys

import pytest

from nighthawk.backends.process_management import stop_process


@pytest.mark.parametrize("ignore_termination", [False, True])
def test_process_cleanup_terminates_and_reaps_running_provider(ignore_termination: bool) -> None:
    async def execute() -> None:
        source = "import signal, time\n"
        if ignore_termination:
            source += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        source += "print('ready', flush=True)\ntime.sleep(30)\n"
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", source, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            assert process.stdout is not None
            assert await asyncio.wait_for(process.stdout.readline(), timeout=3) == b"ready\n"
            await asyncio.wait_for(stop_process(process), timeout=3)
            assert process.returncode is not None and process.returncode < 0
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()

    asyncio.run(execute())
