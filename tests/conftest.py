import os

import pytest


def pytest_sessionstart(session: pytest.Session) -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") == "1" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required when NIGHTHAWK_RUN_INTEGRATION_TESTS=1. Set it in your environment before running pytest.")
