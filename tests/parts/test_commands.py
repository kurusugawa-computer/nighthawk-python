from __future__ import annotations

from pathlib import Path

import pytest

from nighthawk.harness.errors import CapabilityRejectedError
from nighthawk.parts.commands import CommandOutput, CommandPolicy, CommandRequest, command_capability


class _RecordingCommandExecutor:
    def __init__(self, output: CommandOutput | None = None) -> None:
        self.request_list: list[CommandRequest] = []
        self.output = output if output is not None else CommandOutput(exit_code=0, standard_output_preview="ok")

    def execute(self, request: CommandRequest) -> CommandOutput:
        self.request_list.append(request)
        return self.output


def test_run_command_policy_fails_closed_and_bounds_previews(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _RecordingCommandExecutor(CommandOutput(exit_code=1, standard_output_preview="a" * 40, standard_error_preview="b"))
    capability = command_capability(
        policy=CommandPolicy(allow_any_command_prefix=True, command_working_directory_allow_list=(workspace,), maximum_preview_character_count=16),
        command_executor=executor,
    )
    assert capability.name == "run_command"
    assert capability.effect_footprint.perimeter_effect_class_set == {"destructive", "egress", "durable_write_back"}
    run_command = capability.function
    output = run_command(CommandRequest(command=("echo", "hi"), working_directory=workspace))
    assert output == CommandOutput(exit_code=1, standard_output_preview="a" * 16, standard_error_preview="b", truncated=True)
    closed = command_capability(policy=CommandPolicy(command_working_directory_allow_list=(workspace,)), command_executor=executor).function
    with pytest.raises(CapabilityRejectedError) as captured:
        closed(CommandRequest(command=("echo", "hi"), working_directory=workspace))
    assert captured.value.reason_code == "command_prefix_not_allowed"
    denied = command_capability(
        policy=CommandPolicy(
            allow_any_command_prefix=True, denied_command_prefixes=(("rm", "-rf"),), command_working_directory_allow_list=(workspace,)
        ),
        command_executor=executor,
    ).function
    with pytest.raises(CapabilityRejectedError) as denied_capture:
        denied(CommandRequest(command=("rm", "-rf", "/"), working_directory=workspace))
    assert denied_capture.value.reason_code == "command_prefix_denied"
    with pytest.raises(CapabilityRejectedError) as outside:
        run_command(CommandRequest(command=("echo",), working_directory=tmp_path))
    assert outside.value.reason_code == "command_working_directory_disallowed"
    assert len(executor.request_list) == 1


def test_command_wrapper_keeps_optional_full_values(tmp_path: Path) -> None:
    retained = "a" * 1000 + "marker"
    backend = _RecordingCommandExecutor(
        CommandOutput(
            exit_code=0, standard_output_preview=retained, standard_error_preview="error", standard_output_text=retained, standard_error_text="error"
        )
    )
    function = command_capability(
        policy=CommandPolicy(allow_any_command_prefix=True, command_working_directory_allow_list=(tmp_path,), maximum_preview_character_count=16),
        command_executor=backend,
    ).function
    result = function(CommandRequest(command=("example",), working_directory=tmp_path))
    assert isinstance(result, CommandOutput)
    assert result.standard_output_preview == retained[:16] and result.truncated
    assert result.standard_output_text is retained and result.standard_error_text == "error"
    preview_only = CommandOutput(exit_code=0, standard_output_preview="partial", truncated=True)
    assert preview_only.standard_output_text is None
