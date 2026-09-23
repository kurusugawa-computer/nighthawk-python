from typing import Annotated, Any

import pytest
from pydantic import AfterValidator, ValidationError

from nighthawk.errors import ExecutionError
from nighthawk.runtime import MISSING_ANNOTATION, BindingValidationError, infer_binding_types, validate_output_bindings


def test_inference_preserves_explicit_annotations_and_both_inputs() -> None:
    declared_name_to_type = {"missing": MISSING_ANNOTATION, "wide": object, "any": Any, "none": None}
    initial_name_to_value = {"missing": False, "wide": "", "any": 0, "none": 42, "undeclared": []}
    inferred_name_to_type = infer_binding_types(declared_name_to_type, initial_name_to_value)
    assert inferred_name_to_type == {"missing": bool, "wide": object, "any": Any, "none": None}
    assert declared_name_to_type["missing"] is MISSING_ANNOTATION
    assert initial_name_to_value["missing"] is False


def test_partial_validation_preserves_candidates_and_skips_absent_contracts() -> None:
    shared: list[int] = []
    candidate_name_to_value = {"count": "12", "shared": shared, "untyped": "unchanged"}
    validated_name_to_value = validate_output_bindings(
        candidate_name_to_value, {"count": int, "untyped": MISSING_ANNOTATION}, ("count", "label", "shared", "untyped")
    )
    assert validated_name_to_value == {"count": 12, "shared": [], "untyped": "unchanged"}
    assert validated_name_to_value is not candidate_name_to_value
    assert candidate_name_to_value["count"] == "12"
    assert validated_name_to_value["shared"] is shared
    assert validate_output_bindings({}, {"count": int}, {"count"}) == {}


def test_undeclared_names_are_rejected_before_validators() -> None:
    effects: list[int] = []

    def validate(value: int) -> int:
        effects.append(value)
        return value

    with pytest.raises(ExecutionError, match="extra"):
        validate_output_bindings({"count": 12, "extra": 3}, {"count": Annotated[int, AfterValidator(validate)]}, ("count",))
    assert effects == []


def test_validation_failure_preserves_original_exception_and_candidate() -> None:
    candidate_name_to_value = {"count": "12", "empty": 1}
    with pytest.raises(BindingValidationError) as caught:
        validate_output_bindings(candidate_name_to_value, {"count": int, "empty": None}, ("count", "empty"))
    assert caught.value.binding_name == "empty"
    assert isinstance(caught.value.original_exception, ValidationError)
    assert caught.value.__cause__ is caught.value.original_exception
    assert candidate_name_to_value == {"count": "12", "empty": 1}
    assert validate_output_bindings({"empty": None}, {"empty": None}, ("empty",)) == {"empty": None}


def test_validator_order_is_declaration_order_and_effects_are_not_rolled_back() -> None:
    effects: list[str] = []

    def first(value: int) -> int:
        effects.append("first")
        return value

    def second(value: int) -> int:
        effects.append("second")
        raise ValueError("invalid")

    binding_name_to_type = {"first": Annotated[int, AfterValidator(first)], "second": Annotated[int, AfterValidator(second)]}
    with pytest.raises(BindingValidationError):
        validate_output_bindings({"second": 2, "first": 1}, binding_name_to_type, ("first", "second"))
    assert effects == ["first", "second"]
