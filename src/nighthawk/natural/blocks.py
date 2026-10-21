from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

import yaml

from ..errors import NaturalParseError
from ..runtime.bindings import BindingDeclarationError, normalize_binding_names
from ..runtime.step_definition import BlockKind

_BINDING_PATTERN = re.compile(r"(?<!\\)<(:?)([A-Za-z_][A-Za-z0-9_]*)>")


@dataclass(frozen=True)
class NaturalBlock:
    kind: Literal["docstring", "inline"]
    text: str
    input_bindings: tuple[str, ...]
    output_bindings: tuple[str, ...]
    lineno: int
    block_kind: BlockKind = "execute"


def is_natural_sentinel(text: str) -> bool:
    return text.startswith(("execute\n", "decide ", "decide\t", "decide\n"))


def parse_block(text: str, *, has_interpolated_instruction: bool = False) -> tuple[BlockKind, str, tuple[str, ...], tuple[str, ...]]:
    header, separator, body = text.partition("\n")
    if not separator:
        raise NaturalParseError("Block header must end with a newline")
    program = textwrap.dedent(body)
    inputs, outputs = extract_bindings(program)
    if header == "execute":
        return "execute", program, inputs, outputs
    match = re.fullmatch(r"decide[ \t]+<:([A-Za-z_][A-Za-z0-9_]*)>", header)
    if match is None:
        raise NaturalParseError("Expected execute or decide <:output> block header")
    if outputs:
        raise NaturalParseError("decide output must be declared only in its header")
    if not program.strip() and not has_interpolated_instruction:
        raise NaturalParseError("decide requires a judgment instruction")
    _, declared_outputs = extract_bindings(match.group(0))
    return "decide", program, inputs, declared_outputs


def extract_program(text: str) -> str:
    return parse_block(text)[1]


def extract_bindings(program: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    inputs: list[str] = []
    outputs: list[str] = []
    for match in _BINDING_PATTERN.finditer(program):
        is_output = match.group(1) == ":"
        name = match.group(2)
        if is_output:
            outputs.append(name)
        else:
            inputs.append(name)

    try:
        return normalize_binding_names(tuple(dict.fromkeys(inputs))), normalize_binding_names(tuple(dict.fromkeys(outputs)))
    except BindingDeclarationError as exception:
        raise NaturalParseError(str(exception)) from exception


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


def parse_joined_block(joined_string: ast.JoinedStr) -> tuple[BlockKind, str, tuple[str, ...], tuple[str, ...]]:
    first_literal = _joined_string_first_literal_or_none(joined_string)
    if first_literal is None or "\n" not in first_literal:
        raise NaturalParseError("Block header must be static")

    parts: list[str] = []
    interpolation_offsets: list[int] = []
    offset = 0
    for part in joined_string.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parts.append(part.value)
            offset += len(part.value)
        else:
            interpolation_offsets.append(offset)
            # Keep literal tokens separated; offsets, not a reserved character, identify interpolation.
            parts.append(" ")
            offset += 1
    scan_text = "".join(parts)
    for match in re.finditer(r"(?<!\\)<[^>]*>", scan_text):
        if any(match.start() < offset < match.end() for offset in interpolation_offsets):
            raise NaturalParseError("Binding marker must not span formatted value boundary in inline f-string Natural block")

    return parse_block(scan_text, has_interpolated_instruction=bool(interpolation_offsets))


def find_natural_blocks(func_source: str) -> tuple[NaturalBlock, ...]:
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

    docstring_text = ast.get_docstring(func_def, clean=False)
    if docstring_text and is_natural_sentinel(docstring_text):
        block_kind, program, input_bindings, output_bindings = parse_block(docstring_text)
        blocks.append(
            NaturalBlock(
                kind="docstring",
                block_kind=block_kind,
                text=program,
                input_bindings=input_bindings,
                output_bindings=output_bindings,
                lineno=getattr(func_def, "lineno", 1),
            )
        )

    start_index = 0
    if func_def.body:
        first_statement = func_def.body[0]
        if isinstance(first_statement, ast.Expr) and isinstance(first_statement.value, ast.Constant) and isinstance(first_statement.value.value, str):
            start_index = 1

    for statement in func_def.body[start_index:]:
        if not isinstance(statement, ast.Expr):
            continue

        value = statement.value

        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            text = value.value
            if is_natural_sentinel(text):
                block_kind, program, input_bindings, output_bindings = parse_block(text)
                blocks.append(
                    NaturalBlock(
                        kind="inline",
                        block_kind=block_kind,
                        text=program,
                        input_bindings=input_bindings,
                        output_bindings=output_bindings,
                        lineno=getattr(statement, "lineno", 1),
                    )
                )

        if isinstance(value, ast.JoinedStr) and _joined_string_is_natural_sentinel(value):
            block_kind, program, input_bindings, output_bindings = parse_joined_block(value)
            blocks.append(
                NaturalBlock(
                    kind="inline",
                    block_kind=block_kind,
                    text=program,
                    input_bindings=input_bindings,
                    output_bindings=output_bindings,
                    lineno=getattr(statement, "lineno", 1),
                )
            )

    return tuple(blocks)


_FRONTMATTER_STEP_KINDS = ("pass", "return", "break", "continue", "raise")


def validate_frontmatter_deny(frontmatter: dict[str, object]) -> tuple[str, ...]:
    if not frontmatter:
        return ()

    allowed_keys = {"deny"}
    unknown_keys = set(frontmatter.keys()) - allowed_keys
    if unknown_keys:
        unknown_key_list = ", ".join(sorted(str(k) for k in unknown_keys))
        raise NaturalParseError(f"Unknown frontmatter keys: {unknown_key_list}")

    if "deny" not in frontmatter:
        raise NaturalParseError("Frontmatter must include 'deny'")

    deny_value = frontmatter["deny"]
    if not isinstance(deny_value, list) or not all(isinstance(item, str) for item in deny_value):
        raise NaturalParseError("Frontmatter 'deny' must be a YAML sequence of strings")

    if len(deny_value) == 0:
        raise NaturalParseError("Frontmatter 'deny' must not be empty")

    denied: list[str] = []
    for item in deny_value:
        if item not in _FRONTMATTER_STEP_KINDS:
            raise NaturalParseError(f"Unknown denied step kind: {item}")
        if item not in denied:
            denied.append(item)

    return tuple(denied)


def parse_frontmatter(processed_natural_program: str) -> tuple[str, dict[str, Any]]:
    lines = processed_natural_program.splitlines(keepends=True)
    if not lines:
        return processed_natural_program, {}

    start_index: int | None = None
    for i, line in enumerate(lines):
        if line.strip(" \t\r\n") == "":
            continue
        start_index = i
        break

    if start_index is None:
        return processed_natural_program, {}

    first_line = lines[start_index]
    if first_line not in ("---\n", "---"):
        return processed_natural_program, {}

    closing_index: int | None = None
    for i, line in enumerate(lines[start_index + 1 :], start=start_index + 1):
        if line in ("---\n", "---"):
            closing_index = i
            break

    if closing_index is None:
        return processed_natural_program, {}

    yaml_text = "".join(lines[start_index + 1 : closing_index])
    if yaml_text.strip() == "":
        return processed_natural_program, {}

    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise NaturalParseError(f"Frontmatter YAML parsing failed: {e}") from e

    if not isinstance(loaded, dict):
        raise NaturalParseError("Frontmatter YAML must be a mapping")

    instructions_without_frontmatter = "".join(lines[closing_index + 1 :])
    return instructions_without_frontmatter, loaded
