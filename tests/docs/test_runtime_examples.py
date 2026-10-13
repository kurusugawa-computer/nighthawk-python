import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_NAME_TO_OUTPUT = {
    "prompt_observation": "prompt_sections=PROGRAM,LOCALS,GLOBALS; helper_visible=True",
    "embedded_sync": "summary_saved=True; terminal_after_save=True",
    "embedded_async": "selected_type=Selection; terminal_after_save=True",
    "function_admission": "refused_body_effects=0; completed_calls=2",
    "model_selection": "models=A,B,A; unapproved_model_refused=True",
    "request_budget": "observed_tokens=120; next_request_refused=True; aggregate_tokens=0",
    "notification_cleanup": "returned_after_notification_cleanup=True",
}


@pytest.fixture(scope="module")
def example_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    installed_python = os.environ.get("NIGHTHAWK_DISTRIBUTION_PYTHON")
    if installed_python is None:
        return Path(sys.executable)
    executable = Path(installed_python).expanduser().absolute()
    assert executable.is_file(), f"Distribution interpreter does not exist: {executable}"
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import json, sys, nighthawk; print(json.dumps({'prefix': sys.prefix, 'package': nighthawk.__file__}))",
        ],
        cwd=tmp_path_factory.mktemp("distribution_origin"),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    origin_name_to_path = json.loads(probe.stdout)
    prefix = Path(origin_name_to_path["prefix"]).resolve()
    package = Path(origin_name_to_path["package"]).resolve()
    assert not prefix.is_relative_to(_REPOSITORY_ROOT), "Distribution environment must be outside the source tree"
    assert package.is_relative_to(prefix), f"Nighthawk must come from the distribution environment: {package}"
    return executable


@pytest.fixture
def example_name_to_path(tmp_path: Path) -> dict[str, Path]:
    document = "\n".join(
        (_REPOSITORY_ROOT / "docs" / name).read_text() for name in ("runtime-configuration.md", "embedded-execution.md", "verification.md")
    )
    examples = re.findall(
        r"<!-- runnable-example:([a-z_]+) -->\s*```py\n(.*?)```\s*<!-- /runnable-example:\1 -->",
        document,
        re.DOTALL,
    )
    assert len(examples) == len(_EXAMPLE_NAME_TO_OUTPUT)
    assert {name for name, source in examples} == set(_EXAMPLE_NAME_TO_OUTPUT)
    name_to_path: dict[str, Path] = {}
    for name, source in examples:
        path = tmp_path / f"{name}.py"
        path.write_text(source)
        name_to_path[name] = path
    return name_to_path


@pytest.mark.parametrize("example_name", _EXAMPLE_NAME_TO_OUTPUT)
def test_runtime_example(example_name: str, example_name_to_path: dict[str, Path], example_python: Path) -> None:
    source_path = example_name_to_path[example_name]
    completed = subprocess.run(
        [str(example_python), "-I", str(source_path)],
        cwd=source_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == _EXAMPLE_NAME_TO_OUTPUT[example_name]
