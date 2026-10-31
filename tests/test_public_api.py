import re
from pathlib import Path

import pytest

import nighthawk
import nighthawk.runtime


@pytest.mark.parametrize("module", [nighthawk, nighthawk.runtime])
def test_public_exports_resolve(module) -> None:
    assert set(module.__all__).issubset(dir(module))
    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)
    for name in module.__all__:
        assert namespace[name] is getattr(module, name)


def test_documented_runtime_members_are_exported_from_a_package_namespace() -> None:
    api_text = (Path(__file__).resolve().parents[1] / "docs" / "api.md").read_text(encoding="utf-8")
    exported_name_set = set(nighthawk.__all__) | set(nighthawk.runtime.__all__)
    missing_list: list[str] = []
    for block in re.finditer(r"::: (nighthawk\.runtime\.[\w.]+)\n    options:\n      members:\n((?:        - \w+\n)+)", api_text):
        missing_list.extend(f"{block.group(1)}.{name}" for name in re.findall(r"- (\w+)", block.group(2)) if name not in exported_name_set)
    assert missing_list == []
