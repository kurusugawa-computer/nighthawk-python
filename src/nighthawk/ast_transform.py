from __future__ import annotations

import ast

from .errors import NaturalParseError


class NaturalTransformer(ast.NodeTransformer):
    """Rewrite Natural blocks into runtime calls plus explicit assignments.

    For an inline Natural block (an expression statement containing a string literal):

        <string literal whose first logical line is exactly 'natural'>

    rewrite to:

        __nh_outputs__ = __nighthawk_runtime__.run_block(program_text, ['x', 'y'])
        x = __nh_outputs__.get('x', x)
        y = __nh_outputs__.get('y', y)

    This yields real Python assignments (reliable), instead of attempting to mutate frame locals.

    For a Natural docstring:

        def f():
            <docstring whose first logical line is exactly 'natural'>
            ...

    rewrite by removing the docstring statement and inserting an equivalent runtime call at
    the beginning of the function.

    The runtime object is injected into globals when compiling.
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if not node.body:
            return node

        first_stmt = node.body[0]
        if isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Constant) and isinstance(first_stmt.value.value, str):
            doc = first_stmt.value.value
            if _is_natural_sentinel(doc):
                program = _extract_program(doc)
                output_names = _extract_output_names(program)
                injected = _build_runtime_call_and_assignments(program, output_names)

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

    def visit_Expr(self, node: ast.Expr) -> ast.AST:
        self.generic_visit(node)
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            s = value.value
            if _is_natural_sentinel(s):
                program = _extract_program(s)
                output_names = _extract_output_names(program)
                stmts = _build_runtime_call_and_assignments(program, output_names)
                return [ast.copy_location(stmt, node) for stmt in stmts]
        return node


def _build_runtime_call_and_assignments(program: str, output_names: tuple[str, ...]) -> list[ast.stmt]:
    outputs_var = ast.Name(id="__nh_outputs__", ctx=ast.Store())
    call_expr = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="__nighthawk_runtime__", ctx=ast.Load()),
            attr="run_block",
            ctx=ast.Load(),
        ),
        args=[
            ast.Constant(program),
            ast.List(elts=[ast.Constant(n) for n in output_names], ctx=ast.Load()),
        ],
        keywords=[],
    )
    assign_outputs = ast.Assign(targets=[outputs_var], value=call_expr)

    assigns: list[ast.stmt] = [assign_outputs]

    for name in output_names:
        # if 'name' in __nh_outputs__:
        #     name = __nh_outputs__['name']
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
