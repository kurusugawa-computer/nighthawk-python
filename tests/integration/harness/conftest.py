import pytest

from tests.integration.skip_helpers import requires_openai_integration


@pytest.fixture(autouse=True)
def require_provider_opt_in() -> None:
    requires_openai_integration()
