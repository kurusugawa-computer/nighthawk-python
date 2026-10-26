import re
import subprocess
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_NAME_TO_OUTPUT = {
    "prompt_observation": "prompt_sections=PROGRAM,LOCALS,GLOBALS; helper_visible=True",
    "embedded_sync": "summary_saved=True; terminal_after_save=True",
    "embedded_async": "selected_type=Selection; terminal_after_save=True",
    "output_selection": "automatic=2; empty=0; explicit=1; composed=2; sync_and_async=True",
    "shared_values_sync": "child_value=6; policy_shared=True; parent_input_visible=False",
    "shared_values_async": "child_value=6; policy_shared=True; parent_input_visible=False",
    "function_admission": "refused_body_effects=0; completed_calls=2",
    "model_selection": "models=A,B,A; unapproved_model_refused=True",
    "request_budget": "observed_tokens=120; next_request_refused=True; aggregate_tokens=0",
    "notification_cleanup": "returned_after_notification_cleanup=True",
}


@pytest.fixture
def example_name_to_path(tmp_path: Path) -> dict[str, Path]:
    document = "\n".join(
        (_REPOSITORY_ROOT / "docs" / name).read_text()
        for name in ("runtime-configuration.md", "host-integration.md", "embedded-execution.md", "verification.md")
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
def test_runtime_example(example_name: str, example_name_to_path: dict[str, Path], package_python: Path) -> None:
    source_path = example_name_to_path[example_name]
    completed = subprocess.run(
        [str(package_python), "-I", str(source_path)],
        cwd=source_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == _EXAMPLE_NAME_TO_OUTPUT[example_name]
