"""Shared skip helpers for integration tests."""

import os

import pytest


def requires_openai_integration():  # type: ignore[no-untyped-def]
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    openai_module = pytest.importorskip("pydantic_ai.models.openai")
    return openai_module.OpenAIResponsesModelSettings


def requires_codex_integration() -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    # This integration test requires a real `codex` executable on PATH and valid provider credentials.
    if os.getenv("CODEX_API_KEY") is None:
        pytest.skip("Codex CLI integration test requires CODEX_API_KEY")

    # Codex CLI is probabilistic and relies on local state; allow skipping in environments where it is flaky.
    if os.getenv("NIGHTHAWK_SKIP_CODEX_INTEGRATION") == "1":
        pytest.skip("Codex integration tests are skipped")


def requires_claude_code_integration() -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")
