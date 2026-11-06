"""Side-effecting coding capabilities with Python-bound authority.

:func:`build_coding_actions` returns a grant of direct capabilities whose
authority boundary (command allow lists, writable roots, URL policy, output
bounds) is fixed by :class:`CodingActionPolicy` before Natural execution can see
them. Every capability declares the perimeter effect classes it can cause, so a
run whose effective perimeter policy does not permit them is rejected at run
entry, and every attempt is recorded before the effect under host mediation.

Policy checks fail closed: an empty allow list admits nothing unless the
matching ``allow_any_*`` flag is set. Violations raise
:class:`nighthawk.agent.errors.CapabilityRejectedError` with one of the codes in
:data:`CodingActionRejectionCode`; messages never carry request payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .._grants import Capability, Grant
from .._observability import agent_span, emit_agent_event
from .._perimeter import EffectFootprint
from ..errors import CapabilityRejectedError
from ._validation import check_domain
from ._workspace_paths import is_inside_any, resolve_write_target
from .requests import (
    AddFileOperation,
    CommandRequest,
    DeleteFileOperation,
    FilePatchRequest,
    ImageGenerationRequest,
    UpdateFileOperation,
    WebRequest,
)

__all__ = [
    "CodingActionPolicy",
    "CodingActionRejectionCode",
    "CommandExecutor",
    "CommandOutput",
    "FilePatchOutcome",
    "FileWriter",
    "GeneratedImage",
    "ImageGenerationClient",
    "WebRequestClient",
    "WebResponse",
    "build_coding_actions",
]


type CodingActionRejectionCode = Literal[
    "command_prefix_not_allowed",
    "command_prefix_denied",
    "command_working_directory_disallowed",
    "path_outside_allow_list",
    "path_parent_missing",
    "domain_not_allowed",
    "domain_blocked",
    "scheme_disallowed",
    "host_missing",
    "header_name_denied",
    "body_too_large",
]


@dataclass(frozen=True, slots=True)
class CodingActionPolicy:
    """Authority bound to the coding capabilities before exposure."""

    allowed_command_prefixes: tuple[tuple[str, ...], ...] = ()
    allow_any_command_prefix: bool = False
    denied_command_prefixes: tuple[tuple[str, ...], ...] = ()
    command_working_directory_allow_list: tuple[Path, ...] = ()
    path_write_allow_list: tuple[Path, ...] = ()
    allowed_domains: frozenset[str] = field(default_factory=frozenset)
    allow_any_domain: bool = False
    blocked_domains: frozenset[str] = field(default_factory=frozenset)
    allowed_schemes: frozenset[str] = field(default_factory=lambda: frozenset({"https"}))
    denied_request_header_names: frozenset[str] = field(default_factory=frozenset)
    maximum_request_body_bytes: int = 1_000_000
    maximum_preview_character_count: int = 4_000


class _FrozenResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CommandOutput(_FrozenResult):
    """Bounded previews and optionally retained complete command output.

    Missing complete fields mean the backend did not retain that content;
    ``truncated`` describes only preview omission and never implies retention.
    """

    exit_code: int
    stdout_preview: str = ""
    stderr_preview: str = ""
    standard_output_text: str | None = None
    standard_error_text: str | None = None
    truncated: bool = False


class FilePatchOutcome(_FrozenResult):
    """Typed result of one applied file patch."""

    applied_operation_count: int = Field(ge=1)


class WebResponse(_FrozenResult):
    """A bounded HTTP preview and optional complete body retained by the backend."""

    status_code: int
    body_preview: str = ""
    body_text: str | None = None
    truncated: bool = False


class GeneratedImage(_FrozenResult):
    """Typed result of one image generation."""

    media_type: str
    artifact_path: Path | None = None
    byte_count: int | None = Field(default=None, ge=0)


@runtime_checkable
class CommandExecutor(Protocol):
    """Caller-supplied process-execution backend; owns spawning, timeout, capture."""

    def execute(self, request: CommandRequest) -> CommandOutput: ...


@runtime_checkable
class FileWriter(Protocol):
    """Caller-supplied file-patch backend; owns atomic application and validation."""

    def apply(self, request: FilePatchRequest) -> None: ...


@runtime_checkable
class WebRequestClient(Protocol):
    """Caller-supplied HTTP backend; owns the connection and response capture."""

    def send(self, request: WebRequest) -> WebResponse: ...


@runtime_checkable
class ImageGenerationClient(Protocol):
    """Caller-supplied image-generation backend; owns provider call and storage."""

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage: ...


def _reject(capability_name: str, reason_code: CodingActionRejectionCode) -> CapabilityRejectedError:
    emit_agent_event("coding_action_rejected", {"capability_name": capability_name, "reason_code": reason_code})
    return CapabilityRejectedError(capability_name=capability_name, reason_code=reason_code)


def _matches_any_prefix(command: tuple[str, ...], prefixes: tuple[tuple[str, ...], ...]) -> bool:
    return any(len(prefix) <= len(command) and command[: len(prefix)] == prefix for prefix in prefixes)


def _bounded_preview(text: str, maximum_character_count: int) -> tuple[str, bool]:
    if len(text) <= maximum_character_count:
        return text, False
    return text[:maximum_character_count], True


def _check_command_policy(request: CommandRequest, policy: CodingActionPolicy) -> None:
    if _matches_any_prefix(request.command, policy.denied_command_prefixes):
        raise _reject("run_command", "command_prefix_denied")
    if not policy.allow_any_command_prefix and (
        not policy.allowed_command_prefixes or not _matches_any_prefix(request.command, policy.allowed_command_prefixes)
    ):
        raise _reject("run_command", "command_prefix_not_allowed")
    if not policy.command_working_directory_allow_list:
        raise _reject("run_command", "command_working_directory_disallowed")
    try:
        resolved_working_directory = request.working_directory.resolve(strict=True)
    except OSError as error:
        raise _reject("run_command", "command_working_directory_disallowed") from error
    if not is_inside_any(resolved_working_directory, policy.command_working_directory_allow_list):
        raise _reject("run_command", "command_working_directory_disallowed")


def _check_file_patch_policy(request: FilePatchRequest, policy: CodingActionPolicy) -> None:
    if not policy.path_write_allow_list:
        raise _reject("apply_file_patch", "path_outside_allow_list")
    for operation in request.operations:
        try:
            if isinstance(operation, AddFileOperation):
                resolve_write_target(operation.file_path, allowed_roots=policy.path_write_allow_list, must_exist=False)
            elif isinstance(operation, UpdateFileOperation | DeleteFileOperation):
                resolve_write_target(operation.file_path, allowed_roots=policy.path_write_allow_list, must_exist=True)
        except FileNotFoundError as error:
            raise _reject("apply_file_patch", "path_parent_missing") from error
        except ValueError as error:
            raise _reject("apply_file_patch", "path_outside_allow_list") from error


def _check_web_request_policy(request: WebRequest, policy: CodingActionPolicy) -> None:
    parsed = urlsplit(request.url)
    if parsed.scheme not in policy.allowed_schemes:
        raise _reject("web_request", "scheme_disallowed")
    if not parsed.hostname:
        raise _reject("web_request", "host_missing")
    allowed, reason = check_domain(
        request.url,
        allowed_schemes=policy.allowed_schemes,
        allowed_domains=policy.allowed_domains,
        blocked_domains=policy.blocked_domains,
        allow_any_domain=policy.allow_any_domain,
    )
    if not allowed:
        if reason in {"domain_blocked", "domain_not_allowed", "scheme_disallowed", "host_missing"}:
            raise _reject("web_request", reason)  # type: ignore[arg-type]
        raise _reject("web_request", "domain_not_allowed")
    denied_header_names_lowered = {name.lower() for name in policy.denied_request_header_names}
    for header in request.headers:
        if header.name.lower() in denied_header_names_lowered:
            raise _reject("web_request", "header_name_denied")
    if request.body is not None and len(request.body.encode("utf-8")) > policy.maximum_request_body_bytes:
        raise _reject("web_request", "body_too_large")


def _make_run_command(policy: CodingActionPolicy, command_executor: CommandExecutor) -> Capability:
    def run_command(request: CommandRequest) -> CommandOutput:
        """Run one allow-listed command in an allow-listed working directory."""

        _check_command_policy(request, policy)
        with agent_span("coding_action.run_command", {"verb": request.verb}):
            output = command_executor.execute(request)
        stdout_preview, stdout_truncated = _bounded_preview(output.stdout_preview, policy.maximum_preview_character_count)
        stderr_preview, stderr_truncated = _bounded_preview(output.stderr_preview, policy.maximum_preview_character_count)
        emit_agent_event("coding_action.run_command", {"exit_code": output.exit_code})
        return CommandOutput(
            exit_code=output.exit_code,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            standard_output_text=output.standard_output_text,
            standard_error_text=output.standard_error_text,
            truncated=output.truncated or stdout_truncated or stderr_truncated,
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


def _make_apply_file_patch(policy: CodingActionPolicy, file_writer: FileWriter) -> Capability:
    def apply_file_patch(request: FilePatchRequest) -> FilePatchOutcome:
        """Apply add, update, and delete file operations under the writable roots."""

        _check_file_patch_policy(request, policy)
        with agent_span("coding_action.apply_file_patch", {"operation_count": len(request.operations)}):
            file_writer.apply(request)
        emit_agent_event("coding_action.apply_file_patch", {"operation_count": len(request.operations)})
        return FilePatchOutcome(applied_operation_count=len(request.operations))

    roots = ", ".join(str(root) for root in policy.path_write_allow_list) or "no root"
    return Capability(
        name="apply_file_patch",
        function=apply_file_patch,
        authority_boundary=f"write files only under: {roots}",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"durable_write_back", "destructive"})),
    )


def _make_web_request(policy: CodingActionPolicy, web_request_client: WebRequestClient) -> Capability:
    def web_request(request: WebRequest) -> WebResponse:
        """Send one HTTP request to an allow-listed host over an allow-listed scheme."""

        _check_web_request_policy(request, policy)
        parsed = urlsplit(request.url)
        with agent_span("coding_action.web_request", {"method": request.method, "host": parsed.hostname or ""}):
            response = web_request_client.send(request)
        body_preview, truncated = _bounded_preview(response.body_preview, policy.maximum_preview_character_count)
        emit_agent_event("coding_action.web_request", {"status_code": response.status_code})
        return WebResponse(
            status_code=response.status_code, body_preview=body_preview, body_text=response.body_text, truncated=response.truncated or truncated
        )

    domain_description = "any domain not on the block list" if policy.allow_any_domain else f"{len(policy.allowed_domains)} allow-listed domain(s)"
    return Capability(
        name="web_request",
        function=web_request,
        authority_boundary=f"HTTP {', '.join(sorted(policy.allowed_schemes))} to {domain_description}",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"})),
    )


def _make_generate_image(image_generation_client: ImageGenerationClient) -> Capability:
    def generate_image(request: ImageGenerationRequest) -> GeneratedImage:
        """Generate one image from a prompt through the configured provider."""

        with agent_span("coding_action.generate_image", {"output_format": request.output_format}):
            image = image_generation_client.generate(request)
        emit_agent_event("coding_action.generate_image", {"media_type": image.media_type})
        return image

    return Capability(
        name="generate_image",
        function=generate_image,
        authority_boundary="image generation through the configured provider and storage only",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress", "durable_write_back"})),
    )


def build_coding_actions(
    *,
    policy: CodingActionPolicy,
    command_executor: CommandExecutor | None = None,
    file_writer: FileWriter | None = None,
    web_request_client: WebRequestClient | None = None,
    image_generation_client: ImageGenerationClient | None = None,
) -> Grant:
    """Return a grant of side-effecting coding capabilities bound to ``policy``.

    A capability is included only when its backend is supplied: omitted backends
    omit the capability rather than failing open or failing late. Compose the
    result into the run-entry grant with :meth:`Grant.compose`.
    """

    capability_list: list[Capability] = []
    if command_executor is not None:
        capability_list.append(_make_run_command(policy, command_executor))
    if file_writer is not None:
        capability_list.append(_make_apply_file_patch(policy, file_writer))
    if web_request_client is not None:
        capability_list.append(_make_web_request(policy, web_request_client))
    if image_generation_client is not None:
        capability_list.append(_make_generate_image(image_generation_client))
    return Grant(capability_list)
