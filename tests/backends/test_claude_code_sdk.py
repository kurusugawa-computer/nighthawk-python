from __future__ import annotations

import os

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModel
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
