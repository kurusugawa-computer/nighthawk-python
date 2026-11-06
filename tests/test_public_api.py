import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import nighthawk

_PACKAGE = Path(nighthawk.__file__).parent
_MODULES = tuple(
    ".".join(("nighthawk", *path.relative_to(_PACKAGE).with_suffix("").parts)).removesuffix(".__init__")
    for path in sorted(_PACKAGE.rglob("*.py"))
    if path.name != "__init__.py" or path.parent != _PACKAGE
)


@pytest.mark.parametrize("module_name", ("nighthawk", *_MODULES))
def test_declared_exports_resolve(module_name: str) -> None:
    module = importlib.import_module(module_name)
    names = getattr(module, "__all__", ())
    assert len(names) == len(set(names))
    for name in names:
        assert hasattr(module, name), f"{module_name}.{name}"


def test_core_depends_only_on_stable_execution_contracts() -> None:
    for path in (_PACKAGE / "core").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                assert not name.startswith(("pydantic_ai", "nighthawk.adapters", "nighthawk.harness", "nighthawk.parts", "nighthawk.session")), (
                    path,
                    name,
                )


def test_harness_does_not_depend_on_parts_or_sessions() -> None:
    for path in (_PACKAGE / "harness").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                assert not name.startswith(("nighthawk.parts", "nighthawk.session")), (path, name)


def test_core_import_does_not_load_optional_integrations() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import nighthawk.core.scoping; assert not any(name in sys.modules for name in ('pydantic_ai', 'nighthawk.adapters', 'nighthawk.harness', 'nighthawk.parts', 'nighthawk.session'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
