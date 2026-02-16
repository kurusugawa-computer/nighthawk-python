from __future__ import annotations

from nighthawk.execution.contracts import build_execution_outcome_system_prompt_suffix_fragment


def test_fragment_omits_raise_when_not_allowed() -> None:
    fragment = build_execution_outcome_system_prompt_suffix_fragment(
        allowed_outcome_types=("pass", "return"),
        error_type_binding_names=("ValueError",),
    )

    assert "`type` MUST be one of: `pass`, `return`." in fragment

    assert "- `raise`:" not in fragment
    assert "`message`" not in fragment
    assert "error_type" not in fragment


def test_fragment_includes_raise_without_error_type_enum_when_no_bindings() -> None:
    fragment = build_execution_outcome_system_prompt_suffix_fragment(
        allowed_outcome_types=("pass", "raise"),
        error_type_binding_names=(),
    )

    assert "`type` MUST be one of: `pass`, `raise`." in fragment
    assert "- `raise`:" in fragment
    assert "`message` is required." in fragment
    assert "Output keys: `type`, `message`." in fragment
    assert "error_type" not in fragment


def test_fragment_includes_break_and_continue_only_when_allowed() -> None:
    fragment = build_execution_outcome_system_prompt_suffix_fragment(
        allowed_outcome_types=("continue",),
        error_type_binding_names=(),
    )

    assert "- `continue`:" in fragment
    assert "- `break`:" not in fragment


def test_fragment_includes_raise_error_type_enum_when_bindings_present() -> None:
    fragment = build_execution_outcome_system_prompt_suffix_fragment(
        allowed_outcome_types=("raise",),
        error_type_binding_names=("ValueError", "TypeError"),
    )

    assert "- `raise`:" in fragment
    assert "If you include `error_type`, it MUST be one of: `ValueError`, `TypeError`." in fragment
