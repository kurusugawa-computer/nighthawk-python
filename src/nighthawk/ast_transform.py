from __future__ import annotations

import ast

from .errors import NaturalParseError


class NaturalTransformer(ast.NodeTransformer):
    """Rewrite Natural blocks into runtime calls plus explicit assignments.

    For an inline Natural block (an expression statement containing a string literal):

        <string literal whose first logical line is exactly 'natural'>

    rewrite to (schematic):

        __nh_envelope__ = __nighthawk_runtime__.run_block(program_text, ['x', 'y'], return_annotation, is_in_loop)
        __nh_outputs__ = __nh_envelope__['outputs']
        if 'x' in __nh_outputs__:
            x = __nh_outputs__['x']
        if 'y' in __nh_outputs__:
            y = __nh_outputs__['y']

        __nh_final__ = __nh_envelope__['natural_final']
        __nh_effect__ = __nh_final__.effect
        if __nh_effect__ is not None:
            if __nh_effect__.type == 'return':
                return __nh_envelope__['effect_value']
            if __nh_effect__.type == 'break':
                break
            if __nh_effect__.type == 'continue':
                continue

    This yields real Python assignments (reliable) and real Python control flow, instead
    of attempting to mutate frame locals.

    For a Natural docstring:

        def f():
            <docstring whose first logical line is exactly 'natural'>
            ...

    rewrite by removing the docstring statement and inserting an equivalent runtime call
    near the beginning of the function.

    The runtime object is injected into globals when compiling.
    """

    def __init__(self) -> None:
        super().__init__()
        self._return_annotation_stack: list[ast.expr | None] = []
        self._loop_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self._return_annotation_stack.append(node.returns)
        saved_loop_depth = self._loop_depth
        self._loop_depth = 0
        try:
            if node.body:
                first_stmt = node.body[0]
                if isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Constant) and isinstance(first_stmt.value.value, str):
                    doc = first_stmt.value.value
                    if _is_natural_sentinel(doc):
                        program = _extract_program(doc)
                        output_names = _extract_output_names(program)
                        return_annotation = self._current_return_annotation_expr()
                        injected = _build_runtime_call_and_assignments(program, output_names, return_annotation, is_in_loop=self._loop_depth > 0)

                        body_without_docstring = node.body[1:]

                        if output_names:
                            assigned: set[str] = set()

                            def note_assigned(stmt: ast.stmt) -> None:
                                if isinstance(stmt, ast.Assign):
                                    for t in stmt.targets:
                                        if isinstance(t, ast.Name):
                                            assigned.add(t.id)
                                elif isinstance(stmt, ast.AnnAssign):
                                    t = stmt.target
                                    if isinstance(t, ast.Name):
                                        assigned.add(t.id)
                                elif isinstance(stmt, ast.AugAssign):
                                    t = stmt.target
                                    if isinstance(t, ast.Name):
                                        assigned.add(t.id)

                            insert_at = 0
                            for i, stmt in enumerate(body_without_docstring):
                                note_assigned(stmt)
                                if set(output_names).issubset(assigned):
                                    insert_at = i + 1
                                    break

                            node.body = body_without_docstring[:insert_at] + injected + body_without_docstring[insert_at:]
                        else:
                            node.body = injected + body_without_docstring

            node = self.generic_visit(node)  # type: ignore[assignment]
            return node
        finally:
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
            source = value.value
            if _is_natural_sentinel(source):
                program = _extract_program(source)
                output_names = _extract_output_names(program)
                return_annotation = self._current_return_annotation_expr()
                is_in_loop = self._loop_depth > 0
                stmts = _build_runtime_call_and_assignments(program, output_names, return_annotation, is_in_loop=is_in_loop)
                return [ast.copy_location(stmt, node) for stmt in stmts]  # type: ignore[return-value]
        return node

    def _current_return_annotation_expr(self) -> ast.expr:
        if not self._return_annotation_stack:
            return ast.Name(id="object", ctx=ast.Load())
        annotation = self._return_annotation_stack[-1]
        if annotation is None:
            return ast.Name(id="object", ctx=ast.Load())
        return annotation


def _build_runtime_call_and_assignments(
    program: str,
    output_names: tuple[str, ...],
    return_annotation: ast.expr,
    *,
    is_in_loop: bool,
) -> list[ast.stmt]:
    envelope_var = ast.Name(id="__nh_envelope__", ctx=ast.Store())
    call_expr = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="__nighthawk_runtime__", ctx=ast.Load()),
            attr="run_block",
            ctx=ast.Load(),
        ),
        args=[
            ast.Constant(program),
            ast.List(elts=[ast.Constant(n) for n in output_names], ctx=ast.Load()),
            return_annotation,
            ast.Constant(is_in_loop),
        ],
        keywords=[],
    )
    assign_envelope = ast.Assign(targets=[envelope_var], value=call_expr)

    assigns: list[ast.stmt] = [assign_envelope]

    assigns.append(
        ast.Assign(
            targets=[ast.Name(id="__nh_outputs__", ctx=ast.Store())],
            value=ast.Subscript(
                value=ast.Name(id="__nh_envelope__", ctx=ast.Load()),
                slice=ast.Constant("outputs"),
                ctx=ast.Load(),
            ),
        )
    )

    for name in output_names:
        assigns.append(
            ast.If(
                test=ast.Compare(
                    left=ast.Constant(name),
                    ops=[ast.In()],
                    comparators=[ast.Name(id="__nh_outputs__", ctx=ast.Load())],
                ),
                body=[
                    ast.Assign(
                        targets=[ast.Name(id=name, ctx=ast.Store())],
                        value=ast.Subscript(
                            value=ast.Name(id="__nh_outputs__", ctx=ast.Load()),
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
                slice=ast.Constant("natural_final"),
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


def _is_natural_sentinel(text: str) -> bool:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i] == "":
        i += 1
    if i >= len(lines):
        return False
    return lines[i] == "natural"


def _extract_program(text: str) -> str:
    if not _is_natural_sentinel(text):
        raise NaturalParseError("Missing natural sentinel")
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i] == "":
        i += 1
    return "\n".join(lines[i + 1 :])


def _extract_output_names(program: str) -> tuple[str, ...]:
    out: list[str] = []
    i = 0
    while True:
        start = program.find("<", i)
        if start == -1:
            break
        end = program.find(">", start + 1)
        if end == -1:
            break
        token = program[start + 1 : end]
        if token.startswith(":"):
            name = token[1:]
            if name.isidentifier() and name not in out:
                out.append(name)
        i = end + 1
    return tuple(out)


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
