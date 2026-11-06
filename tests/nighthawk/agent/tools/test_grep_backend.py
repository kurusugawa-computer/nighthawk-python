"""Tests for the :class:`GrepBackend` DI hook on ``build_inspect_workspace``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from nighthawk.agent import Grant
from nighthawk.agent.tools import build_inspect_workspace
from nighthawk.agent.tools.inspect_workspace import FileMatch, GrepBackend


class _FakeRipgrepBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
    ) -> Sequence[FileMatch]:
        self.calls.append(
            {
                "pattern": pattern,
                "search_root": search_root,
                "glob_filter": glob_filter,
                "leading_context_line_count": leading_context_line_count,
                "trailing_context_line_count": trailing_context_line_count,
                "case_insensitive": case_insensitive,
                "maximum_match_count": maximum_match_count,
                "maximum_grep_file_bytes": maximum_grep_file_bytes,
                "readable_roots": readable_roots,
            }
        )
        return (
            FileMatch(
                file_path=search_root / "synthetic.txt",
                line_number=1,
                line=f"backend matched {pattern}",
            ),
        )


def _grep_search(bundle: Grant) -> Callable[..., Sequence[FileMatch]]:
    return cast(Callable[..., Sequence[FileMatch]], bundle.capabilities["grep_search"].function)


def test_grep_backend_protocol_is_runtime_checkable() -> None:
    backend = _FakeRipgrepBackend()
    assert isinstance(backend, GrepBackend)


def test_default_grep_uses_pure_python_when_no_backend(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("needle\n")
    bundle = build_inspect_workspace(workspace_root=workspace)
    matches = _grep_search(bundle)("needle")
    assert len(matches) == 1
    assert matches[0].line == "needle"


def test_grep_backend_overrides_pure_python(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("real-content\n")
    backend = _FakeRipgrepBackend()
    bundle = build_inspect_workspace(workspace_root=workspace, grep_backend=backend)
    matches = _grep_search(bundle)(
        "anything",
        leading_context_line_count=2,
        trailing_context_line_count=3,
        case_insensitive=True,
        maximum_match_count=10,
    )
    assert len(matches) == 1
    assert matches[0].line == "backend matched anything"
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["pattern"] == "anything"
    assert call["leading_context_line_count"] == 2
    assert call["trailing_context_line_count"] == 3
    assert call["case_insensitive"] is True
    assert call["maximum_match_count"] == 10
    assert call["search_root"] == workspace.resolve()
    assert call["readable_roots"] == (workspace.resolve(),)
