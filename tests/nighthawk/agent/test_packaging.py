"""Verify source resolution and isolated wheel runtime/Session acceptance."""

from __future__ import annotations

import importlib
import importlib.util
import pathlib


def test_nighthawk_agent_is_importable() -> None:
    spec = importlib.util.find_spec("nighthawk.agent")
    assert spec is not None
    assert spec.origin is not None


def test_nighthawk_agent_module_resolves_to_source_tree() -> None:
    module = importlib.import_module("nighthawk.agent")
    expected_source_init = pathlib.Path(__file__).resolve().parents[3] / "src" / "nighthawk" / "agent" / "__init__.py"
    assert module.__file__ is not None
    assert pathlib.Path(module.__file__).resolve() == expected_source_init.resolve()


def test_errors_submodule_is_public() -> None:
    submodule = importlib.import_module("nighthawk.agent.errors")
    assert hasattr(submodule, "NaturalExecutionError")


def test_optional_composition_modules_are_packaged_from_source() -> None:
    for name in (
        "values",
        "history",
        "catalog",
        "procedures",
        "learning",
        "generated",
        "generation",
        "conversation",
        "sources",
        "tools.artifacts",
        "tools._artifact_backends",
    ):
        module = importlib.import_module("nighthawk.agent." + name)
        assert module.__file__ is not None
        assert pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(__file__).resolve().parents[3] / "src")
