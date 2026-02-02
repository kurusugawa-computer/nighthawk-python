import os

import pytest


def pytest_sessionstart(session: pytest.Session) -> None:
    _ = session

    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") == "1":
        has_openai = bool(os.getenv("OPENAI_API_KEY"))
        has_anthropic = bool(os.getenv("ANTHROPIC_AUTH_TOKEN"))

        if not has_openai and not has_anthropic:
            raise RuntimeError("Integration tests require provider credentials. Set OPENAI_API_KEY for OpenAI integration tests and/or ANTHROPIC_AUTH_TOKEN (and ANTHROPIC_BASE_URL if applicable) for Claude Agent SDK integration tests.")
