from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nighthawk.parts.files import AddFileOperation, FilePatchRequest, UpdateFileOperation

__all__ = ["FileSystemWriter"]


@dataclass(frozen=True, slots=True)
class _PreparedOperation:
    file_path: Path
    text: str | None
    mode: int | None


@dataclass(frozen=True, slots=True)
class FileSystemWriter:
    """Validate a patch in order before applying its prepared operations.

    Later operations on the same path observe earlier operations in the patch.
    Writes preserve line endings and existing file permissions; new files are private to the owner.
    ``overwrite_on_add`` permits replacing an existing add target.
    Each file replacement is atomic, but an operating-system failure can leave earlier operations committed.
    Callers coordinate other writers to the same paths during preparation and application.
    """

    overwrite_on_add: bool = False

    def apply(self, request: FilePatchRequest) -> None:
        path_to_prepared_operation: dict[Path, _PreparedOperation] = {}
        prepared_operation_list: list[_PreparedOperation] = []
        for operation in request.operations:
            parent = operation.file_path.parent.resolve(strict=True)
            if not parent.is_dir():
                raise FileNotFoundError(f"parent directory missing: {parent!s}")
            file_path = parent / operation.file_path.name
            previous = path_to_prepared_operation.get(file_path)
            exists = previous.text is not None if previous is not None else file_path.exists() or file_path.is_symlink()
            mode = previous.mode if previous is not None else stat.S_IMODE(file_path.stat().st_mode) if file_path.is_file() else None
            if isinstance(operation, AddFileOperation):
                if exists and not self.overwrite_on_add:
                    raise FileExistsError(f"target already exists: {file_path!s}")
                if exists and file_path.is_dir():
                    raise IsADirectoryError(f"target is a directory: {file_path!s}")
                text = operation.new_text
            else:
                if not exists or previous is None and not file_path.is_file():
                    raise FileNotFoundError(f"file not found for {operation.verb}: {file_path!s}")
                if isinstance(operation, UpdateFileOperation):
                    if previous is not None:
                        assert previous.text is not None
                        existing = previous.text
                    else:
                        existing = file_path.read_text(encoding="utf-8", newline="")
                    occurrence_count = existing.count(operation.old_text)
                    if occurrence_count == 0:
                        raise ValueError(f"old_text not found in {file_path!s}")
                    if occurrence_count > 1:
                        raise ValueError(f"old_text matched {occurrence_count} times in {file_path!s}; expected exactly one")
                    text = existing.replace(operation.old_text, operation.new_text, 1)
                else:
                    text, mode = None, None
            prepared = _PreparedOperation(file_path, text, mode)
            path_to_prepared_operation[file_path] = prepared
            prepared_operation_list.append(prepared)
        for prepared in prepared_operation_list:
            if prepared.text is None:
                prepared.file_path.unlink()
            else:
                _write_atomic(prepared.file_path, prepared.text, mode=prepared.mode)


def _write_atomic(file_path: Path, text: str, *, mode: int | None) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=str(file_path.parent))
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if mode is not None:
            os.chmod(temporary_name, mode)
        os.replace(temporary_name, file_path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise
