import re
import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError

import nighthawk as nh
from nighthawk.errors import ExecutionError
from nighthawk.testing import CallbackExecutor, StepCall, StepResponse, pass_response


@pytest.mark.parametrize("invalid_output", [False, True], ids=["validated-average", "invalid-output"])
def test_readme_example(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid_output: bool,
) -> None:
    readme_path = Path(__file__).resolve().parents[2] / "README.md"
    match = re.search(r"```py\n(.*?)```", readme_path.read_text(), re.DOTALL)
    assert match is not None, "README must contain a Python example"
    example_path = tmp_path / "readme_example.py"
    example_path.write_text(match.group(1))
    computed_values: list[float] = []

    def respond(call: StepCall) -> StepResponse:
        assert call.step_locals["numbers"] == [1, "2", "three", "cuatro"]
        assert call.binding_name_to_type["result"] is float
        helper = call.step_globals["python_average"]
        assert callable(helper)
        # Only the language interpretation is scripted; execute the published helper.
        average = helper([1.0, 2.0, 3.0, 4.0])
        assert isinstance(average, float)
        computed_values.append(average)
        # A numeric string exercises coercion through the actual output contract.
        return pass_response(binding_name_to_value={"result": "invalid" if invalid_output else str(average)})

    executor = CallbackExecutor(respond)

    def select_executor(self: nh.AgentStepExecutor, selection: nh.ModelSelection) -> CallbackExecutor:
        assert selection.apply(self.configuration).execution_model is not None
        return executor

    monkeypatch.setattr(nh.AgentStepExecutor, "resolve_model_selection", select_executor)
    if invalid_output:
        with pytest.raises(ExecutionError) as failure:
            runpy.run_path(str(example_path))
        assert isinstance(failure.value.__cause__, ValidationError)
        assert failure.value.step_failed is not None
        assert failure.value.step_failed.failure_stage == "binding_validation"
        assert capsys.readouterr().out == ""
    else:
        namespace = runpy.run_path(str(example_path))
        assert capsys.readouterr().out == "2.5\n"
        # Print alone cannot distinguish a string from the validated float.
        with nh.run(executor):
            result = namespace["calculate_average"]([1, "2", "three", "cuatro"])
        assert type(result) is float
        assert result == 2.5

    assert len(executor.calls) == (1 if invalid_output else 2)
    assert computed_values == [2.5] * len(executor.calls)
