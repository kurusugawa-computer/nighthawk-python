from __future__ import annotations

import os
from pathlib import Path

import pytest

from nighthawk.adapters.files import FileSystemWriter
from nighthawk.parts.files import AddFileOperation, DeleteFileOperation, FilePatchRequest, UpdateFileOperation


def _proposal(*operations: object) -> FilePatchRequest:
    return FilePatchRequest(operations=tuple(operations))  # type: ignore[arg-type]


def test_add_writes_new_file(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    writer = FileSystemWriter()
    writer.apply(_proposal(AddFileOperation(file_path=target, new_text="hi\n")))
    assert target.read_text(encoding="utf-8") == "hi\n"


def test_add_refuses_existing_by_default(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old\n")
    writer = FileSystemWriter()
    with pytest.raises(FileExistsError):
        writer.apply(_proposal(AddFileOperation(file_path=target, new_text="new\n")))
    assert target.read_text(encoding="utf-8") == "old\n"


def test_add_overwrite_when_opted_in(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old\n")
    writer = FileSystemWriter(overwrite_on_add=True)
    writer.apply(_proposal(AddFileOperation(file_path=target, new_text="new\n")))
    assert target.read_text(encoding="utf-8") == "new\n"


def test_add_refuses_missing_parent(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "file.txt"
    writer = FileSystemWriter()
    with pytest.raises(FileNotFoundError):
        writer.apply(_proposal(AddFileOperation(file_path=target, new_text="x")))


def test_update_replaces_unique_match(tmp_path: Path) -> None:
    target = tmp_path / "src.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    writer = FileSystemWriter()
    writer.apply(_proposal(UpdateFileOperation(file_path=target, old_text="beta\n", new_text="BETA\n")))
    assert target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_update_refuses_missing_match(tmp_path: Path) -> None:
    target = tmp_path / "src.txt"
    target.write_text("alpha\n")
    writer = FileSystemWriter()
    with pytest.raises(ValueError, match="old_text not found"):
        writer.apply(_proposal(UpdateFileOperation(file_path=target, old_text="beta\n", new_text="x\n")))
    assert target.read_text(encoding="utf-8") == "alpha\n"


def test_update_refuses_ambiguous_match(tmp_path: Path) -> None:
    target = tmp_path / "src.txt"
    target.write_text("token\ntoken\n")
    writer = FileSystemWriter()
    with pytest.raises(ValueError, match="matched 2 times"):
        writer.apply(_proposal(UpdateFileOperation(file_path=target, old_text="token", new_text="X")))
    assert target.read_text(encoding="utf-8") == "token\ntoken\n"


def test_delete_removes_existing(tmp_path: Path) -> None:
    target = tmp_path / "victim.txt"
    target.write_text("bye\n")
    writer = FileSystemWriter()
    writer.apply(_proposal(DeleteFileOperation(file_path=target)))
    assert not target.exists()


def test_delete_refuses_missing(tmp_path: Path) -> None:
    target = tmp_path / "missing.txt"
    writer = FileSystemWriter()
    with pytest.raises(FileNotFoundError):
        writer.apply(_proposal(DeleteFileOperation(file_path=target)))


def test_pre_validation_blocks_all_writes_when_one_op_fails(tmp_path: Path) -> None:
    add_target = tmp_path / "fresh.txt"
    bad_update_target = tmp_path / "src.txt"
    bad_update_target.write_text("alpha\n")
    writer = FileSystemWriter()
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
    writer = FileSystemWriter()
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


def test_operations_on_one_file_observe_prior_operations(tmp_path: Path) -> None:
    target = tmp_path / "sequence.txt"
    FileSystemWriter().apply(
        _proposal(
            AddFileOperation(file_path=target, new_text="first"),
            UpdateFileOperation(file_path=target, old_text="first", new_text="second"),
            DeleteFileOperation(file_path=target),
            AddFileOperation(file_path=target, new_text="third"),
            UpdateFileOperation(file_path=target, old_text="third", new_text="last"),
        )
    )
    assert target.read_text() == "last"


def test_invalid_later_update_prevents_every_write(tmp_path: Path) -> None:
    target = tmp_path / "sequence.txt"
    target.write_text("original")
    added = tmp_path / "added.txt"
    with pytest.raises(ValueError, match="old_text not found"):
        FileSystemWriter().apply(
            _proposal(
                AddFileOperation(file_path=added, new_text="new"),
                UpdateFileOperation(file_path=target, old_text="original", new_text="changed"),
                UpdateFileOperation(file_path=target, old_text="original", new_text="incorrect"),
            )
        )
    assert target.read_text() == "original"
    assert not added.exists()


def test_duplicate_add_prevents_every_write(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    with pytest.raises(FileExistsError):
        FileSystemWriter().apply(
            _proposal(AddFileOperation(file_path=target, new_text="first"), AddFileOperation(file_path=target, new_text="second"))
        )
    assert not target.exists()


def test_update_preserves_permissions_and_line_endings(tmp_path: Path) -> None:
    target = tmp_path / "script.py"
    target.write_bytes(b"first\r\nsecond\r\n")
    target.chmod(0o754)
    FileSystemWriter().apply(_proposal(UpdateFileOperation(file_path=target, old_text="second", new_text="last")))
    assert target.read_bytes() == b"first\r\nlast\r\n"
    assert target.stat().st_mode & 0o777 == 0o754


def test_delete_does_not_decode_file_contents(tmp_path: Path) -> None:
    target = tmp_path / "binary.dat"
    target.write_bytes(b"\xff\x00")
    FileSystemWriter().apply(_proposal(DeleteFileOperation(file_path=target)))
    assert not target.exists()


def test_preparation_resolves_parent_aliases(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    FileSystemWriter().apply(
        _proposal(
            AddFileOperation(file_path=directory / "file.txt", new_text="first"),
            UpdateFileOperation(file_path=alias / "file.txt", old_text="first", new_text="last"),
        )
    )
    assert (directory / "file.txt").read_text() == "last"


def test_replacement_failure_preserves_prior_effects_and_cleans_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    second.write_text("original")
    replace = os.replace

    def fail_second(source, destination):
        if destination == second:
            raise OSError("replacement failed")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError, match="replacement failed"):
        FileSystemWriter().apply(
            _proposal(
                AddFileOperation(file_path=first, new_text="committed"),
                UpdateFileOperation(file_path=second, old_text="original", new_text="changed"),
            )
        )
    assert first.read_text() == "committed"
    assert second.read_text() == "original"
    assert set(tmp_path.iterdir()) == {first, second}
