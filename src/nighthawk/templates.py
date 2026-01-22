from __future__ import annotations

import os
from pathlib import Path

from .errors import NaturalExecutionError


def evaluate_template(text: str, template_locals: dict[str, object]) -> str:
    """Evaluate a Python 3.14 template string from trusted input.

    This intentionally allows function execution inside templates under the trusted-input model.
    """

    try:
        tpl = eval("t" + repr(text), {"__builtins__": __builtins__}, template_locals)
    except Exception as e:
        raise NaturalExecutionError(f"Template evaluation failed: {e}") from e

    try:
        strings = tpl.strings
        values = tpl.values
    except Exception as e:
        raise NaturalExecutionError(f"Unexpected template object: {e}") from e

    out: list[str] = []
    for i, s in enumerate(strings):
        out.append(s)
        if i < len(values):
            out.append(str(values[i]))
    return "".join(out)


_ALLOWED_INCLUDE_ROOTS: tuple[str, ...] = ("docs/", "tests/")


def include(path: str, *, workspace_root: Path) -> str:
    if os.path.isabs(path):
        raise NaturalExecutionError("include(path): absolute paths are not allowed")
    if ".." in Path(path).parts:
        raise NaturalExecutionError("include(path): path traversal is not allowed")
    if not path.endswith(".md"):
        raise NaturalExecutionError("include(path): only .md files are allowed")
    if not any(path.startswith(r) for r in _ALLOWED_INCLUDE_ROOTS):
        raise NaturalExecutionError("include(path): path must start with docs/ or tests/")

    full = (workspace_root / path).resolve()
    # Ensure the resolved path stays within workspace_root.
    if workspace_root.resolve() not in full.parents and full != workspace_root.resolve():
        raise NaturalExecutionError("include(path): resolved path is outside workspace root")

    try:
        return full.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise NaturalExecutionError(f"include(path): file not found: {path}") from e
