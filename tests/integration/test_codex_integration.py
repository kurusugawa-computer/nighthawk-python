import os
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

import nighthawk as nh
from nighthawk.backends.codex import CodexModel
from nighthawk.execution.context import ExecutionContext


class FakeMemory(BaseModel):
    pass


class StructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int


def _requires_codex_integration() -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    # This integration test requires a real `codex` executable on PATH and valid provider credentials.
    if os.getenv("CODEX_API_KEY") is None:
        pytest.skip("Codex CLI integration test requires CODEX_API_KEY")


def test_codex_natural_block_uses_tool(tmp_path: Path) -> None:
    _requires_codex_integration()

    execution_configuration = nh.ExecutionConfiguration(model="codex:default")

    environment = nh.ExecutionEnvironment(
        execution_configuration=execution_configuration,
        execution_executor=nh.AgentExecutor(execution_configuration=execution_configuration),
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


def test_codex_natural_block_uses_custom_nh_tool(tmp_path: Path) -> None:
    _requires_codex_integration()

    execution_configuration = nh.ExecutionConfiguration(model="codex:default")

    environment = nh.ExecutionEnvironment(
        execution_configuration=execution_configuration,
        execution_executor=nh.AgentExecutor(execution_configuration=execution_configuration),
        memory=FakeMemory(),
        workspace_root=tmp_path,
    )

    with nh.environment(environment):

        @nh.tool(name="test_operation")
        def test_operation(run_context, *, a: int, b: int) -> int:  # type: ignore[no-untyped-def]
            _ = run_context
            return a + b

        @nh.fn
        def test_function() -> int:
            result = 0
            """natural
            Compute <:result> with test_operation(a=20, b=22).
            """
            return int(result)

        assert test_function() == 42


def test_codex_structured_output_via_output_schema(tmp_path: Path) -> None:
    _requires_codex_integration()

    execution_configuration = nh.ExecutionConfiguration(model="codex:default")

    environment = nh.ExecutionEnvironment(
        execution_configuration=execution_configuration,
        execution_executor=nh.AgentExecutor(execution_configuration=execution_configuration),
        memory=FakeMemory(),
        workspace_root=tmp_path,
    )

    with nh.environment(environment):
        model = CodexModel()

        tool_context = ExecutionContext(
            execution_id="test_codex_structured_output_via_output_schema",
            execution_configuration=execution_configuration,
            execution_globals={"__builtins__": __builtins__},
            execution_locals={},
            binding_commit_targets=set(),
            memory=None,
        )

        from pydantic_ai import Agent

        structured_agent = Agent(
            model=model,
            deps_type=ExecutionContext,
            output_type=StructuredOutput,
        )

        result = structured_agent.run_sync(
            'Return exactly this JSON object and nothing else: {"answer": 2}',
            deps=tool_context,
        )

        assert result.output.answer == 2
