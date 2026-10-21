from __future__ import annotations

import ast

from ..runtime.step_definition import BlockKind
from ._names import _RuntimeNames
from .blocks import (
    _joined_string_is_natural_sentinel,
    is_natural_sentinel,
    parse_block,
    parse_joined_block,
)


def _copy_generated_location(statement: ast.stmt, source: ast.AST) -> ast.stmt:
    for child in ast.walk(statement):
        ast.copy_location(child, source)
    return statement


class NaturalTransformer(ast.NodeTransformer):
    def __init__(self, *, captured_name_tuple: tuple[str, ...], runtime_names: _RuntimeNames) -> None:
        super().__init__()
        self._runtime_names = runtime_names
        self._captured_name_tuple = captured_name_tuple
        self._return_annotation_stack: list[ast.expr | None] = []
        self._binding_name_to_type_expression_stack: list[dict[str, list[ast.expr]]] = []
        self._variadic_parameter_name_to_kind_stack: list[dict[str, str]] = []
        self._is_async_function_stack: list[bool] = []
        self._loop_depth = 0

    def _visit_function_like(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
        if self._is_async_function_stack:
            # A nested definition owns its source and decorators independently.
            return node
        self._return_annotation_stack.append(node.returns)
        self._binding_name_to_type_expression_stack.append(self._collect_binding_name_to_type_expression(node))
        self._variadic_parameter_name_to_kind_stack.append(
            {argument.arg: kind for argument, kind in ((node.args.vararg, "positional"), (node.args.kwarg, "keyword")) if argument is not None}
        )
        if node.args.vararg is not None and isinstance(node.args.vararg.annotation, ast.Starred):
            self._variadic_parameter_name_to_kind_stack[-1][node.args.vararg.arg] = "unpacked"
        self._is_async_function_stack.append(isinstance(node, ast.AsyncFunctionDef))
        saved_loop_depth = self._loop_depth
        self._loop_depth = 0
        try:
            node = self.generic_visit(node)  # type: ignore[assignment]

            anchor_name = self._runtime_names.allocate("__nh_cell_anchor__")
            name_to_cell_name = self._runtime_names.allocate("__nh_name_to_cell__")

            anchor_body: list[ast.stmt] = [
                ast.Return(
                    value=ast.Tuple(
                        elts=[ast.Name(id=name, ctx=ast.Load()) for name in self._captured_name_tuple],
                        ctx=ast.Load(),
                    )
                )
            ]

            anchor_function = ast.FunctionDef(
                name=anchor_name,
                args=ast.arguments(
                    posonlyargs=[],
                    args=[],
                    kwonlyargs=[],
                    kw_defaults=[],
                    defaults=[],
                ),
                body=anchor_body,
                decorator_list=[],
                returns=None,
                type_comment=None,
            )

            name_to_cell_value = ast.Call(
                func=ast.Name(id=self._runtime_names.allocate("__nh_function_name_to_cell__"), ctx=ast.Load()),
                args=[ast.Name(id=anchor_name, ctx=ast.Load())],
                keywords=[],
            )

            name_to_cell_assign = ast.Assign(
                targets=[ast.Name(id=name_to_cell_name, ctx=ast.Store())],
                value=name_to_cell_value,
            )

            with_statement = ast.With(
                items=[
                    ast.withitem(
                        context_expr=ast.Call(
                            func=ast.Name(id=self._runtime_names.allocate("__nh_python_cell_scope__"), ctx=ast.Load()),
                            args=[ast.Name(id=name_to_cell_name, ctx=ast.Load())],
                            keywords=[],
                        ),
                        optional_vars=None,
                    )
                ],
                body=node.body,
                type_comment=None,
            )

            node.body = [anchor_function, name_to_cell_assign, with_statement]
            return node
        finally:
            self._is_async_function_stack.pop()
            self._binding_name_to_type_expression_stack.pop()
            self._variadic_parameter_name_to_kind_stack.pop()
            self._return_annotation_stack.pop()
            self._loop_depth = saved_loop_depth

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._visit_function_like(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:  # noqa: N802
        return self._visit_function_like(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:  # noqa: N802
        return node

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> ast.AST:
        else_module = ast.Module(body=node.orelse, type_ignores=[])
        node.orelse = []
        self._loop_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._loop_depth -= 1
        # A loop's else clause can control an enclosing loop, not its own loop.
        self.generic_visit(else_module)
        node.orelse = else_module.body
        return node

    def visit_For(self, node: ast.For) -> ast.AST:
        return self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AST:
        return self._visit_loop(node)

    def visit_While(self, node: ast.While) -> ast.AST:
        return self._visit_loop(node)

    def visit_Expr(self, node: ast.Expr) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        value = node.value

        if isinstance(value, ast.Constant) and isinstance(value.value, str) and is_natural_sentinel(value.value):
            block_kind, program, input_bindings, output_bindings = parse_block(value.value)
            program_expression: ast.expr = ast.Constant(program)
        elif isinstance(value, ast.JoinedStr) and _joined_string_is_natural_sentinel(value):
            block_kind, _, input_bindings, output_bindings = parse_joined_block(value)
            program_expression = ast.Call(
                func=ast.Name(id=self._runtime_names.allocate("__nh_extract_program__"), ctx=ast.Load()),
                args=[value],
                keywords=[],
            )
        else:
            return node

        statements = build_runtime_call_and_assignments(
            program_expression,
            input_bindings,
            output_bindings,
            self._current_binding_types_dict_expression(output_bindings),
            self._current_return_annotation_expression(),
            is_in_loop=self._loop_depth > 0,
            is_async_function=self._is_async_function_stack[-1] if self._is_async_function_stack else False,
            runtime_names=self._runtime_names,
            block_kind=block_kind,
        )

        sentinel_location = ast.copy_location(ast.Pass(), node)
        sentinel_location.end_lineno = sentinel_location.lineno
        sentinel_location.end_col_offset = sentinel_location.col_offset
        return [_copy_generated_location(statement, sentinel_location) for statement in statements]

    def _current_return_annotation_expression(self) -> ast.expr:
        if not self._return_annotation_stack:
            return ast.Name(id=self._runtime_names.allocate("__nh_missing_annotation__"), ctx=ast.Load())
        annotation = self._return_annotation_stack[-1]
        if annotation is None:
            return ast.Name(id=self._runtime_names.allocate("__nh_missing_annotation__"), ctx=ast.Load())
        return self._source_annotation_expression([annotation])

    def _source_annotation_expression(self, annotations: list[ast.expr], *, parameter_kind: str | None = None) -> ast.expr:
        return ast.Call(
            func=ast.Name(id=self._runtime_names.allocate("__nh_source_annotation__"), ctx=ast.Load()),
            args=[ast.Tuple(elts=[ast.Constant(ast.unparse(annotation)) for annotation in annotations], ctx=ast.Load())],
            keywords=[ast.keyword(arg="parameter_kind", value=ast.Constant(parameter_kind))] if parameter_kind is not None else [],
        )

    def _collect_binding_name_to_type_expression(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, list[ast.expr]]:
        binding_name_to_type_expression: dict[str, list[ast.expr]] = {}

        for argument in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *([node.args.vararg] if node.args.vararg else []),
            *([node.args.kwarg] if node.args.kwarg else []),
        ]:
            if argument.annotation is not None:
                annotation = argument.annotation.value if isinstance(argument.annotation, ast.Starred) else argument.annotation
                binding_name_to_type_expression[argument.arg] = [annotation]

        class AnnotationVisitor(ast.NodeVisitor):
            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
                if isinstance(node.target, ast.Name):
                    binding_name_to_type_expression.setdefault(node.target.id, []).append(node.annotation)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
                pass

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
                pass

            def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
                pass

        visitor = AnnotationVisitor()
        for statement in node.body:
            visitor.visit(statement)

        return binding_name_to_type_expression

    def _current_binding_types_dict_expression(self, binding_names: tuple[str, ...]) -> ast.expr:
        binding_name_to_type_expression: dict[str, list[ast.expr]] = {}
        if self._binding_name_to_type_expression_stack:
            binding_name_to_type_expression = self._binding_name_to_type_expression_stack[-1]

        keys: list[ast.expr | None] = []
        values: list[ast.expr] = []
        for name in dict.fromkeys((*binding_names, *binding_name_to_type_expression)):
            keys.append(ast.Constant(name))
            annotations = binding_name_to_type_expression.get(name)
            values.append(
                self._source_annotation_expression(
                    annotations,
                    parameter_kind=self._variadic_parameter_name_to_kind_stack[-1].get(name) if self._variadic_parameter_name_to_kind_stack else None,
                )
                if annotations
                else ast.Name(id=self._runtime_names.allocate("__nh_missing_annotation__"), ctx=ast.Load())
            )

        return ast.Dict(keys=keys, values=values)


def build_runtime_call_and_assignments(
    natural_program_expression: ast.expr,
    input_binding_names: tuple[str, ...],
    output_binding_names: tuple[str, ...],
    binding_types_dict_expression: ast.expr,
    return_annotation: ast.expr,
    *,
    is_in_loop: bool,
    is_async_function: bool,
    runtime_names: _RuntimeNames | None = None,
    block_kind: BlockKind = "execute",
) -> list[ast.stmt]:
    if runtime_names is None:
        runtime_names = _RuntimeNames(set(input_binding_names) | set(output_binding_names))
    execution_name = runtime_names.allocate("__nh_execution__")
    envelope_name = runtime_names.allocate("__nh_envelope__")
    bindings_name = runtime_names.allocate("__nh_bindings__")
    runner_name = runtime_names.allocate("__nighthawk_runner__")
    outcome_name = runtime_names.allocate("__nh_step_outcome__")
    method_name = "run_step_async" if is_async_function else "run_step"
    call_expression = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=execution_name, ctx=ast.Load()),
            attr=method_name,
            ctx=ast.Load(),
        ),
        args=[
            natural_program_expression,
            ast.List(elts=[ast.Constant(name) for name in input_binding_names], ctx=ast.Load()),
            ast.List(elts=[ast.Constant(name) for name in output_binding_names], ctx=ast.Load()),
            binding_types_dict_expression,
            return_annotation,
            ast.Constant(is_in_loop),
        ],
        keywords=[],
    )
    envelope_value: ast.expr = ast.Await(value=call_expression) if is_async_function else call_expression

    statements: list[ast.stmt] = ast.parse(f'{envelope_name} = None\n{bindings_name} = {envelope_name}["bindings"]\n').body
    statements[0].value = envelope_value  # type: ignore[attr-defined]

    for name in output_binding_names:
        statements.extend(
            ast.parse(
                f'if "{name}" in {bindings_name}:\n    {name} = {bindings_name}["{name}"]\n    {execution_name}.record_assignment("{name}", {name})\n'
            ).body
        )

    guard_source = (
        f"async with {runner_name}.execution_async(block_kind={block_kind!r}) as {execution_name}:\n    pass\n"
        if is_async_function
        else f"with {runner_name}.execution(block_kind={block_kind!r}) as {execution_name}:\n    pass\n"
    )
    guarded = ast.parse(guard_source).body[0]
    assert isinstance(guarded, (ast.With, ast.AsyncWith))
    guarded.body = statements
    statements = [guarded]

    statements.extend(ast.parse(f'{outcome_name} = {envelope_name}["outcome"]').body)

    outcome_source = f'if {outcome_name} is not None:\n    if {outcome_name}.kind == "return":\n        return {outcome_name}.value\n'
    if is_in_loop:
        outcome_source += f'    if {outcome_name}.kind == "break":\n        break\n    if {outcome_name}.kind == "continue":\n        continue\n'
    statements.extend(ast.parse(outcome_source).body)

    return statements


def transform_module_ast(module: ast.Module, *, captured_name_tuple: tuple[str, ...] = (), runtime_names: _RuntimeNames | None = None) -> ast.Module:
    if runtime_names is None:
        runtime_names = _RuntimeNames({node.id for node in ast.walk(module) if isinstance(node, ast.Name)} | set(captured_name_tuple))
    module = NaturalTransformer(captured_name_tuple=captured_name_tuple, runtime_names=runtime_names).visit(module)  # type: ignore[assignment]
    ast.fix_missing_locations(module)
    return module
