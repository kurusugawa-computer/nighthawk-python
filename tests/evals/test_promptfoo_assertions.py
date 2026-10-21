import json

import pytest

from evals.promptfoo.assertions import binding_value, function_calls, outcome_kind, raise_message


@pytest.mark.parametrize("check_step_locals", [False, True])
def test_missing_binding_does_not_equal_null(check_step_locals: bool) -> None:
    result = binding_value.get_assert(
        json.dumps({"bindings": {}, "step_locals": {}}),
        {"config": {"expected_bindings": {"result": None}, "check_step_locals": check_step_locals}},
    )
    assert result["pass"] is False
    assert "missing" in result["reason"]


def test_explicit_null_is_not_replaced_with_a_namespace_value() -> None:
    output = json.dumps({"bindings": {"result": None}, "step_locals": {"result": 7}})
    for expected, passed in ((None, True), (7, False)):
        result = binding_value.get_assert(output, {"config": {"expected_bindings": {"result": expected}, "check_step_locals": True}})
        assert result["pass"] is passed


def test_namespace_effects_require_explicit_selection() -> None:
    output = json.dumps({"bindings": {}, "step_locals": {"items": [1, 2]}})
    configuration = {"expected_bindings": {"items": [1, 2]}}
    assert binding_value.get_assert(output, {"config": configuration})["pass"] is False
    assert binding_value.get_assert(output, {"config": {**configuration, "check_step_locals": True}})["pass"] is True


def test_missing_binding_lowers_partial_score() -> None:
    result = binding_value.get_assert('{"bindings": {"first": 1}}', {"config": {"expected_bindings": {"first": 1, "second": None}}})
    assert result["pass"] is False
    assert result["score"] == 0.5


@pytest.mark.parametrize("data", [None, [], 7, "unexpected"])
@pytest.mark.parametrize("assertion", [binding_value.get_assert, outcome_kind.get_assert, raise_message.get_assert])
def test_assertions_reject_non_object_outputs(data: object, assertion) -> None:
    assert assertion(json.dumps(data), {})["pass"] is False


@pytest.mark.parametrize(
    "data", [{"outcome_kind": "return"}, {"outcome_kind": "return", "return_value": 2}, {"outcome_kind": "pass", "return_value": 1}]
)
def test_return_assertion_checks_the_actual_result(data: dict) -> None:
    assert outcome_kind.get_assert(json.dumps(data), {"config": {"expected_return_value": 1}})["pass"] is False


def test_null_return_requires_a_returned_value() -> None:
    configuration = {"expected_return_value": None}
    assert outcome_kind.get_assert('{"outcome_kind": "return"}', {"config": configuration})["pass"] is False
    assert outcome_kind.get_assert('{"outcome_kind": "return", "return_value": null}', {"config": configuration})["pass"] is True


def test_fabricated_function_result_does_not_prove_a_function_call() -> None:
    required_call = {"name": "add", "arguments": {"first": 3, "second": 7}}
    configuration = {"expected_calls": [required_call]}
    output = {"bindings": {"result": 10}, "function_calls": []}
    assert function_calls.get_assert(json.dumps(output), {"config": configuration})["pass"] is False
    output["function_calls"] = [required_call]
    assert function_calls.get_assert(json.dumps(output), {"config": configuration})["pass"] is True
