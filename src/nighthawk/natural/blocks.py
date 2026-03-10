from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

import yaml

from ..errors import NaturalParseError

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BINDING_PATTERN = re.compile(r"<(:?)([A-Za-z_][A-Za-z0-9_]*)>")


@dataclass(frozen=True)
class NaturalBlock:
    kind: Literal["docstring", "inline"]
    text: str
    input_bindings: tuple[str, ...]
    output_bindings: tuple[str, ...]
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
    for match in _BINDING_PATTERN.finditer(program):
        is_output = match.group(1) == ":"
        name = match.group(2)
        if not _IDENTIFIER_PATTERN.match(name):
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


def _validate_joined_string_bindings_do_not_span_formatted_values(joined_string: ast.JoinedStr) -> None:
    """Validate that no binding marker spans a formatted value boundary."""
    boundary_marked_text = _joined_string_scan_text(
        joined_string,
        formatted_value_placeholder=_JOINED_STRING_FORMATTED_VALUE_PLACEHOLDER,
    )

    if re.search(r"<[^>]*" + _JOINED_STRING_FORMATTED_VALUE_PLACEHOLDER + r"[^>]*>", boundary_marked_text):
        raise NaturalParseError("Binding marker must not span formatted value boundary in inline f-string Natural block")


def _extract_program_and_bindings_from_joined_string(joined_string: ast.JoinedStr) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    _validate_joined_string_bindings_do_not_span_formatted_values(joined_string)

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

    docstring_text = ast.get_docstring(func_def, clean=False)
    if docstring_text and is_natural_sentinel(docstring_text):
        program = extract_program(docstring_text)
        input_bindings, output_bindings = extract_bindings(program)
        blocks.append(
            NaturalBlock(
                kind="docstring",
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
                program = extract_program(text)
                input_bindings, output_bindings = extract_bindings(program)
                blocks.append(
                    NaturalBlock(
                        kind="inline",
                        text=program,
                        input_bindings=input_bindings,
                        output_bindings=output_bindings,
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
                    output_bindings=output_bindings,
                    lineno=getattr(statement, "lineno", 1),
                )
            )

    return tuple(blocks)


_FRONTMATTER_STEP_KINDS = ("pass", "return", "break", "continue", "raise")


def validate_frontmatter_deny(frontmatter: dict[str, object]) -> tuple[str, ...]:
    """Validate a parsed frontmatter mapping and return the denied step kinds.

    Raises:
        NaturalParseError: If the frontmatter contains unknown keys, unknown
            step kind names, or has an invalid ``deny`` structure.
    """
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
    """Parse and strip YAML frontmatter from a Natural program.

    Frontmatter is recognized when the first non-blank line is ``---`` and a
    matching closing ``---`` line follows.  The YAML content between the
    delimiters must be a mapping.

    Returns:
        A tuple of (program_text_without_frontmatter, parsed_mapping).
        When no frontmatter is present the mapping is empty.

    Raises:
        NaturalParseError: If the frontmatter is syntactically invalid.
    """
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
