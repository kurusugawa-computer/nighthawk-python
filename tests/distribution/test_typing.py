import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.environ.get("NIGHTHAWK_DISTRIBUTION_PYTHON"), reason="Requires an installed distribution outside the source tree")
def test_installed_distribution_preserves_function_types(package_python: Path, tmp_path: Path) -> None:
    source_path = tmp_path / "consumer.py"
    source_path.write_text("""
from typing import assert_type
import nighthawk as nh
from nighthawk.core.natural.definition import FunctionDefinition, get_function_definition
from nighthawk.core.step_definition import StepDefinition
from nighthawk.core.usage import ExecutionUsage, UsageMeter
from nighthawk.harness.proposals import CodeProposal
from nighthawk.parts.files import FilePatchPolicy, file_patch_capability
from nighthawk.adapters.files import FileSystemWriter
from nighthawk.adapters.http import HttpxWebFetchClient
from nighthawk.harness.grants import Capability
from nighthawk.parts.web import WebFetchClient, WebFetchResult

@nh.function
def label(text: str) -> str:
    return text

@nh.function
async def label_async(text: str) -> str:
    return text

assert_type(label('ready'), str)
assert_type(get_function_definition(label), FunctionDefinition | None)
assert_type(StepDefinition('Read the evidence.'), StepDefinition)
assert_type(UsageMeter().snapshot(), ExecutionUsage)
assert_type(CodeProposal(source='def answer(): return 7', export_name_tuple=('answer',)).source, str)
assert_type(file_patch_capability(policy=FilePatchPolicy(), file_writer=FileSystemWriter()), Capability)

def fetch(client: WebFetchClient) -> WebFetchResult:
    return client.fetch('https://example.com', focus='', maximum_character_count=100, check_url=lambda url: None)

assert_type(fetch(HttpxWebFetchClient()), WebFetchResult)

class Reader:
    @nh.function
    def read(self, text: str) -> str:
        return text

    @nh.function
    async def read_async(self, text: str) -> str:
        return text

assert_type(Reader().read('ready'), str)
assert_type(Reader.read(Reader(), 'ready'), str)

async def consume() -> None:
    assert_type(await label_async('ready'), str)
    assert_type(await Reader().read_async('ready'), str)
""")
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
