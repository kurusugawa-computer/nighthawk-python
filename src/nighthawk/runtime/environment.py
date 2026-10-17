"""Provenance-preserving name lookup for one Natural step."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import CellType, MappingProxyType
from typing import Literal

from .bindings import MISSING_ANNOTATION
from .description import annotation_text
from .discovery import DiscoveryResult, page_names

type EnvironmentSource = Literal["effective", "python", "parent", "task", "step"]


class MissingEnvironmentNameError(NameError):
    """A lookup failed in the Natural environment, outside application code."""


class UnboundEnvironmentNameError(UnboundLocalError):
    """A Python local or cell blocks Natural fallback."""


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
            if not environment._is_runtime_name(name)
        )

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._environment.has_python_name(name)


class _ExpressionNamespace(dict[str, object]):
    """Use one live namespace for top-level and nested Python expression scopes."""

    def __init__(self, environment: StepEnvironment, overrides: Mapping[str, object]) -> None:
        super().__init__({"__builtins__": environment._python_builtin_name_to_value, environment.runtime_reference: _RuntimeAccess(environment)})
        self._environment = environment
        self._override_name_to_value = overrides

    def __missing__(self, name: str) -> object:
        if name in self._override_name_to_value:
            return self._override_name_to_value[name]
        return self._environment.resolve(name)

    def __contains__(self, name: object) -> bool:
        # Python 3.14 forward references check membership before looking up a name.
        if not isinstance(name, str):
            return False
        try:
            self[name]
        except MissingEnvironmentNameError:
            return False
        return True


@dataclass(frozen=True)
class _RuntimeAccess:
    environment: StepEnvironment

    @staticmethod
    def inspect(value: object, *, cursor: str | None = None, max_items: int = 8) -> dict[str, object]:
        from .inspection import inspect_value

        return inspect_value(value, cursor=cursor, max_items=max_items)


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
        runtime_name_set: frozenset[str] = frozenset(),
    ) -> None:
        self._runtime_name_set = frozenset(runtime_name_set)
        self._python_name_to_value = {name: value for name, value in python_name_to_value.items() if not self._is_runtime_name(name)}
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
        known_names = (
            python_name_to_value.keys()
            | python_globals.keys()
            | self._python_builtin_name_to_value.keys()
            | self._local_name_set
            | self._python_name_to_cell.keys()
            | self.parent_name_to_value.keys()
            | self.task_name_to_value.keys()
            | self._runtime_name_set
        )
        suffix = 0
        while f"__nighthawk_runtime_{suffix}" in known_names:
            suffix += 1
        self.runtime_reference = f"__nighthawk_runtime_{suffix}"
        self._runtime_name_set = self._runtime_name_set | {self.runtime_reference}

    def _is_runtime_name(self, name: str) -> bool:
        return name in self._runtime_name_set

    @property
    def binding_name_to_type(self) -> Mapping[str, object]:
        """The contracts selected at step entry, independent of working assignments."""
        return MappingProxyType(self._binding_name_to_type)

    def has_python_name(self, name: str) -> bool:
        return not self._is_runtime_name(name) and (
            name in self._local_name_set
            or name in self._python_name_to_value
            or name in self._python_name_to_cell
            or name in self._python_global_name_to_value
            or name in self._python_builtin_name_to_value
        )

    def _resolve_python(self, name: str) -> object:
        if self._is_runtime_name(name):
            raise KeyError(name)
        if name in self._python_name_to_cell:
            try:
                return self._python_name_to_cell[name].cell_contents
            except ValueError:
                raise UnboundEnvironmentNameError(f"Python cell {name!r} is unbound") from None
        if name in self._python_name_to_value:
            return self._python_name_to_value[name]
        if name in self._local_name_set:
            raise UnboundEnvironmentNameError(f"Python local {name!r} is unbound")
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
        raise MissingEnvironmentNameError(f"Name {name!r} is absent from the {source} environment", name=name)

    def check_assignment(self, name: str) -> None:
        if name == self.runtime_reference or (self.has_python_name(name) and name not in self._output_name_set):
            raise ValueError(f"Cannot rebind Python name {name!r}; declare <:{name}> for Python writeback")

    def assign(self, name: str, value: object) -> None:
        self.check_assignment(name)
        self._step_name_to_value[name] = value

    def snapshot_working_state(self) -> dict[str, object]:
        snapshot = dict(self.parent_name_to_value)
        for name in self._python_name_to_value.keys() | self._local_name_set | self._python_name_to_cell.keys():
            if self._is_runtime_name(name):
                continue
            try:
                snapshot[name] = self._resolve_python(name)
            except (KeyError, UnboundLocalError):
                snapshot.pop(name, None)
        snapshot.update(self.step_name_to_value)
        return snapshot

    def expression_namespace(self, overrides: Mapping[str, object] | None = None) -> dict[str, object]:
        return _ExpressionNamespace(self, overrides or {})

    def reference(self, name: str, *, source: EnvironmentSource = "effective") -> str:
        """Return a Python expression resolving this binding when evaluated."""
        return f"{self.runtime_reference}.environment.resolve({name!r}, source={source!r})"

    def discover(self, *, source: EnvironmentSource = "effective", cursor: str | None = None, max_items: int = 8) -> dict[str, object]:
        """List names with provenance, usable references, and progressing pages."""
        source_to_view = {
            "python": self.python_name_to_value,
            "parent": self.parent_name_to_value,
            "task": self.task_name_to_value,
            "step": self.step_name_to_value,
        }
        if source != "effective" and source not in source_to_view:
            return {"error": "Unknown source", "recovery": "Choose effective, python, parent, task, or step."}
        names = (
            tuple(sorted(set().union(*[set(view) for view in source_to_view.values()])))
            if source == "effective"
            else tuple(sorted(source_to_view[source]))
        )
        page = page_names(names, source=source, cursor=cursor, max_items=max_items)
        if page.error is not None:
            return {"error": page.error, "recovery": f"Call {self.runtime_reference}.environment.discover(source={source!r}) without a cursor."}
        description: dict[str, object] = {
            "source": source,
            "names": [self.describe(name, source=source) for name in page.names],
            "next_cursor": page.next_cursor,
        }
        if page.next_cursor is not None:
            description["continuation"] = (
                f"{self.runtime_reference}.environment.discover(source={source!r}, cursor={page.next_cursor!r}, max_items={max_items})"
            )
        return DiscoveryResult(
            description, item_count=len(page.names), repage=lambda count: self.discover(source=source, cursor=cursor, max_items=count)
        )

    def describe(self, name: str, *, source: EnvironmentSource = "effective") -> dict[str, object]:
        """Describe a named binding; annotations are presentation, not validation."""
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
        origin = sources[0] if sources and source == "effective" else source if source in sources else None
        unbound = False
        if origin == "python":
            try:
                self._resolve_python(name)
            except UnboundLocalError:
                unbound = True
        is_python_binding = origin == "python" or source in ("effective", "python", "step") and name in self._output_name_set
        annotation = self._binding_name_to_annotation.get(name, MISSING_ANNOTATION) if is_python_binding else MISSING_ANNOTATION
        contract = self._binding_name_to_type.get(name, MISSING_ANNOTATION) if is_python_binding else MISSING_ANNOTATION
        return {
            "name": name,
            "origin": origin,
            "shadowed_sources": sources[1:],
            "unbound": unbound,
            "write_eligible": source in ("effective", "step")
            and name != self.runtime_reference
            and (name in self._output_name_set or not self.has_python_name(name)),
            "annotation": None if annotation is MISSING_ANNOTATION else annotation_text(annotation),
            "output_contract": None if contract is MISSING_ANNOTATION else annotation_text(contract),
            "reference": self.reference(name, source=source),
            "source_references": [self.reference(name, source=selected_source) for selected_source in sources],  # type: ignore[arg-type]
        }
