"""Shared inference preserves explicit Python contracts."""

from nighthawk.runtime import MISSING_ANNOTATION, infer_binding_types


def test_infer_str_from_initial_value():
    binding_name_to_type: dict[str, object] = {"result": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"result": ""}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is str


def test_infer_int_from_initial_value():
    binding_name_to_type: dict[str, object] = {"count": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"count": 0}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["count"] is int


def test_infer_list_from_initial_value():
    binding_name_to_type: dict[str, object] = {"items": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"items": []}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["items"] is list


def test_infer_dict_from_initial_value():
    binding_name_to_type: dict[str, object] = {"data": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"data": {}}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["data"] is dict


def test_skip_none_initial_value():
    binding_name_to_type: dict[str, object] = {"result": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"result": None}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is object


def test_skip_object_initial_value():
    sentinel = object()
    binding_name_to_type: dict[str, object] = {"result": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"result": sentinel}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is object


def test_preserve_explicit_annotation():
    binding_name_to_type: dict[str, object] = {"result": str}
    step_locals: dict[str, object] = {"result": 42}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is str


def test_skip_missing_from_step_locals():
    binding_name_to_type: dict[str, object] = {"result": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["result"] is object


def test_infer_float_from_initial_value():
    binding_name_to_type: dict[str, object] = {"score": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"score": 0.0}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["score"] is float


def test_infer_bool_from_initial_value():
    binding_name_to_type: dict[str, object] = {"flag": MISSING_ANNOTATION}
    step_locals: dict[str, object] = {"flag": False}
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["flag"] is bool


def test_multiple_bindings():
    binding_name_to_type: dict[str, object] = {
        "name": MISSING_ANNOTATION,
        "count": MISSING_ANNOTATION,
        "label": str,
    }
    step_locals: dict[str, object] = {
        "name": "",
        "count": 0,
        "label": "hello",
    }
    binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
    assert binding_name_to_type["name"] is str
    assert binding_name_to_type["count"] is int
    assert binding_name_to_type["label"] is str  # preserved, not overwritten
