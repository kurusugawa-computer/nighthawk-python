"""Text projection for backends that must talk to a CLI text channel.

Converts a ``RequestPromptPartList`` (user-prompt tuples plus tool-return parts)
into a single prompt string and, when needed, a staged temporary directory that
holds ``BinaryContent`` bytes as local files.

Backend-specific concerns (transport selection, CLI option assembly) live in
the individual backend modules. This module must not grow into a general
projection layer.
"""

from __future__ import annotations

import contextlib
import mimetypes
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    BinaryContent,
    CachePoint,
    FileUrl,
    TextContent,
    ToolReturnPart,
    UploadedFile,
    UserContent,
    is_multi_modal_content,
)

from ..runtime._user_content import coalesce_user_content
from ..tools._tool_return_content import resolve_tool_return_segments
from ._text_fallback import (
    format_file_url_with_placeholder,
    format_uploaded_file_with_placeholder,
    is_image_content,
)
from .base import RequestPromptPartList


@dataclass
class TextProjectedRequest:
    prompt_text: str
    temporary_directory: tempfile.TemporaryDirectory[str] | None

    def cleanup(self) -> None:
        """Clean up the temporary directory, if any, suppressing all exceptions."""
        if self.temporary_directory is not None:
            with contextlib.suppress(Exception):
                self.temporary_directory.cleanup()


# Explicit map for common media types so that the chosen extension is
# deterministic across platforms.
#
# We deliberately do NOT delegate to Pydantic AI's ``BinaryContent.format``:
# that property targets web/API ergonomics and returns identifiers like
# ``"jpeg"`` for ``image/jpeg`` and ``"oga"`` for ``audio/ogg``. The CLIs that
# consume the staged file paths sniff by extension and expect the conventional
# ``.jpg`` / ``.ogg`` forms instead.
#
# ``mimetypes.guess_extension`` is also unsuitable as a primary source: it can
# return variants like ``.jpe`` for ``image/jpeg`` depending on the
# environment's ``mime.types`` database. We keep ``mimetypes.guess_extension``
# only as a last-resort fallback for media types not covered here.
_KNOWN_MEDIA_TYPE_TO_EXTENSION: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


def _guess_file_extension(*, media_type: str) -> str:
    known_extension = _KNOWN_MEDIA_TYPE_TO_EXTENSION.get(media_type)
    if known_extension is not None:
        return known_extension
    extension = mimetypes.guess_extension(media_type)
    if extension is None:
        return ".bin"
    return extension


_CONTROL_CHARACTER_TRANSLATION_TABLE = {c: None for c in range(0x00, 0x20)} | {0x7F: None}
_MAX_PROJECTED_FILE_STEM_LENGTH = 200


def _sanitize_projected_file_stem(*, identifier: str) -> str:
    sanitized_stem = Path(identifier).name
    if not sanitized_stem:
        return "file"
    sanitized_stem = sanitized_stem.translate(_CONTROL_CHARACTER_TRANSLATION_TABLE)
    if not sanitized_stem:
        return "file"
    if len(sanitized_stem) > _MAX_PROJECTED_FILE_STEM_LENGTH:
        sanitized_stem = sanitized_stem[:_MAX_PROJECTED_FILE_STEM_LENGTH]
    return sanitized_stem


class _TextProjectionStager:
    """Owns the staging temporary directory for a single projection pass.

    Per-pass orchestration responsibilities bundled here:

    - Lazy lifetime of the staging temporary directory: created on the first
      call to :meth:`stage_binary` and kept alive across the rest of the
      projection so that adjacent ``BinaryContent`` items share one directory.
    - Per-pass file-name collision avoidance via
      :meth:`_build_unique_projected_file_path`, so two ``BinaryContent`` items
      that share an identifier within one step do not clobber each other.
    - Coordination of stem sanitization
      (:func:`_sanitize_projected_file_stem`) and extension selection
      (:func:`_guess_file_extension` plus the deterministic
      ``_KNOWN_MEDIA_TYPE_TO_EXTENSION`` map) at staging time.
    """

    def __init__(self, *, staging_root_directory: Path | None) -> None:
        self._staging_root_directory = staging_root_directory
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None

    @property
    def temporary_directory(self) -> tempfile.TemporaryDirectory[str] | None:
        return self._temporary_directory

    def stage_binary(self, content: BinaryContent) -> Path:
        temporary_directory = self._ensure_temporary_directory()
        safe_file_stem = _sanitize_projected_file_stem(identifier=content.identifier)
        file_extension = _guess_file_extension(media_type=content.media_type)
        file_path = self._build_unique_projected_file_path(
            temporary_directory=temporary_directory,
            file_stem=safe_file_stem,
            file_extension=file_extension,
        )
        file_path.write_bytes(content.data)
        return file_path

    def _ensure_temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        if self._temporary_directory is not None:
            return self._temporary_directory
        if self._staging_root_directory is None:
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="nighthawk-request-files-")
        else:
            self._temporary_directory = tempfile.TemporaryDirectory(
                prefix="nighthawk-request-files-",
                dir=str(self._staging_root_directory),
            )
        return self._temporary_directory

    @staticmethod
    def _build_unique_projected_file_path(
        *,
        temporary_directory: tempfile.TemporaryDirectory[str],
        file_stem: str,
        file_extension: str,
    ) -> Path:
        # The staging directory is fresh per projection pass, so collisions only
        # arise when two BinaryContent items share the same identifier within
        # one step (e.g. the same image bound multiple times). Use ``_N`` suffix
        # only in that case so the common case keeps the unsuffixed identifier.
        file_path = Path(temporary_directory.name) / f"{file_stem}{file_extension}"
        if not file_path.exists():
            return file_path

        collision_index = 2
        while True:
            candidate_path = Path(temporary_directory.name) / f"{file_stem}_{collision_index}{file_extension}"
            if not candidate_path.exists():
                return candidate_path
            collision_index += 1


def _project_multi_modal_content_with_placeholder_lines(
    *,
    content: object,
    stager: _TextProjectionStager,
) -> list[str]:
    if isinstance(content, UploadedFile):
        # Reachable only via tool-return projection; coding-agent user-prompt
        # admission rejects UploadedFile earlier because provider-owned file
        # references cannot be resolved by these backends.
        return format_uploaded_file_with_placeholder(content=content, transport_label="this backend")

    if isinstance(content, BinaryContent):
        file_path = stager.stage_binary(content)
        placeholder = "<image>" if is_image_content(content=content) else "<file>"
        return [
            placeholder,
            f"Local file path: {file_path}",
            f"Media type: {content.media_type}",
        ]

    if isinstance(content, FileUrl):
        return format_file_url_with_placeholder(content=content)

    raise UserError(f"Unsupported multimodal content for text projection: {type(content).__name__}")


def _project_user_content_to_text(
    *,
    content: UserContent,
    stager: _TextProjectionStager,
) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, TextContent):
        return content.content

    return "\n".join(
        _project_multi_modal_content_with_placeholder_lines(
            content=content,
            stager=stager,
        )
    )


def _project_tool_return_part_to_text(
    *,
    tool_return_part: ToolReturnPart,
    stager: _TextProjectionStager,
) -> str:
    segments = resolve_tool_return_segments(
        tool_name=tool_return_part.tool_name,
        payload=tool_return_part.content,
    )
    if segments.is_empty_success():
        return f"Tool return from {tool_return_part.tool_name}:"

    projected_user_content_text = _project_content_item_list_to_text(
        content_items=segments.ordered_user_content,
        stager=stager,
    )

    projected_section_part_list: list[str] = []
    if segments.response_text:
        projected_section_part_list.append(segments.response_text)
    if projected_user_content_text:
        projected_section_part_list.append(projected_user_content_text)

    if projected_section_part_list:
        projected_content_text = "\n\n".join(projected_section_part_list)
        return f"Tool return from {tool_return_part.tool_name}:\n{projected_content_text}"
    return f"Tool return from {tool_return_part.tool_name}:"


def _project_content_item_to_text(
    *,
    content_item: object,
    stager: _TextProjectionStager,
) -> tuple[str, bool]:
    if isinstance(content_item, str):
        return content_item, False
    if isinstance(content_item, TextContent):
        return content_item.content, False
    if isinstance(content_item, CachePoint):
        return "", False
    if not is_multi_modal_content(content_item):
        raise UserError(f"Unsupported request content for text projection: {type(content_item).__name__}")

    projected_content_text = _project_user_content_to_text(
        content=content_item,
        stager=stager,
    )
    return projected_content_text, True


def _project_content_item_list_to_text(
    *,
    content_items: Sequence[UserContent],
    stager: _TextProjectionStager,
) -> str:
    # Collapse adjacent text runs first so the separator logic below only has
    # to reason about "text item vs multimodal item", not about multiple
    # adjacent str/TextContent items. Both producers (RequestPromptPart tuples
    # and ToolReturnPart.model_response_str_and_user_content output) emit only
    # UserContent, so coalesce_user_content can run unconditionally.
    normalized_items = coalesce_user_content(content_items)

    content_text_part_list: list[str] = []
    previous_item_was_multimodal = False

    for content_item in normalized_items:
        projected_content_text, current_item_is_multimodal = _project_content_item_to_text(
            content_item=content_item,
            stager=stager,
        )
        if not projected_content_text:
            continue

        if content_text_part_list:
            if previous_item_was_multimodal and current_item_is_multimodal:
                content_text_part_list.append("\n\n")
            elif previous_item_was_multimodal or current_item_is_multimodal:
                content_text_part_list.append("\n")

        content_text_part_list.append(projected_content_text)
        previous_item_was_multimodal = current_item_is_multimodal

    return "".join(content_text_part_list)


def project_request_prompt_part_list_to_text(
    request_prompt_part_list: RequestPromptPartList,
    *,
    staging_root_directory: Path | None = None,
) -> TextProjectedRequest:
    stager = _TextProjectionStager(staging_root_directory=staging_root_directory)
    projected_part_list: list[str] = []

    try:
        for request_prompt_part in request_prompt_part_list:
            if isinstance(request_prompt_part, tuple):
                projected_part = _project_content_item_list_to_text(
                    content_items=request_prompt_part,
                    stager=stager,
                )
                if projected_part:
                    projected_part_list.append(projected_part)
            elif isinstance(request_prompt_part, ToolReturnPart):
                projected_part = _project_tool_return_part_to_text(
                    tool_return_part=request_prompt_part,
                    stager=stager,
                )
                if projected_part:
                    projected_part_list.append(projected_part)
            else:
                raise TypeError(f"Unexpected request prompt part type: {type(request_prompt_part).__name__}")
    except Exception:
        if stager.temporary_directory is not None:
            with contextlib.suppress(Exception):
                stager.temporary_directory.cleanup()
        raise

    prompt_text = "\n\n".join(projected_part_list)
    return TextProjectedRequest(
        prompt_text=prompt_text,
        temporary_directory=stager.temporary_directory,
    )


def resolve_text_projection_staging_root_directory(*, working_directory: str) -> Path:
    if working_directory:
        return Path(working_directory)
    return Path.cwd()
