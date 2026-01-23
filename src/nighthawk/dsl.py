from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .core import NaturalParseError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BINDING_RE = re.compile(r"<(:?)([A-Za-z_][A-Za-z0-9_]*)>")


@dataclass(frozen=True)
class NaturalBlock:
    kind: str  # 'docstring' | 'inline'
    text: str
    input_bindings: tuple[str, ...]
    bindings: tuple[str, ...]
    lineno: int


def is_natural_sentinel(text: str) -> bool:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i] == "":
        i += 1
    if i >= len(lines):
        return False
    return lines[i] == "natural"


def extract_program(text: str) -> str:
    if not is_natural_sentinel(text):
        raise NaturalParseError("Missing natural sentinel")
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i] == "":
        i += 1
    return "\n".join(lines[i + 1 :])


def extract_bindings(program: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    inputs: list[str] = []
    outputs: list[str] = []
    for match in _BINDING_RE.finditer(program):
        is_output = match.group(1) == ":"
        name = match.group(2)
        if not _IDENTIFIER_RE.match(name):
            raise NaturalParseError(f"Invalid binding name: {name!r}")
        if is_output:
            outputs.append(name)
        else:
            inputs.append(name)

    def deduplicate(names: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        return tuple(ordered)

    return deduplicate(inputs), deduplicate(outputs)


def find_natural_blocks(func_source: str) -> tuple[NaturalBlock, ...]:
    """Parse function source text and return Natural blocks (docstring + inline)."""

    try:
        module = ast.parse(func_source)
    except SyntaxError as e:
        raise NaturalParseError(str(e)) from e

    blocks: list[NaturalBlock] = []

    func_def: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_def = node
            break
    if func_def is None:
        raise NaturalParseError("No function definition found")

    doc = ast.get_docstring(func_def, clean=False)
    if doc and is_natural_sentinel(doc):
        program = extract_program(doc)
        input_bindings, output_bindings = extract_bindings(program)
        blocks.append(
            NaturalBlock(
                kind="docstring",
                text=program,
                input_bindings=input_bindings,
                bindings=output_bindings,
                lineno=getattr(func_def, "lineno", 1),
            )
        )

    start_index = 1 if func_def.body and isinstance(func_def.body[0], ast.Expr) and isinstance(getattr(func_def.body[0], "value", None), ast.Constant) and isinstance(getattr(getattr(func_def.body[0], "value", None), "value", None), str) else 0

    for statement in func_def.body[start_index:]:
        if not isinstance(statement, ast.Expr):
            continue
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            text = value.value
            if is_natural_sentinel(text):
                program = extract_program(text)
                input_bindings, output_bindings = extract_bindings(program)
                blocks.append(
                    NaturalBlock(
                        kind="inline",
                        text=program,
                        input_bindings=input_bindings,
                        bindings=output_bindings,
                        lineno=getattr(statement, "lineno", 1),
                    )
                )

    return tuple(blocks)


class NaturalTransformer(ast.NodeTransformer):
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
                first_statement = node.body[0]
                if isinstance(first_statement, ast.Expr) and isinstance(first_statement.value, ast.Constant) and isinstance(first_statement.value.value, str):
                    doc = first_statement.value.value
                    if is_natural_sentinel(doc):
                        program = extract_program(doc)
                        _input_bindings, output_bindings = extract_bindings(program)
                        return_annotation = self._current_return_annotation_expression()
                        injected = build_runtime_call_and_assignments(
                            program,
                            output_bindings,
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
                is_in_loop = self._loop_depth > 0
                statements = build_runtime_call_and_assignments(
                    program,
                    output_bindings,
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


def build_runtime_call_and_assignments(
    program: str,
    binding_names: tuple[str, ...],
    return_annotation: ast.expr,
    *,
    is_in_loop: bool,
) -> list[ast.stmt]:
    envelope_variable = ast.Name(id="__nh_envelope__", ctx=ast.Store())
    call_expression = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="__nighthawk_runtime__", ctx=ast.Load()),
            attr="run_block",
            ctx=ast.Load(),
        ),
        args=[
            ast.Constant(program),
            ast.List(elts=[ast.Constant(name) for name in binding_names], ctx=ast.Load()),
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
