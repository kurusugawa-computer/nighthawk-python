"""Operator source adoption through observed callable identity.

Approval, successful materialization, and run admission remain separate facts.
These host decisions govern source identity, not reversible Python mutation.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from nighthawk import get_function_definition

from .._call_contracts import build_value_adapter
from .._host import current_host
from ..errors import CapabilityRejectedError, RunRejectedError


@dataclass(frozen=True)
class IncludedSourceApproval:
    path: Path
    source: str
    approval_identity: str


@dataclass(frozen=True)
class RatifiedFunctionSource:
    function: Callable[..., object]
    source: str
    approval_identity: str
    included_source_approval_tuple: tuple[IncludedSourceApproval, ...] = ()


@dataclass(frozen=True)
class _ApprovedSource:
    source: str
    source_identity: str
    approval_identity: str
    route: Literal["operator_submission", "trusted_import_registration"]
    cell_id: str | None
    included_source_approval_tuple: tuple[IncludedSourceApproval, ...]


def require_operator(action: str) -> None:
    if current_host() is not None:
        raise CapabilityRejectedError(capability_name=action, reason_code="host_control_required")


def verify_included_sources(approvals: tuple[IncludedSourceApproval, ...]) -> None:
    for approval in approvals:
        if approval.path.resolve().read_text() != approval.source:
            raise RunRejectedError(reason="invalid_declaration", detail="included source differs from its approved snapshot")


class _SourceRegistry:
    def __init__(
        self,
        registrations: tuple[RatifiedFunctionSource, ...],
        included_source_approval_tuple: tuple[IncludedSourceApproval, ...],
        function_to_result_type: Mapping[Callable[..., object], object] | None,
    ) -> None:
        self.function_to_approval: dict[Callable[..., object], _ApprovedSource] = {}
        self.approval_list: list[_ApprovedSource] = []
        self.included_source_approval_tuple = included_source_approval_tuple
        self.function_to_result_type = dict(function_to_result_type or {})
        for registration in registrations:
            verify_included_sources(registration.included_source_approval_tuple)
            approval = self._approval(
                registration.source,
                registration.approval_identity,
                "trusted_import_registration",
                None,
                registration.included_source_approval_tuple,
            )
            self.function_to_approval[registration.function] = approval

    def _approval(
        self,
        source: str,
        identity: str,
        route: Literal["operator_submission", "trusted_import_registration"],
        cell_id: str | None,
        includes: tuple[IncludedSourceApproval, ...],
    ) -> _ApprovedSource:
        approval = _ApprovedSource(source, hashlib.sha256(source.encode()).hexdigest(), identity, route, cell_id, includes)
        self.approval_list.append(approval)
        return approval

    def instrument(
        self,
        module: ast.Module,
        source: str,
        cell_id: str,
        binding_name_to_value: dict[str, Any],
    ) -> tuple[str, ...]:
        require_operator("source_adoption")
        helper_name_list: list[str] = []
        lines = source.splitlines(keepends=True)
        registry = self

        class Observer(ast.NodeTransformer):
            def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.GeneratorExp:
                # Lazy bodies can run after the cell's observation helpers expire.
                return node

            def visit_Lambda(self, node: ast.Lambda) -> ast.expr:
                # Observe the expression's exact result, before assignment or aliasing.
                # Bodies belong to later calls, so do not instrument nested definitions.
                approval = registry._approval(
                    ast.get_source_segment(source, node) or source,
                    cell_id,
                    "operator_submission",
                    cell_id,
                    (),
                )

                def observe_lambda(function: Callable[..., object]) -> Callable[..., object]:
                    require_operator("source_adoption")
                    registry.function_to_approval[function] = approval
                    return function

                name = f"__nighthawk_lambda_{cell_id}_{len(helper_name_list)}"
                binding_name_to_value[name] = observe_lambda
                helper_name_list.append(name)
                return ast.copy_location(ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=[node], keywords=[]), node)

            def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
                return node

            def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
                observe(node)
                return node

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
                observe(node)
                return node

        def observe(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            verify_included_sources(registry.included_source_approval_tuple)
            first_line = min(node.lineno, *(decorator.lineno for decorator in node.decorator_list)) if node.decorator_list else node.lineno
            approval = registry._approval(
                "".join(lines[first_line - 1 : node.end_lineno]),
                cell_id,
                "operator_submission",
                cell_id,
                registry.included_source_approval_tuple,
            )
            original: Callable[..., object] | None = None

            def observe_original(function: Callable[..., object]) -> Callable[..., object]:
                nonlocal original
                require_operator("source_adoption")
                original = function
                return function

            def observe_result(function: object) -> object:
                require_operator("source_adoption")
                definition = get_function_definition(function) if callable(function) else None
                if callable(function) and (function is original or (definition is not None and definition.original_function is original)):
                    registry.function_to_approval[function] = approval
                return function

            original_name = f"__nighthawk_original_{cell_id}_{len(helper_name_list)}"
            result_name = f"__nighthawk_materialized_{cell_id}_{len(helper_name_list)}"
            binding_name_to_value[original_name] = observe_original
            binding_name_to_value[result_name] = observe_result
            helper_name_list.extend((original_name, result_name))
            location = node.decorator_list[0] if node.decorator_list else node
            node.decorator_list.insert(0, ast.copy_location(ast.Name(id=result_name, ctx=ast.Load()), location))
            node.decorator_list.append(ast.copy_location(ast.Name(id=original_name, ctx=ast.Load()), location))

        Observer().visit(module)
        return tuple(helper_name_list)

    def result_type(self, function: Callable[..., object]) -> Any:
        if function not in self.function_to_approval:
            raise RunRejectedError(reason="invalid_declaration", detail="Natural function has no approved source/callable association")
        try:
            if function in self.function_to_result_type:
                declaration = self.function_to_result_type[function]
            else:
                definition = get_function_definition(function)
                original = function if definition is None else definition.original_function
                return_annotation = inspect.signature(original).return_annotation
                if return_annotation is inspect.Signature.empty:
                    raise ValueError("missing return annotation")
                declaration = typing.get_type_hints(original, include_extras=True)["return"]
            build_value_adapter(declaration)
        except Exception as error:
            raise RunRejectedError(reason="invalid_declaration", detail=f"invalid Natural result declaration: {type(error).__name__}") from error
        return declaration

    def approval_for(self, value: object) -> _ApprovedSource | None:
        # Callable instances may define arbitrary equality/hash methods.
        # Correspondence is exact identity, never Python equality.
        return next((approval for function, approval in self.function_to_approval.items() if function is value), None)

    def dispose(self) -> None:
        self.function_to_approval.clear()
        self.function_to_result_type.clear()
        self.approval_list.clear()
