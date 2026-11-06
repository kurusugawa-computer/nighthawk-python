"""Read-only workspace-context capabilities.

:func:`build_inspect_workspace` returns a grant of bounded read-only
capabilities (``read_file``, ``list_directory``, ``glob_search``,
``grep_search``, ``view_image``) whose path authority is fixed by the readable
roots before Natural execution can see them. They declare no perimeter effect.

Symlink traversal, hidden-file handling, and result ordering are documented per
capability below rather than fixed by a specification.
"""

from __future__ import annotations

import fnmatch
import mimetypes
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic_ai.messages import BinaryContent, ImageUrl

from .._grants import Capability, Grant
from .._observability import agent_span, emit_agent_event
from ._validation import ensure_non_negative_or_raise, ensure_positive_or_raise
from ._workspace_paths import is_inside_any, resolve_inside, to_workspace_relative

__all__ = [
    "DirectoryEntry",
    "FileMatch",
    "FileSlice",
    "GrepBackend",
    "ImageContent",
    "ImageReference",
    "SearchHit",
    "build_inspect_workspace",
]


type ImageContent = BinaryContent | ImageUrl
"""Image-only narrowed subset of :data:`pydantic_ai.messages.UserContent`.

``str``, ``TextContent``, audio / document / video / uploaded-file are
intentionally excluded. :class:`ImageReference` enforces the narrowing
both statically (via this alias) and at runtime (via
``__post_init__``).
"""


_DEFAULT_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_EXTENSION_TO_IMAGE_MEDIA_TYPE: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass(frozen=True, slots=True)
class FileSlice:
    """A bounded line-oriented read of a file."""

    file_path: Path
    text: str
    starting_line_number: int
    next_line_number: int | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """A single directory listing entry."""

    file_path: Path
    entry_type: Literal["file", "directory", "symlink", "other"]


@dataclass(frozen=True, slots=True)
class FileMatch:
    """A single grep-style match with optional context lines."""

    file_path: Path
    line_number: int
    line: str
    leading_context: tuple[str, ...] = ()
    trailing_context: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single glob hit."""

    file_path: Path
    modified_at_unix_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ImageReference:
    """A workspace image surfaced as Pydantic AI multi-modal content."""

    file_path: Path
    content: ImageContent
    detail: Literal["original"] | None = None

    def __post_init__(self) -> None:
        content = self.content
        if not isinstance(content, (BinaryContent, ImageUrl)):
            raise TypeError("ImageReference.content must be BinaryContent or ImageUrl")
        if isinstance(content, BinaryContent):
            media_type = content.media_type or ""
            if not media_type.startswith("image/"):
                raise ValueError(f"ImageReference.content (BinaryContent) media_type must start with 'image/', got {media_type!r}")


@runtime_checkable
class GrepBackend(Protocol):
    """Caller-supplied grep backend for :func:`build_inspect_workspace`.

    The default ``build_inspect_workspace`` uses a pure-Python regex
    walker. Pass a :class:`GrepBackend` to substitute a faster engine
    (for example ripgrep). Implementations must respect ``readable_roots``
    and ``maximum_grep_file_bytes``: the framework does not re-validate
    the returned :class:`FileMatch` paths.
    """

    def search(
        self,
        pattern: str,
        *,
        readable_roots: tuple[Path, ...],
        search_root: Path,
        glob_filter: str | None,
        leading_context_line_count: int,
        trailing_context_line_count: int,
        case_insensitive: bool,
        maximum_match_count: int,
        maximum_grep_file_bytes: int,
    ) -> Sequence[FileMatch]: ...


@dataclass(frozen=True, slots=True)
class _InspectWorkspaceConfiguration:
    workspace_root: Path
    additional_readable_directories: tuple[Path, ...] = ()
    skip_hidden_directories: bool = False
    maximum_grep_file_bytes: int = 5 * 1024 * 1024
    maximum_image_bytes: int = 10 * 1024 * 1024
    allowed_image_extensions: frozenset[str] = field(default_factory=lambda: _DEFAULT_IMAGE_EXTENSIONS)
    image_url_mapper: Callable[[Path], ImageUrl] | None = None
    grep_backend: GrepBackend | None = None

    @property
    def all_readable_roots(self) -> tuple[Path, ...]:
        return (self.workspace_root, *self.additional_readable_directories)


def _emit_tool_event(
    span_name: str, configuration: _InspectWorkspaceConfiguration, file_path: Path, *, extra: dict[str, object] | None = None
) -> None:
    relative_path = to_workspace_relative(file_path, configuration.workspace_root)
    attributes: dict[str, object] = {"file_extension": file_path.suffix}
    if relative_path is not None:
        attributes["workspace_relative_path"] = relative_path
    if extra is not None:
        attributes.update(extra)
    emit_agent_event(span_name, attributes)


def _read_file_bytes_or_raise(resolved_path: Path) -> bytes:
    return resolved_path.read_bytes()


def _is_likely_binary(blob: bytes) -> bool:
    return b"\x00" in blob[:8192]


def _make_read_file(configuration: _InspectWorkspaceConfiguration) -> Callable[..., FileSlice]:
    readable_roots = configuration.all_readable_roots

    def read_file(
        file_path: Path,
        *,
        starting_line_number: int = 1,
        maximum_line_count: int = 2_000,
    ) -> FileSlice:
        ensure_positive_or_raise(starting_line_number, parameter_name="starting_line_number")
        ensure_positive_or_raise(maximum_line_count, parameter_name="maximum_line_count")
        with agent_span("tool.read_file"):
            resolved = resolve_inside(file_path, readable_roots)
            blob = resolved.read_bytes()
            if _is_likely_binary(blob):
                raise ValueError(f"file {file_path!s} contains binary content")
            text = blob.decode("utf-8")
            lines = text.splitlines(keepends=True)
            slice_lines = lines[starting_line_number - 1 : starting_line_number - 1 + maximum_line_count]
            truncated = (starting_line_number - 1 + maximum_line_count) < len(lines)
            next_line_number = starting_line_number + len(slice_lines) if truncated else None
            result = FileSlice(
                file_path=resolved,
                text="".join(slice_lines),
                starting_line_number=starting_line_number,
                next_line_number=next_line_number,
                truncated=truncated,
            )
            _emit_tool_event(
                "tool.read_file",
                configuration,
                resolved,
                extra={"line_count": len(slice_lines), "truncated": truncated},
            )
            return result

    return read_file


def _classify_entry(file_path: Path) -> Literal["file", "directory", "symlink", "other"]:
    if file_path.is_symlink():
        return "symlink"
    if file_path.is_dir():
        return "directory"
    if file_path.is_file():
        return "file"
    return "other"


def _walk_directory(
    directory_path: Path,
    *,
    maximum_depth: int,
    skip_hidden_directories: bool,
) -> Iterable[Path]:
    if maximum_depth == 0:
        return
    pending: list[tuple[Path, int]] = [(directory_path, 0)]
    while pending:
        current_directory, current_depth = pending.pop(0)
        try:
            children = sorted(current_directory.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for child in children:
            if skip_hidden_directories and child.is_dir() and child.name.startswith("."):
                continue
            yield child
            if child.is_dir() and not child.is_symlink() and (current_depth + 1) < maximum_depth:
                pending.append((child, current_depth + 1))


def _make_list_directory(configuration: _InspectWorkspaceConfiguration) -> Callable[..., Sequence[DirectoryEntry]]:
    readable_roots = configuration.all_readable_roots

    def list_directory(
        directory_path: Path,
        *,
        maximum_depth: int = 1,
        maximum_entry_count: int = 200,
    ) -> Sequence[DirectoryEntry]:
        ensure_non_negative_or_raise(maximum_depth, parameter_name="maximum_depth")
        ensure_positive_or_raise(maximum_entry_count, parameter_name="maximum_entry_count")
        with agent_span("tool.list_directory"):
            resolved = resolve_inside(directory_path, readable_roots)
            entries: list[DirectoryEntry] = []
            for child in _walk_directory(
                resolved,
                maximum_depth=maximum_depth,
                skip_hidden_directories=configuration.skip_hidden_directories,
            ):
                resolved_child = child.resolve(strict=False)
                if not is_inside_any(resolved_child, readable_roots):
                    continue
                entries.append(DirectoryEntry(file_path=resolved_child, entry_type=_classify_entry(child)))
                if len(entries) >= maximum_entry_count:
                    break
            entries.sort(key=lambda entry: str(entry.file_path))
            _emit_tool_event(
                "tool.list_directory",
                configuration,
                resolved,
                extra={"entry_count": len(entries)},
            )
            return tuple(entries)

    return list_directory


def _validate_glob_pattern(pattern: str) -> None:
    if not pattern:
        raise ValueError("glob_search pattern must not be empty")
    if Path(pattern).is_absolute():
        raise ValueError("glob_search pattern must be relative")
    parts = Path(pattern).parts
    if any(part == ".." for part in parts):
        raise ValueError("glob_search pattern must not contain '..' segments")


def _make_glob_search(configuration: _InspectWorkspaceConfiguration) -> Callable[..., Sequence[SearchHit]]:
    readable_roots = configuration.all_readable_roots

    def glob_search(
        pattern: str,
        *,
        search_root: Path | None = None,
        maximum_result_count: int = 200,
    ) -> Sequence[SearchHit]:
        ensure_positive_or_raise(maximum_result_count, parameter_name="maximum_result_count")
        _validate_glob_pattern(pattern)
        effective_root = search_root if search_root is not None else configuration.workspace_root
        with agent_span("tool.glob_search"):
            resolved_root = resolve_inside(effective_root, readable_roots)
            seen: set[Path] = set()
            hits: list[SearchHit] = []
            for raw_hit in resolved_root.glob(pattern):
                try:
                    resolved_hit = raw_hit.resolve(strict=True)
                except (FileNotFoundError, OSError):
                    continue
                if resolved_hit in seen:
                    continue
                if not is_inside_any(resolved_hit, readable_roots):
                    continue
                seen.add(resolved_hit)
                modified_at = resolved_hit.stat().st_mtime if resolved_hit.exists() else None
                hits.append(SearchHit(file_path=resolved_hit, modified_at_unix_seconds=modified_at))
                if len(hits) >= maximum_result_count:
                    break
            hits.sort(key=lambda hit: str(hit.file_path))
            _emit_tool_event(
                "tool.glob_search",
                configuration,
                resolved_root,
                extra={"hit_count": len(hits)},
            )
            return tuple(hits)

    return glob_search


def _pure_python_grep(
    pattern: str,
    *,
    readable_roots: tuple[Path, ...],
    search_root: Path,
    glob_filter: str | None,
    leading_context_line_count: int,
    trailing_context_line_count: int,
    case_insensitive: bool,
    maximum_match_count: int,
    maximum_grep_file_bytes: int,
) -> Sequence[FileMatch]:
    try:
        compiled_pattern = re.compile(pattern, flags=re.IGNORECASE if case_insensitive else 0)
    except re.error as compile_error:
        raise ValueError(f"invalid regex: {compile_error}") from compile_error
    matches: list[FileMatch] = []
    for current_root, _directory_names, file_names in os.walk(search_root):
        for file_name in file_names:
            if glob_filter is not None and not fnmatch.fnmatch(file_name, glob_filter):
                continue
            candidate = Path(current_root) / file_name
            try:
                resolved_candidate = candidate.resolve(strict=True)
            except (FileNotFoundError, OSError):
                continue
            if not is_inside_any(resolved_candidate, readable_roots):
                continue
            try:
                if resolved_candidate.stat().st_size > maximum_grep_file_bytes:
                    continue
                blob = _read_file_bytes_or_raise(resolved_candidate)
            except (PermissionError, FileNotFoundError, OSError):
                continue
            if _is_likely_binary(blob):
                continue
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if compiled_pattern.search(line):
                    leading_start = max(0, index - leading_context_line_count)
                    trailing_end = min(len(lines), index + 1 + trailing_context_line_count)
                    matches.append(
                        FileMatch(
                            file_path=resolved_candidate,
                            line_number=index + 1,
                            line=line,
                            leading_context=tuple(lines[leading_start:index]),
                            trailing_context=tuple(lines[index + 1 : trailing_end]),
                        )
                    )
                    if len(matches) >= maximum_match_count:
                        break
            if len(matches) >= maximum_match_count:
                break
        if len(matches) >= maximum_match_count:
            break
    return tuple(matches)


def _make_grep_search(configuration: _InspectWorkspaceConfiguration) -> Callable[..., Sequence[FileMatch]]:
    readable_roots = configuration.all_readable_roots

    def grep_search(
        pattern: str,
        *,
        search_root: Path | None = None,
        glob_filter: str | None = None,
        leading_context_line_count: int = 0,
        trailing_context_line_count: int = 0,
        case_insensitive: bool = False,
        maximum_match_count: int = 50,
    ) -> Sequence[FileMatch]:
        ensure_non_negative_or_raise(leading_context_line_count, parameter_name="leading_context_line_count")
        ensure_non_negative_or_raise(trailing_context_line_count, parameter_name="trailing_context_line_count")
        ensure_positive_or_raise(maximum_match_count, parameter_name="maximum_match_count")
        effective_root = search_root if search_root is not None else configuration.workspace_root
        with agent_span("tool.grep_search"):
            resolved_root = resolve_inside(effective_root, readable_roots)
            if configuration.grep_backend is not None:
                matches = configuration.grep_backend.search(
                    pattern,
                    readable_roots=readable_roots,
                    search_root=resolved_root,
                    glob_filter=glob_filter,
                    leading_context_line_count=leading_context_line_count,
                    trailing_context_line_count=trailing_context_line_count,
                    case_insensitive=case_insensitive,
                    maximum_match_count=maximum_match_count,
                    maximum_grep_file_bytes=configuration.maximum_grep_file_bytes,
                )
            else:
                matches = _pure_python_grep(
                    pattern,
                    readable_roots=readable_roots,
                    search_root=resolved_root,
                    glob_filter=glob_filter,
                    leading_context_line_count=leading_context_line_count,
                    trailing_context_line_count=trailing_context_line_count,
                    case_insensitive=case_insensitive,
                    maximum_match_count=maximum_match_count,
                    maximum_grep_file_bytes=configuration.maximum_grep_file_bytes,
                )
            _emit_tool_event(
                "tool.grep_search",
                configuration,
                resolved_root,
                extra={"match_count": len(matches)},
            )
            return tuple(matches)

    return grep_search


def _media_type_from_extension(extension: str) -> str:
    if extension in _EXTENSION_TO_IMAGE_MEDIA_TYPE:
        return _EXTENSION_TO_IMAGE_MEDIA_TYPE[extension]
    guessed_media_type, _ = mimetypes.guess_type(f"placeholder{extension}")
    if guessed_media_type is None or not guessed_media_type.startswith("image/"):
        return "application/octet-stream"
    return guessed_media_type


def _make_view_image(configuration: _InspectWorkspaceConfiguration) -> Callable[..., ImageReference]:
    readable_roots = configuration.all_readable_roots

    def view_image(
        file_path: Path,
        *,
        detail: Literal["original"] | None = None,
    ) -> ImageReference:
        with agent_span("tool.view_image"):
            resolved = resolve_inside(file_path, readable_roots)
            extension = resolved.suffix.lower()
            if extension not in configuration.allowed_image_extensions:
                raise ValueError(f"image extension {extension!r} is not in the allow-list")
            byte_size = resolved.stat().st_size
            if byte_size > configuration.maximum_image_bytes:
                raise ValueError(f"image is {byte_size} bytes, exceeding maximum {configuration.maximum_image_bytes}")
            content: ImageContent
            if configuration.image_url_mapper is None:
                blob = resolved.read_bytes()
                content = BinaryContent(data=blob, media_type=_media_type_from_extension(extension))
            else:
                content = configuration.image_url_mapper(resolved)
            result = ImageReference(file_path=resolved, content=content, detail=detail)
            _emit_tool_event(
                "tool.view_image",
                configuration,
                resolved,
                extra={"byte_size": byte_size, "transport": "binary" if configuration.image_url_mapper is None else "image_url"},
            )
            return result

    return view_image


def build_inspect_workspace(
    *,
    workspace_root: Path,
    additional_readable_directories: Sequence[Path] = (),
    skip_hidden_directories: bool = False,
    maximum_grep_file_bytes: int = 5 * 1024 * 1024,
    maximum_image_bytes: int = 10 * 1024 * 1024,
    allowed_image_extensions: frozenset[str] = _DEFAULT_IMAGE_EXTENSIONS,
    image_url_mapper: Callable[[Path], ImageUrl] | None = None,
    grep_backend: GrepBackend | None = None,
) -> Grant:
    """Return a grant of read-only context capabilities rooted at ``workspace_root``.

    ``grep_backend`` overrides the pure-Python regex walker used by
    ``grep_search``; an implementation must respect the supplied
    ``readable_roots`` and ``maximum_grep_file_bytes`` arguments.
    """

    configuration = _InspectWorkspaceConfiguration(
        workspace_root=workspace_root.resolve(strict=True),
        additional_readable_directories=tuple(directory.resolve(strict=True) for directory in additional_readable_directories),
        skip_hidden_directories=skip_hidden_directories,
        maximum_grep_file_bytes=maximum_grep_file_bytes,
        maximum_image_bytes=maximum_image_bytes,
        allowed_image_extensions=allowed_image_extensions,
        image_url_mapper=image_url_mapper,
        grep_backend=grep_backend,
    )
    roots = ", ".join(str(root) for root in configuration.all_readable_roots)
    authority_boundary = f"read-only access under: {roots}"
    return Grant(
        (
            Capability(name="read_file", function=_make_read_file(configuration), authority_boundary=authority_boundary),
            Capability(name="list_directory", function=_make_list_directory(configuration), authority_boundary=authority_boundary),
            Capability(name="glob_search", function=_make_glob_search(configuration), authority_boundary=authority_boundary),
            Capability(name="grep_search", function=_make_grep_search(configuration), authority_boundary=authority_boundary),
            Capability(name="view_image", function=_make_view_image(configuration), authority_boundary=authority_boundary),
        )
    )
