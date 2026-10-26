import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.environ.get("NIGHTHAWK_DISTRIBUTION_PYTHON"), reason="Requires an installed distribution outside the source tree")
def test_installed_distribution_preserves_function_types(package_python: Path, tmp_path: Path) -> None:
    source_path = tmp_path / "consumer.py"
    source_path.write_text(
        "from typing import assert_type\n"
        "import nighthawk as nh\n"
        "from nighthawk.resilience import budget, circuit_breaker, timeout\n"
        "from nighthawk.runtime import StepDefinition\n"
        "\n"
        "@nh.function\n"
        "def label(text: str) -> str:\n"
        "    return text\n"
        "\n"
        "@nh.function\n"
        "async def label_async(text: str) -> str:\n"
        "    return text\n"
        "\n"
        "assert_type(label('ready'), str)\n"
        "assert_type(nh.get_function_definition(label), nh.FunctionDefinition | None)\n"
        "assert_type(StepDefinition('Read the evidence.'), StepDefinition)\n"
        "assert_type(timeout(seconds=1)(circuit_breaker()(label))('ready'), str)\n"
        "assert_type(budget(tokens=100)(label)('ready'), str)\n"
        "\n"
        "class Reader:\n"
        "    @circuit_breaker()\n"
        "    def read(self, text: str) -> str:\n"
        "        return text\n"
        "\n"
        "    @circuit_breaker()\n"
        "    async def read_async(self, text: str) -> str:\n"
        "        return text\n"
        "\n"
        "assert_type(Reader().read('ready'), str)\n"
        "assert_type(Reader.read(Reader(), 'ready'), str)\n"
        "\n"
        "async def consume() -> None:\n"
        "    assert_type(await label_async('ready'), str)\n"
        "    assert_type(await timeout(seconds=1)(circuit_breaker()(label_async))('ready'), str)\n"
        "    assert_type(await budget(tokens=100)(label_async)('ready'), str)\n"
        "    assert_type(await Reader().read_async('ready'), str)\n"
    )
    configuration_path = tmp_path / "pyrightconfig.json"
    configuration_path.write_text(
        json.dumps(
            {
                "include": [source_path.name],
                "typeCheckingMode": "strict",
                "useLibraryCodeForTypes": False,
            }
        )
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(configuration_path), "--pythonpath", str(package_python)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
