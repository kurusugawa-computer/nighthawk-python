import os
from pathlib import Path

import logfire
import pytest
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

import nighthawk as nh
import nighthawk.execution.executors

logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()


class FakeMemory(BaseModel):
    pass


def global_import_file(file_path: Path | str) -> str:
    with open(nh.get_environment().workspace_root / file_path, "r") as f:
        return f.read()


def test_local_import_integration():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=nighthawk.execution.executors.make_agent_executor(
            nh.ExecutionConfiguration(),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
        memory=FakeMemory(),
        workspace_root=Path(__file__).parent,
    )

    with nh.environment(environment):

        @nh.fn
        def test_local_function() -> int:
            def local_import_file(file_path: Path | str) -> str:
                with open(nh.get_environment().workspace_root / file_path, "r") as f:
                    return f.read()

            print(f"""{local_import_file("test_import.md")}""")
            """natural
            {local_import_file("test_import.md")}
            """
            return 1

        assert test_local_function() == 10


def test_global_import_integration():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=nighthawk.execution.executors.make_agent_executor(
            nh.ExecutionConfiguration(),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
        memory=FakeMemory(),
        workspace_root=Path(__file__).parent,
    )

    with nh.environment(environment):

        @nh.fn
        def test_global_function() -> int:
            """natural
            {global_import_file("test_import.md")}
            """
            return 1

        assert test_global_function() == 10
