import pytest

import nighthawk
from nighthawk import agent
from nighthawk.agent import conversation, generated, generation, runtime, session, sources


@pytest.mark.parametrize(
    "module",
    [
        nighthawk,
        nighthawk.lifecycle,
        nighthawk.oversight,
        nighthawk.resilience,
        agent,
        conversation,
        generated,
        generation,
        runtime,
        session,
        sources,
    ],
)
def test_public_exports_resolve(module) -> None:
    assert set(module.__all__).issubset(dir(module))
    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)
    for name in module.__all__:
        assert namespace[name] is getattr(module, name)
