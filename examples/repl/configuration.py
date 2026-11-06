"""Terminal-owned project preparation and optional Manager factory resolution."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

from nighthawk.session import Session, SessionManager, create_session


@contextmanager
def terminal_environment(directory: Path) -> Generator[None]:
    """Own cwd, project lookup, and __main__ until confirmed terminal shutdown."""

    resolved = directory.resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(str(resolved))
    previous_directory = Path.cwd()
    previous_path = list(sys.path)
    previous_main = sys.modules.get("__main__")
    try:
        os.chdir(resolved)
        sys.path.insert(0, str(resolved))
        sys.modules["__main__"] = ModuleType("__main__")
        yield
    finally:
        os.chdir(previous_directory)
        sys.path[:] = previous_path
        if previous_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = previous_main


DEFAULT_EXECUTION_MODEL = "openai-responses:gpt-6-luna"


def create_terminal_session(
    *, execution_model: str | None = None, decision_model: str | None = None, factory_reference: str | None = None
) -> Session:
    """Construct one Session in the terminal's already prepared environment.

    Without a factory, the Session permits exactly the selected models, with DEFAULT_EXECUTION_MODEL when none is selected.
    A factory receives the selections unchanged and owns its defaults and permitted models.
    """

    if factory_reference is None:
        return create_session(execution_model=DEFAULT_EXECUTION_MODEL if execution_model is None else execution_model, decision_model=decision_model)
    module_name, separator, function_name = factory_reference.partition(":")
    if not separator or not module_name or not function_name or ":" in function_name:
        raise ValueError("Session manager factory must use MODULE:FUNCTION")
    factory = getattr(importlib.import_module(module_name), function_name)
    manager = factory(execution_model=execution_model, decision_model=decision_model)
    if not isinstance(manager, SessionManager):
        raise TypeError("Session manager factory must return SessionManager")
    return manager.create_session()
