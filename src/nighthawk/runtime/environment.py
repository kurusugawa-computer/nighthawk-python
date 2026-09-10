"""Provenance-preserving name lookup for one Natural step."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import CellType, MappingProxyType
from typing import Literal

type EnvironmentSource = Literal["effective", "python", "parent", "task", "step"]


def _is_runtime_name(name: str) -> bool:
    return name.startswith(("__nh_", "__nighthawk_"))


class _PythonEnvironmentView(Mapping[str, object]):
    def __init__(self, environment: StepEnvironment) -> None:
        self._environment = environment

    def __getitem__(self, name: str) -> object:
        return self._environment._resolve_python(name)

    def __iter__(self) -> Iterator[str]:
        environment = self._environment
        yield from sorted(
            name
            for name in (
                environment._local_name_set
                | environment._python_name_to_value.keys()
                | environment._python_name_to_cell.keys()
                | environment._python_global_name_to_value.keys()
                | environment._python_builtin_name_to_value.keys()
            )
            if not _is_runtime_name(name)
        )

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._environment.has_python_name(name)


class _ExpressionNamespace(dict[str, object]):
    """Use one live namespace for top-level and nested Python expression scopes."""

    def __init__(self, environment: StepEnvironment, overrides: Mapping[str, object]) -> None:
        super().__init__({"__builtins__": environment._python_builtin_name_to_value})
        self._environment = environment
        self._override_name_to_value = overrides

    def __missing__(self, name: str) -> object:
        if name in self._override_name_to_value:
            return self._override_name_to_value[name]
        return self._environment.resolve(name)


class StepEnvironment:
    """Read-only environment views with a separate, tracked working namespace.

    Parent membership and object associations are captured at entry.
    Application values remain live; the views do not freeze their object graphs.
    """

    def __init__(
        self,
        *,
        python_name_to_value: Mapping[str, object],
        python_globals: dict[str, object],
        local_name_set: frozenset[str] = frozenset(),
        python_name_to_cell: Mapping[str, CellType] | None = None,
        parent_name_to_value: Mapping[str, object] | None = None,
        task_name_to_value: Mapping[str, object] | None = None,
        output_name_set: frozenset[str] = frozenset(),
        binding_name_to_annotation: Mapping[str, object] | None = None,
        binding_name_to_type: Mapping[str, object] | None = None,
    ) -> None:
        self._python_name_to_value = {name: value for name, value in python_name_to_value.items() if not _is_runtime_name(name)}
        self._python_global_name_to_value = python_globals
        builtins_value = python_globals.get("__builtins__", __builtins__)
        self._python_builtin_name_to_value: dict[str, object] = builtins_value if isinstance(builtins_value, dict) else vars(builtins_value)
        self._local_name_set = local_name_set
        self._python_name_to_cell = dict(python_name_to_cell or {})
        self._output_name_set = output_name_set
        self._step_name_to_value: dict[str, object] = {}
        self._binding_name_to_annotation = dict(binding_name_to_annotation or {})
        self._binding_name_to_type = dict(binding_name_to_type or {})
        self.python_name_to_value: Mapping[str, object] = _PythonEnvironmentView(self)
        self.parent_name_to_value = MappingProxyType(dict(parent_name_to_value or {}))
        self.task_name_to_value = MappingProxyType(dict(task_name_to_value or {}))
        self.step_name_to_value = MappingProxyType(self._step_name_to_value)

    def has_python_name(self, name: str) -> bool:
        return not _is_runtime_name(name) and (
            name in self._local_name_set
            or name in self._python_name_to_value
            or name in self._python_name_to_cell
            or name in self._python_global_name_to_value
            or name in self._python_builtin_name_to_value
        )

    def _resolve_python(self, name: str) -> object:
        if _is_runtime_name(name):
            raise KeyError(name)
        if name in self._python_name_to_cell:
            try:
                return self._python_name_to_cell[name].cell_contents
            except ValueError:
                raise UnboundLocalError(f"Python cell {name!r} is unbound") from None
        if name in self._python_name_to_value:
            return self._python_name_to_value[name]
        if name in self._local_name_set:
            raise UnboundLocalError(f"Python local {name!r} is unbound")
        if name in self._python_global_name_to_value:
            return self._python_global_name_to_value[name]
        if name in self._python_builtin_name_to_value:
            return self._python_builtin_name_to_value[name]
        raise KeyError(name)

    def resolve(self, name: str, *, source: EnvironmentSource = "effective") -> object:
        source_to_name_to_value = {
            "step": self.step_name_to_value,
            "python": self.python_name_to_value,
            "parent": self.parent_name_to_value,
            "task": self.task_name_to_value,
        }
        if source != "effective" and source not in source_to_name_to_value:
            raise ValueError(f"Unknown environment source: {source!r}")
        sources = tuple(source_to_name_to_value) if source == "effective" else (source,)
        for selected_source in sources:
            try:
                return source_to_name_to_value[selected_source][name]
            except KeyError:
                continue
        raise NameError(f"Name {name!r} is absent from the {source} environment", name=name)

    def check_assignment(self, name: str) -> None:
        if _is_runtime_name(name) or (self.has_python_name(name) and name not in self._output_name_set):
            raise ValueError(f"Cannot rebind Python name {name!r}; declare <:{name}> for Python writeback")

    def assign(self, name: str, value: object) -> None:
        self.check_assignment(name)
        self._step_name_to_value[name] = value

    def snapshot_working_state(self) -> dict[str, object]:
        snapshot = dict(self.parent_name_to_value)
        for name in self._python_name_to_value.keys() | self._local_name_set | self._python_name_to_cell.keys():
            if _is_runtime_name(name):
                continue
            try:
                snapshot[name] = self._resolve_python(name)
            except (KeyError, UnboundLocalError):
                snapshot.pop(name, None)
        snapshot.update(self.step_name_to_value)
        return snapshot

    def expression_namespace(self, overrides: Mapping[str, object] | None = None) -> dict[str, object]:
        return _ExpressionNamespace(self, overrides or {})

    def describe(self, name: str) -> dict[str, object]:
        sources = [
            source
            for source, view in (
                ("step", self.step_name_to_value),
                ("python", self.python_name_to_value),
                ("parent", self.parent_name_to_value),
                ("task", self.task_name_to_value),
            )
            if name in view
        ]
        unbound = False
        if sources and sources[0] == "python":
            try:
                self._resolve_python(name)
            except UnboundLocalError:
                unbound = True
        return {
            "name": name,
            "origin": sources[0] if sources else None,
            "shadowed_sources": sources[1:],
            "unbound": unbound,
            "write_eligible": name in self._output_name_set or (not self.has_python_name(name) and not _is_runtime_name(name)),
            "annotation": self._binding_name_to_annotation.get(name),
            "output_contract": self._binding_name_to_type.get(name),
        }
