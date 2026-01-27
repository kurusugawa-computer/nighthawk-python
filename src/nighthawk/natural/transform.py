from __future__ import annotations

import ast

from ..errors import NaturalParseError
from .blocks import extract_bindings, extract_program, is_natural_sentinel


class NaturalTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        super().__init__()
        self._return_annotation_stack: list[ast.expr | None] = []
        self._binding_name_to_type_expression_stack: list[dict[str, ast.expr]] = []
        self._loop_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self._return_annotation_stack.append(node.returns)
        self._binding_name_to_type_expression_stack.append(self._collect_binding_name_to_type_expression(node))
        saved_loop_depth = self._loop_depth
        self._loop_depth = 0
        try:
            if node.body:
                first_statement = node.body[0]
                if isinstance(first_statement, ast.Expr) and isinstance(first_statement.value, ast.Constant) and isinstance(first_statement.value.value, str):
                    doc = first_statement.value.value
                    if is_natural_sentinel(doc):
                        program = extract_program(doc)
                        _input_bindings, output_bindings = extract_bindings(program)
                        return_annotation = self._current_return_annotation_expression()
                        binding_types_dict_expression = self._current_binding_types_dict_expression(output_bindings)
                        injected = build_runtime_call_and_assignments(
                            program,
                            output_bindings,
                            binding_types_dict_expression,
                            return_annotation,
                            is_in_loop=self._loop_depth > 0,
                        )

                        body_without_docstring = node.body[1:]

                        if output_bindings:
                            assigned: set[str] = set()

                            def note_assigned(statement: ast.stmt) -> None:
                                if isinstance(statement, ast.Assign):
                                    for target in statement.targets:
                                        if isinstance(target, ast.Name):
                                            assigned.add(target.id)
                                elif isinstance(statement, ast.AnnAssign):
                                    target = statement.target
                                    if isinstance(target, ast.Name):
                                        assigned.add(target.id)
                                elif isinstance(statement, ast.AugAssign):
                                    target = statement.target
                                    if isinstance(target, ast.Name):
                                        assigned.add(target.id)

                            insert_at = 0
                            for i, statement in enumerate(body_without_docstring):
                                note_assigned(statement)
                                if set(output_bindings).issubset(assigned):
                                    insert_at = i + 1
                                    break

                            node.body = body_without_docstring[:insert_at] + injected + body_without_docstring[insert_at:]
                        else:
                            node.body = injected + body_without_docstring

            node = self.generic_visit(node)  # type: ignore[assignment]
            return node
        finally:
            self._binding_name_to_type_expression_stack.pop()
            self._return_annotation_stack.pop()
            self._loop_depth = saved_loop_depth

    def visit_For(self, node: ast.For) -> ast.AST:
        self._loop_depth += 1
        try:
            return self.generic_visit(node)
        finally:
            self._loop_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AST:
        self._loop_depth += 1
        try:
            return self.generic_visit(node)
        finally:
            self._loop_depth -= 1

    def visit_While(self, node: ast.While) -> ast.AST:
        self._loop_depth += 1
        try:
            return self.generic_visit(node)
        finally:
            self._loop_depth -= 1

    def visit_Expr(self, node: ast.Expr) -> ast.AST:
        self.generic_visit(node)
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            text = value.value
            if is_natural_sentinel(text):
                program = extract_program(text)
                _input_bindings, output_bindings = extract_bindings(program)
                return_annotation = self._current_return_annotation_expression()
                binding_types_dict_expression = self._current_binding_types_dict_expression(output_bindings)
                is_in_loop = self._loop_depth > 0
                statements = build_runtime_call_and_assignments(
                    program,
                    output_bindings,
                    binding_types_dict_expression,
                    return_annotation,
                    is_in_loop=is_in_loop,
                )
                return [ast.copy_location(statement, node) for statement in statements]  # type: ignore[return-value]
        return node

    def _current_return_annotation_expression(self) -> ast.expr:
        if not self._return_annotation_stack:
            return ast.Name(id="object", ctx=ast.Load())
        annotation = self._return_annotation_stack[-1]
        if annotation is None:
            return ast.Name(id="object", ctx=ast.Load())
        return annotation

    def _collect_binding_name_to_type_expression(self, node: ast.FunctionDef) -> dict[str, ast.expr]:
        binding_name_to_type_expression: dict[str, ast.expr] = {}

        for argument in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]:
            if argument.annotation is not None:
                binding_name_to_type_expression[argument.arg] = argument.annotation

        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                target = statement.target
                if isinstance(target, ast.Name):
                    binding_name_to_type_expression[target.id] = statement.annotation

        return binding_name_to_type_expression

    def _current_binding_types_dict_expression(self, binding_names: tuple[str, ...]) -> ast.expr:
        if not binding_names:
            return ast.Dict(keys=[], values=[])

        binding_name_to_type_expression: dict[str, ast.expr] = {}
        if self._binding_name_to_type_expression_stack:
            binding_name_to_type_expression = self._binding_name_to_type_expression_stack[-1]

        keys: list[ast.expr | None] = []
        values: list[ast.expr] = []
        for name in binding_names:
            keys.append(ast.Constant(name))
            values.append(binding_name_to_type_expression.get(name, ast.Name(id="object", ctx=ast.Load())))

        return ast.Dict(keys=keys, values=values)


def build_runtime_call_and_assignments(
    program: str,
    binding_names: tuple[str, ...],
    binding_types_dict_expression: ast.expr,
    return_annotation: ast.expr,
    *,
    is_in_loop: bool,
) -> list[ast.stmt]:
    envelope_variable = ast.Name(id="__nh_envelope__", ctx=ast.Store())
    call_expression = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="__nighthawk_orchestrator__", ctx=ast.Load()),
            attr="run_natural_block",
            ctx=ast.Load(),
        ),
        args=[
            ast.Constant(program),
            ast.List(elts=[ast.Constant(name) for name in binding_names], ctx=ast.Load()),
            binding_types_dict_expression,
            return_annotation,
            ast.Constant(is_in_loop),
        ],
        keywords=[],
    )
    assign_envelope = ast.Assign(targets=[envelope_variable], value=call_expression)

    assigns: list[ast.stmt] = [assign_envelope]

    assigns.append(
        ast.Assign(
            targets=[ast.Name(id="__nh_bindings__", ctx=ast.Store())],
            value=ast.Subscript(
                value=ast.Name(id="__nh_envelope__", ctx=ast.Load()),
                slice=ast.Constant("bindings"),
                ctx=ast.Load(),
            ),
        )
    )

    for name in binding_names:
        assigns.append(
            ast.If(
                test=ast.Compare(
                    left=ast.Constant(name),
                    ops=[ast.In()],
                    comparators=[ast.Name(id="__nh_bindings__", ctx=ast.Load())],
                ),
                body=[
                    ast.Assign(
                        targets=[ast.Name(id=name, ctx=ast.Store())],
                        value=ast.Subscript(
                            value=ast.Name(id="__nh_bindings__", ctx=ast.Load()),
                            slice=ast.Constant(name),
                            ctx=ast.Load(),
                        ),
                    )
                ],
                orelse=[],
            )
        )

    assigns.append(
        ast.Assign(
            targets=[ast.Name(id="__nh_final__", ctx=ast.Store())],
            value=ast.Subscript(
                value=ast.Name(id="__nh_envelope__", ctx=ast.Load()),
                slice=ast.Constant("execution_final"),
                ctx=ast.Load(),
            ),
        )
    )

    assigns.append(
        ast.Assign(
            targets=[ast.Name(id="__nh_effect__", ctx=ast.Store())],
            value=ast.Attribute(
                value=ast.Name(id="__nh_final__", ctx=ast.Load()),
                attr="effect",
                ctx=ast.Load(),
            ),
        )
    )

    effect_statements: list[ast.stmt] = [
        ast.If(
            test=ast.Compare(
                left=ast.Attribute(
                    value=ast.Name(id="__nh_effect__", ctx=ast.Load()),
                    attr="type",
                    ctx=ast.Load(),
                ),
                ops=[ast.Eq()],
                comparators=[ast.Constant("return")],
            ),
            body=[
                ast.Return(
                    value=ast.Subscript(
                        value=ast.Name(id="__nh_envelope__", ctx=ast.Load()),
                        slice=ast.Constant("effect_value"),
                        ctx=ast.Load(),
                    )
                )
            ],
            orelse=[],
        )
    ]

    if is_in_loop:
        effect_statements.extend(
            [
                ast.If(
                    test=ast.Compare(
                        left=ast.Attribute(
                            value=ast.Name(id="__nh_effect__", ctx=ast.Load()),
                            attr="type",
                            ctx=ast.Load(),
                        ),
                        ops=[ast.Eq()],
                        comparators=[ast.Constant("break")],
                    ),
                    body=[ast.Break()],
                    orelse=[],
                ),
                ast.If(
                    test=ast.Compare(
                        left=ast.Attribute(
                            value=ast.Name(id="__nh_effect__", ctx=ast.Load()),
                            attr="type",
                            ctx=ast.Load(),
                        ),
                        ops=[ast.Eq()],
                        comparators=[ast.Constant("continue")],
                    ),
                    body=[ast.Continue()],
                    orelse=[],
                ),
            ]
        )

    assigns.append(
        ast.If(
            test=ast.Compare(
                left=ast.Name(id="__nh_effect__", ctx=ast.Load()),
                ops=[ast.IsNot()],
                comparators=[ast.Constant(None)],
            ),
            body=effect_statements,
            orelse=[],
        )
    )

    return assigns


def transform_function_source(func_source: str) -> str:
    """Return a rewritten module source with Natural blocks rewritten."""

    try:
        module = ast.parse(func_source)
    except SyntaxError as e:
        raise NaturalParseError(str(e)) from e

    module = NaturalTransformer().visit(module)  # type: ignore[assignment]
    ast.fix_missing_locations(module)

    try:
        return ast.unparse(module)
    except Exception as e:
        raise NaturalParseError(f"Failed to unparse transformed AST: {e}") from e
