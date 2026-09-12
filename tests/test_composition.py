from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import FrozenInstanceError

import pytest

import nighthawk as nh
from tests.support.values import IdentityOnly


def test_wrappers_capture_structure_and_preserve_identity() -> None:
    value = IdentityOnly()
    values = [value]
    name_to_value = {"value": value}
    extension = nh.Extend(values)
    merge = nh.Merge(name_to_value)
    values.clear()
    name_to_value.clear()
    assert extension.values[0] is value
    assert merge.name_to_value["value"] is value
    assert copy(nh.UNSET) is deepcopy(nh.UNSET) is nh.UnsetType() is nh.UNSET
    with pytest.raises(FrozenInstanceError):
        extension.values = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        merge.name_to_value["other"] = value  # type: ignore[index]


@pytest.mark.parametrize("value", [None, "abc", b"abc", {1, 2}, iter([1])])
def test_extend_rejects_non_sequences(value: object) -> None:
    with pytest.raises(TypeError):
        nh.Extend(value)  # type: ignore[arg-type]
