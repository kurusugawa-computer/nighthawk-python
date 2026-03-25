"""Tests for _infer_binding_types_from_initial_values."""

from nighthawk.runtime.runner import _infer_binding_types_from_initial_values


def test_infer_str_from_initial_value():
    binding_name_to_type: dict[str, object] = {"result": object}
    step_locals: dict[str, object] = {"result": ""}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is str


def test_infer_int_from_initial_value():
    binding_name_to_type: dict[str, object] = {"count": object}
    step_locals: dict[str, object] = {"count": 0}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["count"] is int


def test_infer_list_from_initial_value():
    binding_name_to_type: dict[str, object] = {"items": object}
    step_locals: dict[str, object] = {"items": []}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["items"] is list


def test_infer_dict_from_initial_value():
    binding_name_to_type: dict[str, object] = {"data": object}
    step_locals: dict[str, object] = {"data": {}}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["data"] is dict


def test_skip_none_initial_value():
    binding_name_to_type: dict[str, object] = {"result": object}
    step_locals: dict[str, object] = {"result": None}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is object


def test_skip_object_initial_value():
    sentinel = object()
    binding_name_to_type: dict[str, object] = {"result": object}
    step_locals: dict[str, object] = {"result": sentinel}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is object


def test_preserve_explicit_annotation():
    binding_name_to_type: dict[str, object] = {"result": str}
    step_locals: dict[str, object] = {"result": 42}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is str


def test_skip_missing_from_step_locals():
    binding_name_to_type: dict[str, object] = {"result": object}
    step_locals: dict[str, object] = {}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is object


def test_infer_float_from_initial_value():
    binding_name_to_type: dict[str, object] = {"score": object}
    step_locals: dict[str, object] = {"score": 0.0}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["score"] is float


def test_infer_bool_from_initial_value():
    binding_name_to_type: dict[str, object] = {"flag": object}
    step_locals: dict[str, object] = {"flag": False}
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["flag"] is bool


def test_multiple_bindings():
    binding_name_to_type: dict[str, object] = {
        "name": object,
        "count": object,
        "label": str,
    }
    step_locals: dict[str, object] = {
        "name": "",
        "count": 0,
        "label": "hello",
    }
    _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
    assert binding_name_to_type["name"] is str
    assert binding_name_to_type["count"] is int
    assert binding_name_to_type["label"] is str  # preserved, not overwritten
