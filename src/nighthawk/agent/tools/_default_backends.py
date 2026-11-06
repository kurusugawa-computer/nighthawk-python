"""Reference file-patch backend.

:func:`build_default_file_writer` returns a :class:`FileWriter` backed by the
local filesystem. It is opt-in: pass it to :func:`build_coding_actions`
together with a :class:`CodingActionPolicy` whose ``path_write_allow_list``
covers the target tree.

The reference writer pre-validates every operation before any disk write
(``update`` requires exactly one match of ``old_text``, ``add`` requires an
existing parent and, by default, a missing target, ``delete`` requires an
existing file), then commits each file atomically through ``os.replace``. It
does not provide cross-file atomicity: an operating-system failure mid-sequence
can leave earlier operations committed.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .requests import (
    AddFileOperation,
    DeleteFileOperation,
    FilePatchOperation,
    FilePatchRequest,
    UpdateFileOperation,
)

__all__ = ["build_default_file_writer"]


@dataclass(frozen=True, slots=True)
class _DefaultFileWriter:
    overwrite_on_add: bool = False

    def apply(self, request: FilePatchRequest) -> None:
        for operation in request.operations:
            self._pre_validate(operation)
        for operation in request.operations:
            self._apply(operation)

    def _pre_validate(self, operation: FilePatchOperation) -> None:
        if isinstance(operation, AddFileOperation):
            parent = operation.file_path.parent
            if not parent.exists() or not parent.is_dir():
                raise FileNotFoundError(f"parent directory missing: {parent!s}")
            if not self.overwrite_on_add and operation.file_path.exists():
                raise FileExistsError(f"target already exists: {operation.file_path!s}")
            return
        if isinstance(operation, UpdateFileOperation):
            if not operation.file_path.is_file():
                raise FileNotFoundError(f"file not found for update: {operation.file_path!s}")
            existing = operation.file_path.read_text(encoding="utf-8")
            occurrence_count = existing.count(operation.old_text)
            if occurrence_count == 0:
                raise ValueError(f"old_text not found in {operation.file_path!s}")
            if occurrence_count > 1:
                raise ValueError(f"old_text matched {occurrence_count} times in {operation.file_path!s}; expected exactly one")
            return
        if isinstance(operation, DeleteFileOperation) and not operation.file_path.is_file():
            raise FileNotFoundError(f"file not found for delete: {operation.file_path!s}")

    def _apply(self, operation: FilePatchOperation) -> None:
        if isinstance(operation, AddFileOperation):
            _write_atomic(operation.file_path, operation.new_text)
            return
        if isinstance(operation, UpdateFileOperation):
            existing = operation.file_path.read_text(encoding="utf-8")
            _write_atomic(operation.file_path, existing.replace(operation.old_text, operation.new_text, 1))
            return
        if isinstance(operation, DeleteFileOperation):
            operation.file_path.unlink()


def _write_atomic(file_path: Path, text: str) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=str(file_path.parent))
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary_name, file_path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def build_default_file_writer(*, overwrite_on_add: bool = False) -> _DefaultFileWriter:
    """Return the reference :class:`FileWriter`; ``overwrite_on_add`` relaxes the add check."""

    return _DefaultFileWriter(overwrite_on_add=overwrite_on_add)
