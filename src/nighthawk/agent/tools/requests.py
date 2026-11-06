"""Typed request values accepted by the side-effecting coding capabilities.

Each request names the semantic inputs of one host-mediated operation and
nothing else: authority (allow lists, roots, schemes, limits) is bound in Python
by the capability that accepts the request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AddFileOperation",
    "CommandRequest",
    "CommandVerb",
    "DeleteFileOperation",
    "FilePatchOperation",
    "FilePatchRequest",
    "HttpHeader",
    "HttpMethod",
    "ImageGenerationRequest",
    "UpdateFileOperation",
    "WebRequest",
]


type CommandVerb = Literal["run", "test", "format", "lint", "type_check", "inspect"]
type HttpMethod = Literal["GET", "POST", "PUT", "DELETE", "PATCH"]


_HTTP_HEADER_FORBIDDEN_CODE_POINTS = frozenset(chr(code_point) for code_point in (*range(0x20), 0x7F))


class _FrozenRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HttpHeader(_FrozenRequest):
    """Validated HTTP header name and value; control characters are rejected."""

    name: str
    value: str

    @field_validator("name", "value")
    @classmethod
    def _reject_control_characters(cls, raw: str) -> str:
        if any(character in _HTTP_HEADER_FORBIDDEN_CODE_POINTS for character in raw):
            raise ValueError("HttpHeader name and value must not contain control characters")
        return raw


class CommandRequest(_FrozenRequest):
    """A shell-free command invocation: argument tokens, working directory, verb."""

    command: tuple[str, ...] = Field(min_length=1)
    working_directory: Path
    verb: CommandVerb = "run"


class _BaseFileOperation(_FrozenRequest):
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


class FilePatchRequest(_FrozenRequest):
    """One or more file operations applied together."""

    operations: tuple[FilePatchOperation, ...] = Field(min_length=1)


class WebRequest(_FrozenRequest):
    """An HTTP request: URL, method, headers, optional body."""

    url: str
    method: HttpMethod = "GET"
    headers: tuple[HttpHeader, ...] = ()
    body: str | None = None


class ImageGenerationRequest(_FrozenRequest):
    """Provider-driven image generation from a prompt."""

    prompt: str = Field(min_length=1)
    output_format: Literal["png", "jpeg", "webp"] = "png"
