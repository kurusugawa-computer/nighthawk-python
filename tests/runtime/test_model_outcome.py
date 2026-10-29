from __future__ import annotations

from typing import Any, cast

import pytest

from nighthawk.runtime.model_outcome import build_model_response_instructions, build_model_response_schema


@pytest.mark.parametrize("allowed_kinds", [("pass",), ("pass", "return"), ("return",), ("break", "continue"), ("raise",)])
def test_response_schema_exposes_only_allowed_control_results(allowed_kinds) -> None:
    schema = build_model_response_schema(allowed_kinds=allowed_kinds, raise_error_type_binding_names=())
    assert schema["type"] == "object"
    assert schema["required"] == ["result"]
    assert schema["additionalProperties"] is False
    result = cast(dict[str, Any], schema["properties"])["result"]
    variants = result.get("anyOf", [result])
    assert tuple(variant["properties"]["kind"]["const"] for variant in variants) == allowed_kinds
    assert all(variant["additionalProperties"] is False for variant in variants)
    if "return" in allowed_kinds:
        variant = next(variant for variant in variants if variant["properties"]["kind"]["const"] == "return")
        assert "return_expression" in variant["required"]
        assert variant["properties"]["return_expression"]["type"] == "string"


@pytest.mark.parametrize("binding_names", [(), ("selected_error",), ("ValueError", "TypeError")])
def test_exception_schema_preserves_binding_names_and_default(binding_names) -> None:
    schema = build_model_response_schema(allowed_kinds=("raise",), raise_error_type_binding_names=binding_names)
    result = cast(dict[str, Any], schema["properties"])["result"]
    assert "raise_message" in result["required"]
    if binding_names:
        assert result["properties"]["raise_error_type"]["enum"] == [*binding_names, None]
    else:
        assert "raise_error_type" not in result["properties"]


@pytest.mark.parametrize("allowed_kinds", [("pass", "return"), ("return",), ("continue",)])
def test_completion_guidance_defaults_to_pass_only_when_allowed(allowed_kinds) -> None:
    fragment = build_model_response_instructions(allowed_kinds=allowed_kinds)
    assert "Complete the program" in fragment
    assert ('"kind":"pass"' in fragment) == ("pass" in allowed_kinds)
