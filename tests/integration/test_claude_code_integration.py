import os
from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.execution.executors import make_agent_executor


class FakeMemory(BaseModel):
    pass


def test_claude_code_natural_block_uses_tool(tmp_path: Path) -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    execution_configuration = nh.ExecutionConfiguration(model="claude-code:default")

    environment = nh.ExecutionEnvironment(
        execution_configuration=execution_configuration,
        execution_executor=make_agent_executor(execution_configuration),
        memory=FakeMemory(),
        workspace_root=tmp_path,
    )

    with nh.environment(environment):

        @nh.fn
        def test_function() -> str:
            result = ""
            """natural
            <:result>
            Use nh_eval("1 + 1") to confirm arithmetic, then call nh_assign("result", "'2'").
            """

            return result

        assert test_function() == "2"
