"""Tests for the reference :func:`build_default_file_writer` backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from nighthawk.agent.tools import build_default_file_writer
from nighthawk.agent.tools.requests import (
    AddFileOperation,
    DeleteFileOperation,
    FilePatchRequest,
    UpdateFileOperation,
)


def _proposal(*operations: object) -> FilePatchRequest:
    return FilePatchRequest(operations=tuple(operations))  # type: ignore[arg-type]


def test_add_writes_new_file(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    writer = build_default_file_writer()
    writer.apply(_proposal(AddFileOperation(file_path=target, new_text="hi\n")))
    assert target.read_text(encoding="utf-8") == "hi\n"


def test_add_refuses_existing_by_default(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old\n")
    writer = build_default_file_writer()
    with pytest.raises(FileExistsError):
        writer.apply(_proposal(AddFileOperation(file_path=target, new_text="new\n")))
    assert target.read_text(encoding="utf-8") == "old\n"


def test_add_overwrite_when_opted_in(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old\n")
    writer = build_default_file_writer(overwrite_on_add=True)
    writer.apply(_proposal(AddFileOperation(file_path=target, new_text="new\n")))
    assert target.read_text(encoding="utf-8") == "new\n"


def test_add_refuses_missing_parent(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "file.txt"
    writer = build_default_file_writer()
    with pytest.raises(FileNotFoundError):
        writer.apply(_proposal(AddFileOperation(file_path=target, new_text="x")))


def test_update_replaces_unique_match(tmp_path: Path) -> None:
    target = tmp_path / "src.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    writer = build_default_file_writer()
    writer.apply(_proposal(UpdateFileOperation(file_path=target, old_text="beta\n", new_text="BETA\n")))
    assert target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_update_refuses_missing_match(tmp_path: Path) -> None:
    target = tmp_path / "src.txt"
    target.write_text("alpha\n")
    writer = build_default_file_writer()
    with pytest.raises(ValueError, match="old_text not found"):
        writer.apply(_proposal(UpdateFileOperation(file_path=target, old_text="beta\n", new_text="x\n")))
    assert target.read_text(encoding="utf-8") == "alpha\n"


def test_update_refuses_ambiguous_match(tmp_path: Path) -> None:
    target = tmp_path / "src.txt"
    target.write_text("token\ntoken\n")
    writer = build_default_file_writer()
    with pytest.raises(ValueError, match="matched 2 times"):
        writer.apply(_proposal(UpdateFileOperation(file_path=target, old_text="token", new_text="X")))
    assert target.read_text(encoding="utf-8") == "token\ntoken\n"


def test_delete_removes_existing(tmp_path: Path) -> None:
    target = tmp_path / "victim.txt"
    target.write_text("bye\n")
    writer = build_default_file_writer()
    writer.apply(_proposal(DeleteFileOperation(file_path=target)))
    assert not target.exists()


def test_delete_refuses_missing(tmp_path: Path) -> None:
    target = tmp_path / "missing.txt"
    writer = build_default_file_writer()
    with pytest.raises(FileNotFoundError):
        writer.apply(_proposal(DeleteFileOperation(file_path=target)))


def test_pre_validation_blocks_all_writes_when_one_op_fails(tmp_path: Path) -> None:
    add_target = tmp_path / "fresh.txt"
    bad_update_target = tmp_path / "src.txt"
    bad_update_target.write_text("alpha\n")
    writer = build_default_file_writer()
    with pytest.raises(ValueError):
        writer.apply(
            _proposal(
                AddFileOperation(file_path=add_target, new_text="x\n"),
                UpdateFileOperation(file_path=bad_update_target, old_text="missing", new_text="y"),
            )
        )
    assert not add_target.exists()
    assert bad_update_target.read_text(encoding="utf-8") == "alpha\n"


def test_mixed_verbs_apply_sequentially(tmp_path: Path) -> None:
    add_target = tmp_path / "new.txt"
    update_target = tmp_path / "src.txt"
    update_target.write_text("old\n")
    delete_target = tmp_path / "victim.txt"
    delete_target.write_text("doomed\n")
    writer = build_default_file_writer()
    writer.apply(
        _proposal(
            AddFileOperation(file_path=add_target, new_text="hello\n"),
            UpdateFileOperation(file_path=update_target, old_text="old\n", new_text="new\n"),
            DeleteFileOperation(file_path=delete_target),
        )
    )
    assert add_target.read_text(encoding="utf-8") == "hello\n"
    assert update_target.read_text(encoding="utf-8") == "new\n"
    assert not delete_target.exists()
