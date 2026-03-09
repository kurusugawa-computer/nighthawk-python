"""Verify that prompt examples in docs/manual.md match build_user_prompt output.

Each example in manual.md is wrapped in HTML comment markers:
    <!-- prompt-example:NAME -->
    ```text
    ...prompt text...
    ```
    <!-- /prompt-example:NAME -->

This test file defines the inputs for each named example, calls
build_user_prompt, and asserts the output matches the documented text.
"""

import builtins
import re
from pathlib import Path

import pytest

from nighthawk.configuration import StepExecutorConfiguration
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_executor import build_user_prompt

_MANUAL_PATH = Path(__file__).resolve().parents[2] / "docs" / "manual.md"
_DEFAULT_CONFIGURATION = StepExecutorConfiguration()


def _extract_prompt_example(name: str) -> str:
    """Extract the prompt text for a named example from manual.md."""
    manual_text = _MANUAL_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"<!-- prompt-example:{re.escape(name)} -->\s*```py\n(.*?)```\s*<!-- /prompt-example:{re.escape(name)} -->",
        re.DOTALL,
    )
    match = pattern.search(manual_text)
    if match is None:
        pytest.fail(f"prompt-example:{name} not found in {_MANUAL_PATH}")
    return match.group(1)


def _build_prompt(
    *,
    processed_natural_program: str,
    python_locals: dict[str, object],
    python_globals: dict[str, object] | None = None,
) -> str:
    step_context = StepContext(
        step_id="test",
        step_globals=python_globals if python_globals is not None else {"__builtins__": builtins},
        step_locals=python_locals,
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
    )
    return build_user_prompt(
        processed_natural_program=processed_natural_program,
        step_context=step_context,
        configuration=_DEFAULT_CONFIGURATION,
    )


def test_basic_binding() -> None:
    expected = _extract_prompt_example("basic-binding")
    actual = _build_prompt(
        processed_natural_program="Read <text> and update <:priority> with one of: low, normal, high.",
        python_locals={"text": "Server is on fire!", "priority": "normal"},
    )
    assert actual == expected


def test_carry_pattern() -> None:
    expected = _extract_prompt_example("carry-pattern")
    actual = _build_prompt(
        processed_natural_program=(
            "Read <carry> for prior context.\n"
            "The carry says the previous result was 10.\n"
            "Set <:result> to 20 (previous result plus 10).\n"
            "Append a one-line summary of what you did to <carry>."
        ),
        python_locals={"carry": ["Set result to 10."], "result": 0},
    )
    assert actual == expected


def test_fstring_injection() -> None:
    expected = _extract_prompt_example("fstring-injection")
    project_policy = ["safety-first", "concise-output", "cite-assumptions"]
    actual = _build_prompt(
        processed_natural_program=(
            "Read <post>.\n"
            f"Available policies: {project_policy}\n"
            "Select the single best policy and set <:selected_policy>."
        ),
        python_locals={"post": "Breaking: earthquake hits downtown", "selected_policy": ""},
    )
    assert actual == expected


def test_local_function_signature() -> None:
    expected = _extract_prompt_example("local-function-signature")

    def add_points(base: int, bonus: int) -> int:
        """Return a deterministic sum for score calculation."""
        return base + bonus

    actual = _build_prompt(
        processed_natural_program=(
            "Compute <:result> by choosing the most suitable local helper based on its docstring.\n"
            "Use base=38 and bonus=4."
        ),
        python_locals={"add_points": add_points, "result": 0},
    )
    assert actual == expected


def test_global_function_reference() -> None:
    expected = _extract_prompt_example("global-function-reference")

    def python_average(numbers):  # type: ignore[no-untyped-def]
        return sum(numbers) / len(numbers)

    actual = _build_prompt(
        processed_natural_program=(
            "Map each element of <numbers> to the number it represents,\n"
            "then compute <:result> by calling <python_average> with the mapped list."
        ),
        python_locals={"numbers": [1, "2", "three", "cuatro"], "result": 0},
        python_globals={"__builtins__": builtins, "python_average": python_average},
    )
    assert actual == expected
