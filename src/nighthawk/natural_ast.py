from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .errors import NaturalParseError


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BINDING_RE = re.compile(r"<(:?)([A-Za-z_][A-Za-z0-9_]*)>")


@dataclass(frozen=True)
class NaturalBlock:
    kind: str  # 'docstring' | 'inline'
    text: str
    input_bindings: tuple[str, ...]
    output_bindings: tuple[str, ...]
    lineno: int


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
    # Skip sentinel line
    return "\n".join(lines[i + 1 :])


def _extract_bindings(program: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    inputs: list[str] = []
    outputs: list[str] = []
    for m in _BINDING_RE.finditer(program):
        is_out = m.group(1) == ":"
        name = m.group(2)
        if not _IDENTIFIER_RE.match(name):
            raise NaturalParseError(f"Invalid binding name: {name!r}")
        if is_out:
            outputs.append(name)
        else:
            inputs.append(name)
    # Preserve order but de-dup
    def dedup(xs: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return tuple(out)

    return dedup(inputs), dedup(outputs)


def find_natural_blocks(func_source: str) -> tuple[NaturalBlock, ...]:
    """Parse a function source text and return Natural blocks (docstring + inline).

    This expects the input to be the source of a module or function definition.
    """

    try:
        module = ast.parse(func_source)
    except SyntaxError as e:
        raise NaturalParseError(str(e)) from e

    blocks: list[NaturalBlock] = []

    # Find the first function definition in the parsed source.
    func_def: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_def = node
            break
    if func_def is None:
        raise NaturalParseError("No function definition found")

    doc = ast.get_docstring(func_def, clean=False)
    if doc and _is_natural_sentinel(doc):
        program = _extract_program(doc)
        ins, outs = _extract_bindings(program)
        blocks.append(
            NaturalBlock(
                kind="docstring",
                text=program,
                input_bindings=ins,
                output_bindings=outs,
                lineno=getattr(func_def, "lineno", 1),
            )
        )

    # If there is a Natural docstring, do not double-count it as an inline Expr(str).
    start_idx = 1 if func_def.body and isinstance(func_def.body[0], ast.Expr) and isinstance(getattr(func_def.body[0], "value", None), ast.Constant) and isinstance(getattr(getattr(func_def.body[0], "value", None), "value", None), str) else 0

    for stmt in func_def.body[start_idx:]:
        if not isinstance(stmt, ast.Expr):
            continue
        value = stmt.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            s = value.value
            if _is_natural_sentinel(s):
                program = _extract_program(s)
                ins, outs = _extract_bindings(program)
                blocks.append(
                    NaturalBlock(
                        kind="inline",
                        text=program,
                        input_bindings=ins,
                        output_bindings=outs,
                        lineno=getattr(stmt, "lineno", 1),
                    )
                )

    return tuple(blocks)
