import pytest

import nighthawk


@pytest.mark.parametrize("module", [nighthawk, nighthawk.lifecycle, nighthawk.oversight, nighthawk.resilience])
def test_public_exports_resolve(module) -> None:
    assert set(module.__all__).issubset(dir(module))
    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)
    for name in module.__all__:
        assert namespace[name] is getattr(module, name)
