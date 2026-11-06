from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nighthawk.harness._observability import emit_harness_event, harness_span
from nighthawk.harness.grants import Capability
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.parts._validation import reject_capability
from nighthawk.parts._workspace_paths import is_inside_any


@dataclass(frozen=True, slots=True)
class FilePatchPolicy:
    path_write_allow_list: tuple[Path, ...] = ()


class _BaseFileOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    file_path: Path


class AddFileOperation(_BaseFileOperation):
    """Create a file with ``new_text``."""

    verb: Literal["add"] = "add"
    new_text: str


class UpdateFileOperation(_BaseFileOperation):
    """Replace exactly one occurrence of ``old_text`` with ``new_text``."""

    verb: Literal["update"] = "update"
    old_text: str
    new_text: str


class DeleteFileOperation(_BaseFileOperation):
    """Delete a file."""

    verb: Literal["delete"] = "delete"


type FilePatchOperation = Annotated[
    AddFileOperation | UpdateFileOperation | DeleteFileOperation,
    Field(discriminator="verb"),
]


class FilePatchRequest(BaseModel):
    """Ordered file operations; later operations observe earlier changes to the same path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operations: tuple[FilePatchOperation, ...] = Field(min_length=1)


class FilePatchOutcome(BaseModel):
    """Typed result of one applied file patch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    applied_operation_count: int = Field(ge=1)


@runtime_checkable
class FileWriter(Protocol):
    """Validate the ordered patch before writing; own application and partial-failure semantics."""

    def apply(self, request: FilePatchRequest) -> None: ...


def _check_file_patch_policy(request: FilePatchRequest, policy: FilePatchPolicy) -> None:
    if not policy.path_write_allow_list:
        raise reject_capability("apply_file_patch", "path_outside_allow_list")
    for operation in request.operations:
        try:
            entry_path = operation.file_path.parent.resolve(strict=True) / operation.file_path.name
            if not is_inside_any(entry_path, policy.path_write_allow_list) or not is_inside_any(entry_path.resolve(), policy.path_write_allow_list):
                raise ValueError("path is outside the configured allow-list")
        except FileNotFoundError as error:
            raise reject_capability("apply_file_patch", "path_parent_missing") from error
        except ValueError as error:
            raise reject_capability("apply_file_patch", "path_outside_allow_list") from error


def file_patch_capability(*, policy: FilePatchPolicy, file_writer: FileWriter) -> Capability:
    def apply_file_patch(request: FilePatchRequest) -> FilePatchOutcome:
        """Apply add, update, and delete file operations under the writable roots."""

        _check_file_patch_policy(request, policy)
        with harness_span("capability.apply_file_patch", {"operation_count": len(request.operations)}):
            file_writer.apply(request)
        emit_harness_event("capability.apply_file_patch", {"operation_count": len(request.operations)})
        return FilePatchOutcome(applied_operation_count=len(request.operations))

    roots = ", ".join(str(root) for root in policy.path_write_allow_list) or "no root"
    return Capability(
        name="apply_file_patch",
        function=apply_file_patch,
        authority_boundary=f"write files only under: {roots}",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"durable_write_back", "destructive"})),
    )
