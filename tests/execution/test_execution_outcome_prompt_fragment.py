from __future__ import annotations

from nighthawk.runtime.step_contract import build_step_system_prompt_suffix_fragment


def test_fragment_omits_raise_when_not_allowed() -> None:
    fragment = build_step_system_prompt_suffix_fragment(
        allowed_kinds=("pass", "return"),
        raise_error_type_binding_names=("ValueError",),
    )

    assert "kind: pass | return." in fragment

    assert "raise" not in fragment
    assert "raise_message" not in fragment
    assert "raise_error_type" not in fragment


def test_fragment_includes_raise_without_raise_error_type_enum_when_no_bindings() -> None:
    fragment = build_step_system_prompt_suffix_fragment(
        allowed_kinds=("pass", "raise"),
        raise_error_type_binding_names=(),
    )

    assert "kind: pass | raise." in fragment
    assert "raise needs raise_message." in fragment
    assert "raise_error_type" not in fragment


def test_fragment_includes_break_and_continue_only_when_allowed() -> None:
    fragment = build_step_system_prompt_suffix_fragment(
        allowed_kinds=("continue",),
        raise_error_type_binding_names=(),
    )

    assert "continue" in fragment
    assert "break" not in fragment


def test_fragment_includes_raise_error_type_enum_when_bindings_present() -> None:
    fragment = build_step_system_prompt_suffix_fragment(
        allowed_kinds=("raise",),
        raise_error_type_binding_names=("ValueError", "TypeError"),
    )

    assert "raise needs raise_message." in fragment
    assert "raise_error_type: ValueError | TypeError" in fragment


def test_fragment_mentions_result_field() -> None:
    fragment = build_step_system_prompt_suffix_fragment(
        allowed_kinds=("pass",),
        raise_error_type_binding_names=(),
    )

    assert '"result"' in fragment
    assert '"kind"' in fragment
    assert "Default: pass." in fragment
