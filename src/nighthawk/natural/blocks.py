from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..errors import NaturalParseError

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
