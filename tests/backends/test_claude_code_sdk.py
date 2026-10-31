from __future__ import annotations

import os
from typing import Any, cast

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings

from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModel, ClaudeCodeSdkModelSettings
from nighthawk.runtime.step_context import StepContext
from tests.support.contexts import new_context


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_request_preserves_host_environment(monkeypatch: pytest.MonkeyPatch, failed: bool) -> None:
    sdk = pytest.importorskip("claude_agent_sdk")
    monkeypatch.setenv("CLAUDECODE", "host-session")
    environment_values: list[str | None] = []
    closed: list[bool] = []

    class Client:
        def __init__(self, *, options) -> None:
            environment_values.append(os.environ.get("CLAUDECODE"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *arguments: object) -> None:
            environment_values.append(os.environ.get("CLAUDECODE"))
            closed.append(True)

        async def query(self, prompt: str) -> None:
            assert "Read the evidence" in prompt

        async def receive_response(self):
            environment_values.append(os.environ.get("CLAUDECODE"))
            yield sdk.ResultMessage(
                subtype="error_during_execution" if failed else "success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=failed,
                num_turns=1,
                session_id="test-session",
                result="provider failed" if failed else "completed",
                usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30},
            )

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    model = ClaudeCodeSdkModel()
    agent = Agent(model, deps_type=StepContext)
    if failed:
        with pytest.raises(UnexpectedModelBehavior, match="provider failed") as caught:
            await agent.run("Read the evidence", deps=new_context())
        assert '"session_id": "test-session"' in str(caught.value)
    else:
        response = await agent.run("Read the evidence", deps=new_context())
        assert response.output == "completed"
        assert response.usage.input_tokens == 60
        assert response.usage.output_tokens == 5
        assert response.usage.cache_read_tokens == 20
        assert response.usage.cache_write_tokens == 30
        assert response.usage.total_tokens == 65
    assert closed == [True]
    assert environment_values == ["host-session"] * 3
    assert os.environ["CLAUDECODE"] == "host-session"


@pytest.mark.asyncio
async def test_shared_claude_settings_reach_sdk_options(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = pytest.importorskip("claude_agent_sdk")
    captured_options: list[Any] = []

    class Client:
        def __init__(self, *, options) -> None:
            captured_options.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *arguments: object) -> None:
            return None

        async def query(self, prompt: str) -> None:
            return None

        async def receive_response(self):
            yield sdk.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                result="completed",
                usage={},
            )

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    agent = Agent(ClaudeCodeSdkModel(), deps_type=StepContext)
    settings = ClaudeCodeSdkModelSettings(max_budget_usd=2.5, claude_allowed_tool_names=("Read",))
    result = await agent.run("Read the evidence", deps=new_context(), model_settings=cast(ModelSettings, settings.model_dump()))

    options = captured_options[0]
    assert options.max_budget_usd == 2.5
    assert "Read" in options.allowed_tools
    assert result.response.provider_name == "claude-code-sdk"
