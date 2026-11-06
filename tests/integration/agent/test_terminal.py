import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.examples.repl.terminal_support import TerminalProcess


@pytest.mark.integration
@pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="OPENAI_API_KEY is required for default terminal acceptance")
def test_default_terminal_declares_and_reuses_generated_function_in_one_session_process(tmp_path: Path) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path), "--no-animation")
    try:
        terminal.wait_screen(lambda screen: "Idle" in terminal.status_line)
        assert len(terminal.worker_process_id_set) == 1
        worker_process_id = next(iter(terminal.worker_process_id_set))
        terminal.send("フィボナッチ数列を計算する関数 <:fib> を宣言して。0 番目は 0、1 番目は 1 とする。\x1b\r")
        terminal.wait_new_status("success", timeout=120)
        terminal.cell("assert fib(3) == 2\nimport os\nassert os.getpid() == " + str(worker_process_id) + "\noriginal_session = session\nfib(3)")
        assert re.search(rb"\[result[^\]]*\] 2", terminal.raw_output), terminal.visible
        terminal.send("\x7f# fib に 10 を与えて実行し、結果を <:answer> に代入して。\x1b\r")
        terminal.wait_new_status("success", timeout=120)
        terminal.cell("assert answer == 55\nassert session is original_session\nassert os.getpid() == " + str(worker_process_id))
        terminal.cell(
            "@nh.function\n"
            "def spin() -> None:\n"
            "    from nighthawk.agent import CodeProposal, dispatch\n"
            "    source = \"def spin():\\n    print('GENERATED_' + 'RUNNING', flush=True)\\n    while True: pass\\n\"\n"
            "    dispatch(CodeProposal(source=source, export_name_tuple=('spin',))).export_name_to_value['spin']()"
        )
        terminal.send("spin()\x1b\r")
        terminal.read_until("GENERATED_RUNNING")
        terminal.send("draft remains editable")
        terminal.wait_screen(lambda screen: "!> draft remains editable" in screen and "Running Python" in terminal.status_line)
        terminal.send("\x03")
        terminal.wait_new_status("cancelled")
        terminal.send("\x03")
        terminal.cell("assert session is original_session\nassert fib(3) == 2")
        listing = subprocess.check_output(["ps", "-ww", "-axo", "pid=,ppid=,args="], text=True)
        child_list = [line.split(None, 2) for line in listing.splitlines() if len(line.split(None, 2)) == 3]
        assert [int(fields[0]) for fields in child_list if fields[1] == str(terminal.process_id)] == [worker_process_id]
        assert not [fields for fields in child_list if fields[1] == str(worker_process_id)]
        assert terminal.worker_process_id_set == {worker_process_id}
        terminal.finish()
    finally:
        terminal.close()
