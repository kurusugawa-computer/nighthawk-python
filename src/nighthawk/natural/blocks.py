from __future__ import annotations

import ast
import re
import textwrap
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
    return text.startswith("natural\n")


def extract_program(text: str) -> str:
    if not is_natural_sentinel(text):
        raise NaturalParseError("Missing natural sentinel")
    program = text.removeprefix("natural\n")
    return textwrap.dedent(program)


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


_JOINED_STRING_FORMATTED_VALUE_PLACEHOLDER = "\x00"


def _joined_string_first_literal_or_none(joined_string: ast.JoinedStr) -> str | None:
    if not joined_string.values:
        return None
    first = joined_string.values[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    return first.value


def _joined_string_is_natural_sentinel(joined_string: ast.JoinedStr) -> bool:
    first_literal = _joined_string_first_literal_or_none(joined_string)
    if first_literal is None:
        return False
    return is_natural_sentinel(first_literal)


def _joined_string_scan_text(joined_string: ast.JoinedStr, *, formatted_value_placeholder: str) -> str:
    parts: list[str] = []
    for part in joined_string.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parts.append(part.value)
        else:
            parts.append(formatted_value_placeholder)
    return "".join(parts)


def _extract_program_and_bindings_from_joined_string(joined_string: ast.JoinedStr) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    boundary_marked_text = _joined_string_scan_text(
        joined_string,
        formatted_value_placeholder=_JOINED_STRING_FORMATTED_VALUE_PLACEHOLDER,
    )

    if re.search(r"<[^>]*" + _JOINED_STRING_FORMATTED_VALUE_PLACEHOLDER + r"[^>]*>", boundary_marked_text):
        raise NaturalParseError("Binding marker must not span formatted value boundary in inline f-string Natural block")

    scan_text = _joined_string_scan_text(joined_string, formatted_value_placeholder="")
    program = extract_program(scan_text)
    input_bindings, output_bindings = extract_bindings(program)
    return program, input_bindings, output_bindings


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

        if isinstance(value, ast.JoinedStr) and _joined_string_is_natural_sentinel(value):
            program, input_bindings, output_bindings = _extract_program_and_bindings_from_joined_string(value)
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
