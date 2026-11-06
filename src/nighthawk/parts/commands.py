from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nighthawk.harness._observability import emit_harness_event, harness_span
from nighthawk.harness.grants import Capability
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.parts._validation import reject_capability
from nighthawk.parts._workspace_paths import is_inside_any


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    allowed_command_prefixes: tuple[tuple[str, ...], ...] = ()
    allow_any_command_prefix: bool = False
    denied_command_prefixes: tuple[tuple[str, ...], ...] = ()
    command_working_directory_allow_list: tuple[Path, ...] = ()
    maximum_preview_character_count: int = 4_000


type CommandVerb = Literal["run", "test", "format", "lint", "type_check", "inspect"]


class CommandRequest(BaseModel):
    """A shell-free command invocation: argument tokens, working directory, verb."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: tuple[str, ...] = Field(min_length=1)
    working_directory: Path
    verb: CommandVerb = "run"


class CommandOutput(BaseModel):
    """Bounded previews and optionally retained complete command output.

    Missing complete fields mean the backend did not retain that content;
    ``truncated`` describes only preview omission and never implies retention.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int
    standard_output_preview: str = ""
    standard_error_preview: str = ""
    standard_output_text: str | None = None
    standard_error_text: str | None = None
    truncated: bool = False


@runtime_checkable
class CommandExecutor(Protocol):
    """Caller-supplied process-execution backend; owns spawning, timeout, capture."""

    def execute(self, request: CommandRequest) -> CommandOutput: ...


def _matches_any_prefix(command: tuple[str, ...], prefixes: tuple[tuple[str, ...], ...]) -> bool:
    return any(len(prefix) <= len(command) and command[: len(prefix)] == prefix for prefix in prefixes)


def _check_command_policy(request: CommandRequest, policy: CommandPolicy) -> None:
    if _matches_any_prefix(request.command, policy.denied_command_prefixes):
        raise reject_capability("run_command", "command_prefix_denied")
    if not policy.allow_any_command_prefix and (
        not policy.allowed_command_prefixes or not _matches_any_prefix(request.command, policy.allowed_command_prefixes)
    ):
        raise reject_capability("run_command", "command_prefix_not_allowed")
    if not policy.command_working_directory_allow_list:
        raise reject_capability("run_command", "command_working_directory_disallowed")
    try:
        resolved_working_directory = request.working_directory.resolve(strict=True)
    except OSError as error:
        raise reject_capability("run_command", "command_working_directory_disallowed") from error
    if not is_inside_any(resolved_working_directory, policy.command_working_directory_allow_list):
        raise reject_capability("run_command", "command_working_directory_disallowed")


def command_capability(*, policy: CommandPolicy, command_executor: CommandExecutor) -> Capability:
    def run_command(request: CommandRequest) -> CommandOutput:
        """Run one allow-listed command in an allow-listed working directory."""

        _check_command_policy(request, policy)
        with harness_span("capability.run_command", {"verb": request.verb}):
            output = command_executor.execute(request)
        standard_output_preview, standard_output_truncated = (
            output.standard_output_preview[: policy.maximum_preview_character_count],
            len(output.standard_output_preview) > policy.maximum_preview_character_count,
        )
        standard_error_preview, standard_error_truncated = (
            output.standard_error_preview[: policy.maximum_preview_character_count],
            len(output.standard_error_preview) > policy.maximum_preview_character_count,
        )
        emit_harness_event("capability.run_command", {"exit_code": output.exit_code})
        return CommandOutput(
            exit_code=output.exit_code,
            standard_output_preview=standard_output_preview,
            standard_error_preview=standard_error_preview,
            standard_output_text=output.standard_output_text,
            standard_error_text=output.standard_error_text,
            truncated=output.truncated or standard_output_truncated or standard_error_truncated,
        )

    if policy.allow_any_command_prefix:
        prefix_description = "any command not on the deny list"
    else:
        prefix_description = f"{len(policy.allowed_command_prefixes)} allow-listed command prefix(es)"
    return Capability(
        name="run_command",
        function=run_command,
        authority_boundary=f"{prefix_description} inside {len(policy.command_working_directory_allow_list)} allow-listed working directory root(s)",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"destructive", "egress", "durable_write_back"})),
    )
