import os

import pytest


def pytest_sessionstart(session: pytest.Session) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for this test suite. Set it in your environment before running pytest.")
