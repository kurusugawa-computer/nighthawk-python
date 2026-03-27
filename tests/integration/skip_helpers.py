"""Shared skip helpers for integration tests."""

import os

import pytest


def requires_openai_integration():  # type: ignore[no-untyped-def]
    if os.getenv("NIGHTHAWK_OPENAI_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    openai_module = pytest.importorskip("pydantic_ai.models.openai")
    return openai_module.OpenAIResponsesModelSettings


def requires_codex_integration() -> None:
    import shutil

    if os.getenv("NIGHTHAWK_CODEX_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if shutil.which("codex") is None:
        pytest.skip("Codex CLI integration test requires 'codex' on PATH")


def requires_claude_code_sdk_integration() -> None:
    if os.getenv("NIGHTHAWK_CLAUDE_SDK_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_AUTH_TOKEN") is None and os.getenv("ANTHROPIC_API_KEY") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY")


def requires_claude_code_cli_integration() -> None:
    import shutil

    if os.getenv("NIGHTHAWK_CLAUDE_CLI_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if shutil.which("claude") is None:
        pytest.skip("Claude Code CLI integration test requires 'claude' on PATH")
