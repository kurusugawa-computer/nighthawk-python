import re
import runpy
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

import nighthawk as nh
from nighthawk.testing import CallbackExecutor, StepCall, StepResponse, pass_response, raise_response


@pytest.fixture
def example_name_to_path(tmp_path: Path) -> dict[str, Path]:
    document = (Path(__file__).resolve().parents[2] / "docs" / "patterns.md").read_text(encoding="utf-8")
    examples = re.findall(r"<!-- error-example:([a-z_]+) -->\s*```py\n(.*?)```\s*<!-- /error-example:\1 -->", document, re.DOTALL)
    name_to_path: dict[str, Path] = {}
    for name, source in examples:
        path = tmp_path / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        name_to_path[name] = path
    return name_to_path


@pytest.mark.parametrize("example_name", ["default_raise", "custom_raise", "output_validation"])
def test_documented_error_handler(example_name: str, example_name_to_path: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    events: list[nh.lifecycle.StepFinished] = []

    def respond(call: StepCall) -> StepResponse:
        if example_name == "output_validation":
            return pass_response(binding_name_to_value={"count": "not an integer"})
        error_type = cast(type[BaseException], call.step_globals["InputError"]) if example_name == "custom_raise" else Exception
        return raise_response("invalid input", error_type=error_type)

    executor = CallbackExecutor(respond)
    with nh.run(executor), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(events.append)):
        namespace = runpy.run_path(str(example_name_to_path[example_name]), init_globals={"nh": nh})

    assert len(executor.calls) == len(events) == 1
    event = events[0]
    if example_name == "output_validation":
        assert isinstance(event, nh.lifecycle.StepFailed)
        assert event.failure_stage == "binding_validation"
        assert isinstance(event.original_exception, ValidationError)
        assert capsys.readouterr().out == "Output validation failed; review the input before retrying.\n"
    else:
        assert isinstance(event, nh.lifecycle.StepRaised)
        assert type(event.exception) is (namespace["InputError"] if example_name == "custom_raise" else Exception)
        prefix = "Input error" if example_name == "custom_raise" else "Validation failed"
        assert capsys.readouterr().out == f"{prefix}: invalid input\n"


def test_output_validation_handler_propagates_other_failure_stages(example_name_to_path: dict[str, Path]) -> None:
    original = RuntimeError("executor failed")

    def respond(call: StepCall) -> StepResponse:
        raise original

    with nh.run(CallbackExecutor(respond)), pytest.raises(nh.ExecutionError) as caught:
        runpy.run_path(str(example_name_to_path["output_validation"]), init_globals={"nh": nh})
    assert caught.value.__cause__ is original
    assert caught.value.step_failed is not None
    assert caught.value.step_failed.failure_stage == "executor"
