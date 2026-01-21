import os

import pytest


@pytest.mark.skipif(
    os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1",
    reason="integration tests disabled",
)
def test_openai_client_import_and_agent_construction():
    from nighthawk.configuration import Configuration
    from nighthawk.openai_client import make_agent

    agent = make_agent(Configuration())
    assert agent is not None
