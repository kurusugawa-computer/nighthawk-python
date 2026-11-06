from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from nighthawk.parts.workspace import (
    DirectoryEntry,
    FileMatch,
    FileSlice,
    ImageReference,
    SearchHit,
    build_inspect_workspace,
)


def _build_bundle(workspace: Path, **overrides: object) -> dict[str, Callable[..., object]]:
    grant = build_inspect_workspace(workspace_root=workspace, **overrides)  # type: ignore[arg-type]
    return {name: capability.function for name, capability in grant.capabilities.items()}


def _read_file(bundle: dict[str, Callable[..., object]]) -> Callable[..., FileSlice]:
    return cast(Callable[..., FileSlice], bundle["read_file"])


def _list_directory(bundle: dict[str, Callable[..., object]]) -> Callable[..., tuple[DirectoryEntry, ...]]:
    return cast(Callable[..., tuple[DirectoryEntry, ...]], bundle["list_directory"])


def _glob_search(bundle: dict[str, Callable[..., object]]) -> Callable[..., tuple[SearchHit, ...]]:
    return cast(Callable[..., tuple[SearchHit, ...]], bundle["glob_search"])


def _grep_search(bundle: dict[str, Callable[..., object]]) -> Callable[..., tuple[FileMatch, ...]]:
    return cast(Callable[..., tuple[FileMatch, ...]], bundle["grep_search"])


def _view_image(bundle: dict[str, Callable[..., object]]) -> Callable[..., ImageReference]:
    return cast(Callable[..., ImageReference], bundle["view_image"])


def test_path_boundary_blocks_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("escape")
    escape_link = workspace / "escape"
    os.symlink(outside, escape_link)
    bundle = _build_bundle(workspace)
    read_file = _read_file(bundle)
    with pytest.raises(ValueError):
        read_file(escape_link / "secret.txt")


def test_read_file_truncation_and_window(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "long.txt"
    target.write_text("\n".join(f"line-{index}" for index in range(20)) + "\n")
    bundle = _build_bundle(workspace)
    read_file = _read_file(bundle)
    first_window = read_file(target, starting_line_number=1, maximum_line_count=5)
    assert isinstance(first_window, FileSlice)
    assert first_window.truncated
    assert first_window.next_line_number == 6
    assert "line-0" in first_window.text
    last_window = read_file(target, starting_line_number=18, maximum_line_count=5)
    assert not last_window.truncated
    assert last_window.next_line_number is None


def test_read_file_rejects_binary(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    binary = workspace / "blob.bin"
    binary.write_bytes(b"hello\x00world")
    bundle = _build_bundle(workspace)
    with pytest.raises(ValueError):
        _read_file(bundle)(binary)


def test_read_file_argument_bounds(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "x.txt"
    target.write_text("hi\n")
    bundle = _build_bundle(workspace)
    read_file = _read_file(bundle)
    with pytest.raises(ValueError):
        read_file(target, starting_line_number=0)
    with pytest.raises(ValueError):
        read_file(target, maximum_line_count=0)


def test_list_directory_default_shows_hidden(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".hidden").mkdir()
    (workspace / ".hidden" / "file.txt").write_text("a")
    (workspace / "visible.txt").write_text("b")
    bundle = _build_bundle(workspace)
    entries = _list_directory(bundle)(workspace, maximum_depth=2)
    assert isinstance(entries, tuple)
    paths = [str(entry.file_path) for entry in entries]
    assert any(".hidden" in path for path in paths)


def test_list_directory_skip_hidden_opt_in(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".hidden").mkdir()
    (workspace / ".hidden" / "file.txt").write_text("a")
    (workspace / "visible.txt").write_text("b")
    bundle = _build_bundle(workspace, skip_hidden_directories=True)
    entries = _list_directory(bundle)(workspace, maximum_depth=2)
    paths = [str(entry.file_path) for entry in entries]
    names = {entry.file_path.name for entry in entries}
    assert ".hidden" not in names
    assert all("file.txt" not in path or ".hidden" not in path for path in paths)


def test_list_directory_skip_hidden_keeps_hidden_files(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".hidden").mkdir()
    (workspace / ".gitignore").write_text("ignored\n")
    (workspace / "visible.txt").write_text("b")
    bundle = _build_bundle(workspace, skip_hidden_directories=True)
    entries = _list_directory(bundle)(workspace, maximum_depth=1)
    names = {entry.file_path.name for entry in entries}
    assert ".hidden" not in names
    assert ".gitignore" in names
    assert "visible.txt" in names


def test_list_directory_argument_bounds(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bundle = _build_bundle(workspace)
    list_directory = _list_directory(bundle)
    with pytest.raises(ValueError):
        list_directory(workspace, maximum_depth=-1)
    with pytest.raises(ValueError):
        list_directory(workspace, maximum_entry_count=0)


def test_list_directory_classifies_entries(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "subdir").mkdir()
    (workspace / "file.txt").write_text("a")
    bundle = _build_bundle(workspace)
    entries = _list_directory(bundle)(workspace, maximum_depth=1)
    assert isinstance(entries, tuple)
    types = {entry.entry_type for entry in entries}
    assert "file" in types
    assert "directory" in types


def test_glob_search_relative_pattern_required(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bundle = _build_bundle(workspace)
    glob_search = _glob_search(bundle)
    with pytest.raises(ValueError):
        glob_search("/etc/*")
    with pytest.raises(ValueError):
        glob_search("../etc/*")


def test_glob_search_returns_hits(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a")
    (workspace / "b.txt").write_text("b")
    (workspace / "c.md").write_text("c")
    bundle = _build_bundle(workspace)
    hits = _glob_search(bundle)("*.txt")
    assert isinstance(hits, tuple)
    assert all(isinstance(hit, SearchHit) for hit in hits)
    paths = sorted(str(hit.file_path) for hit in hits)
    assert any("a.txt" in path for path in paths)
    assert any("b.txt" in path for path in paths)


def test_grep_search_basic(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "doc.txt").write_text("alpha\nbeta\nGAMMA\n")
    bundle = _build_bundle(workspace)
    grep_search = _grep_search(bundle)
    matches = grep_search("beta")
    assert isinstance(matches, tuple)
    assert len(matches) == 1
    assert isinstance(matches[0], FileMatch)
    case_insensitive = grep_search("gamma", case_insensitive=True)
    assert len(case_insensitive) == 1


def test_grep_search_invalid_regex_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bundle = _build_bundle(workspace)
    with pytest.raises(ValueError):
        _grep_search(bundle)("[unterminated")


def test_grep_search_glob_filter_and_context(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "match.txt").write_text("zero\nfind me\nnext\n")
    (workspace / "ignore.md").write_text("find me\n")
    bundle = _build_bundle(workspace)
    matches = _grep_search(bundle)(
        "find me",
        glob_filter="*.txt",
        leading_context_line_count=1,
        trailing_context_line_count=1,
    )
    assert len(matches) == 1
    only_match = matches[0]
    assert only_match.leading_context == ("zero",)
    assert only_match.trailing_context == ("next",)


def test_grep_search_skips_oversize_and_binary(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "small.txt").write_text("interesting\n")
    (workspace / "big.txt").write_bytes(b"interesting\n" * 1024)
    (workspace / "binary.bin").write_bytes(b"interesting\x00\n")
    bundle = _build_bundle(workspace, maximum_grep_file_bytes=64)
    matches = _grep_search(bundle)("interesting")
    paths = {str(match.file_path) for match in matches}
    assert any(path.endswith("small.txt") for path in paths)
    assert all(not path.endswith("big.txt") for path in paths)
    assert all(not path.endswith("binary.bin") for path in paths)


def test_grep_search_handles_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "sealed.txt").write_text("interesting\n")

    def _raise_permission_error(self: Path, *args: object, **kwargs: object) -> bytes:
        raise PermissionError(f"denied: {self}")

    monkeypatch.setattr(Path, "read_bytes", _raise_permission_error)
    bundle = _build_bundle(workspace)
    matches = _grep_search(bundle)("interesting")
    assert matches == ()


def _write_minimal_png(target: Path) -> None:
    # 1x1 transparent PNG
    payload = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    target.write_bytes(payload)


def test_view_image_default_returns_binary_content(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    image = workspace / "pic.png"
    _write_minimal_png(image)
    bundle = _build_bundle(workspace)
    reference = _view_image(bundle)(image)
    assert isinstance(reference, ImageReference)
    assert isinstance(reference.data, bytes)
    assert reference.media_type == "image/png"


def test_view_image_url_mapper_returns_image_url(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    image = workspace / "pic.png"
    _write_minimal_png(image)

    def mapper(file_path: Path) -> str:
        return f"file://{file_path}"

    bundle = _build_bundle(workspace, image_url_mapper=mapper)
    reference = _view_image(bundle)(image)
    assert reference.url == f"file://{image}"


def test_view_image_extension_allow_list(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    not_image = workspace / "notes.txt"
    not_image.write_text("not an image")
    bundle = _build_bundle(workspace)
    with pytest.raises(ValueError):
        _view_image(bundle)(not_image)


def test_view_image_oversize_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    image = workspace / "pic.png"
    _write_minimal_png(image)
    bundle = _build_bundle(workspace, maximum_image_bytes=10)
    with pytest.raises(ValueError):
        _view_image(bundle)(image)


def test_image_reference_rejects_non_image_binary_content(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ImageReference(file_path=tmp_path / "x.wav", data=b"WAVEFORMATEX", media_type="audio/wav")


def test_closures_have_stable_names(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    expected_names = {"read_file", "list_directory", "glob_search", "grep_search", "view_image"}
    assert set(bundle) == expected_names
    for name, callable_value in bundle.items():
        assert getattr(callable_value, "__name__", "") == name


def test_image_reference_round_trip_with_image_url(tmp_path: Path) -> None:
    reference = ImageReference(
        file_path=tmp_path / "pic.png",
        url="https://example.com/pic.png",
    )
    assert reference.url == "https://example.com/pic.png" and reference.data is None
