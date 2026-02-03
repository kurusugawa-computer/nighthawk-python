from __future__ import annotations

import ast

from ..errors import NaturalParseError
from .blocks import extract_bindings, extract_program, is_natural_sentinel


class NaturalTransformer(ast.NodeTransformer):
    def __init__(self, *, captured_name_tuple: tuple[str, ...]) -> None:
        super().__init__()
        self._captured_name_tuple = captured_name_tuple
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
                        input_bindings, output_bindings = extract_bindings(program)
                        return_annotation = self._current_return_annotation_expression()
                        binding_types_dict_expression = self._current_binding_types_dict_expression(output_bindings)
                        injected = build_runtime_call_and_assignments(
                            program,
                            input_bindings,
                            output_bindings,
                            binding_types_dict_expression,
                            return_annotation,
                            is_in_loop=self._loop_depth > 0,
                        )

                        body_without_docstring = node.body[1:]
                        node.body = injected + body_without_docstring

            node = self.generic_visit(node)  # type: ignore[assignment]

            if self._captured_name_tuple:
                anchor_name = "__nh_cell_anchor__"
                name_to_cell_name = "__nh_name_to_cell__"

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

                freevars_expression = ast.Attribute(
                    value=ast.Attribute(value=ast.Name(id=anchor_name, ctx=ast.Load()), attr="__code__", ctx=ast.Load()),
                    attr="co_freevars",
                    ctx=ast.Load(),
                )

                closure_expression = ast.BoolOp(
                    op=ast.Or(),
                    values=[
                        ast.Attribute(value=ast.Name(id=anchor_name, ctx=ast.Load()), attr="__closure__", ctx=ast.Load()),
                        ast.Tuple(elts=[], ctx=ast.Load()),
                    ],
                )

                name_to_cell_value = ast.Call(
                    func=ast.Name(id="dict", ctx=ast.Load()),
                    args=[
                        ast.Call(
                            func=ast.Name(id="zip", ctx=ast.Load()),
                            args=[freevars_expression, closure_expression],
                            keywords=[],
                        )
                    ],
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
                                func=ast.Name(id="__nh_python_cell_scope__", ctx=ast.Load()),
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

    def visit_Expr(self, node: ast.Expr) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            text = value.value
            if is_natural_sentinel(text):
                program = extract_program(text)
                input_bindings, output_bindings = extract_bindings(program)
                return_annotation = self._current_return_annotation_expression()
                binding_types_dict_expression = self._current_binding_types_dict_expression(output_bindings)
                is_in_loop = self._loop_depth > 0
                statements = build_runtime_call_and_assignments(
                    program,
                    input_bindings,
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
    input_binding_names: tuple[str, ...],
    output_binding_names: tuple[str, ...],
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
            ast.List(elts=[ast.Constant(name) for name in input_binding_names], ctx=ast.Load()),
            ast.List(elts=[ast.Constant(name) for name in output_binding_names], ctx=ast.Load()),
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

    for name in output_binding_names:
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


def transform_function_source(func_source: str, *, captured_name_tuple: tuple[str, ...] = ()) -> str:
    """Return a rewritten module source with Natural blocks rewritten."""

    try:
        module = ast.parse(func_source)
    except SyntaxError as e:
        raise NaturalParseError(str(e)) from e

    module = NaturalTransformer(captured_name_tuple=captured_name_tuple).visit(module)  # type: ignore[assignment]
    ast.fix_missing_locations(module)

    try:
        return ast.unparse(module)
    except Exception as e:
        raise NaturalParseError(f"Failed to unparse transformed AST: {e}") from e
