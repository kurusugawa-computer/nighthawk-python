from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai import models
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

import nighthawk as nh


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("string_selection", [False, True])
def test_managed_model_keeps_prompts_tools_settings_and_dynamic_contract(
    monkeypatch: pytest.MonkeyPatch, asynchronous: bool, string_selection: bool
) -> None:
    observed: list[tuple[str, str, dict[str, Any]]] = []
    invoked: list[str] = []
    hook_calls: list[str] = []
    commits: list[nh.oversight.StepCommit] = []
    tool_calls: list[str] = []

    def custom_tool() -> int:
        invoked.append("custom")
        return 3

    async def before_request(context: Any, request: Any) -> Any:
        hook_calls.append("request")
        return request

    async def respond(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
        prompt = repr(messages)
        assert "configured-system" in prompt and "scoped-system" in prompt
        assert "configured-user" in prompt and "scoped-user" in prompt
        assert "<<<NH:PROGRAM>>>" in prompt
        assert "You are executing one Nighthawk Natural" in prompt
        assert {tool.name for tool in information.function_tools} == {"nh_eval", "nh_assign", "custom_tool"}
        assert information.model_settings is not None
        assert information.model_settings.get("temperature") == 0.1
        assert information.model_settings.get("max_tokens") == 77
        output_tool = information.output_tools[0]
        schema = json.dumps(output_tool.parameters_json_schema)
        completed = {part.tool_name for message in messages for part in message.parts if isinstance(part, ToolReturnPart)}
        observed.append((prompt, schema, dict(information.model_settings)))
        if "nh_eval" not in completed:
            parts = [ToolCallPart("nh_eval", {"expression": "invoked.append('eval')"}, "evaluate")]
        elif "custom_tool" not in completed:
            parts = [ToolCallPart("custom_tool", {}, "custom")]
        elif "nh_assign" not in completed:
            parts = [ToolCallPart("nh_assign", {"target_path": "result", "expression": "'41'"}, "assign")]
        else:
            kind = "pass" if "FIRST_STEP" in prompt else "return"
            result = {"kind": kind}
            if kind == "return":
                result["return_expression"] = "result + 1"
            parts = [ToolCallPart(output_tool.name, {"result": result}, "finish")]
        return ModelResponse(parts=parts, usage=RequestUsage(input_tokens=10, output_tokens=5))

    model = FunctionModel(respond, model_name="managed-local", settings={"temperature": 0.9, "max_tokens": 77})
    infer_model = models.infer_model

    def select(selection: Any, *arguments: Any, **keywords: Any) -> Any:
        if isinstance(selection, str):
            assert string_selection and selection == "test:managed-local"
            return model
        assert selection is model
        return infer_model(selection, *arguments, **keywords)

    monkeypatch.setattr(models, "infer_model", select)
    configuration = nh.StepExecutorConfiguration(
        model="test:managed-local" if string_selection else model,
        model_settings={"temperature": 0.1},
        system_prompt_suffix_fragments=("configured-system",),
        user_prompt_suffix_fragments=("configured-user",),
    )
    executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
    assert executor.agent_is_managed
    assert executor.agent is not None and executor.agent.model is model  # type: ignore[attr-defined]

    @nh.natural_function
    def workflow() -> int:
        result: int = 0  # noqa: F841
        for _ in range(1):
            """natural
            ---
            deny: [return]
            ---
            FIRST_STEP <invoked> <:result>
            Use the tools.
            """
        """natural
        SECOND_STEP <invoked> <:result>
        Return result plus one.
        """
        return 0

    @nh.natural_function
    async def workflow_async() -> int:
        result: int = 0  # noqa: F841
        for _ in range(1):
            """natural
            ---
            deny: [return]
            ---
            FIRST_STEP <invoked> <:result>
            Use the tools.
            """
        """natural
        SECOND_STEP <invoked> <:result>
        Return result plus one.
        """
        return 0

    def inspect_tool(call: nh.oversight.ToolCall) -> nh.oversight.Accept:
        tool_calls.append(call.tool_name)
        return nh.oversight.Accept()

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.Accept:
        commits.append(commit)
        return nh.oversight.Accept()

    with (
        nh.run(executor),
        nh.scope(
            tools=[custom_tool],
            capabilities=[Hooks(before_model_request=before_request)],
            system_prompt_suffix_fragments=["scoped-system"],
            user_prompt_suffix_fragments=["scoped-user"],
            oversight=nh.oversight.Oversight(inspect_step_commit=inspect, inspect_tool_call=inspect_tool),
        ),
    ):
        assert (asyncio.run(workflow_async()) if asynchronous else workflow()) == 42
        assert nh.get_usage_meter().total_tokens > 0
    assert invoked == ["eval", "custom", "eval", "custom"]
    assert tool_calls == ["nh_eval", "custom_tool", "nh_assign"] * 2
    assert len(commits) == 2
    assert len(hook_calls) == len(observed) == 8
    assert '"break"' in observed[0][1] and '"return"' not in observed[0][1]
    assert '"return"' in observed[-1][1] and '"break"' not in observed[-1][1]
    assert commits[0].binding_name_to_value["result"] == 41


def test_in_memory_authentication_scope_restoration_and_borrowed_clients(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    keys = ("synthetic-parent-credential", "synthetic-child-credential")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    original_setitem = os._Environ.__setitem__

    def guarded_setitem(environment: Any, name: str, value: str) -> None:
        assert all(key not in value for key in keys)
        original_setitem(environment, name, value)

    monkeypatch.setattr(os._Environ, "__setitem__", guarded_setitem)
    requests: list[tuple[str, dict[str, Any]]] = []
    closed: list[str] = []

    def assert_credentials_absent() -> None:
        assert "OPENAI_API_KEY" not in os.environ
        assert "ANTHROPIC_API_KEY" not in os.environ
        for key in keys:
            assert all(key not in value for value in os.environ.values())
            assert all(key not in argument for argument in sys.argv)

    class Transport(httpx2.AsyncBaseTransport):
        def __init__(self, key: str) -> None:
            self.key = key

        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            assert_credentials_absent()
            assert request.url.host == "mock.invalid"
            assert request.headers["authorization"] == f"Bearer {self.key}"
            body = json.loads(request.content)
            assert body["store"] is False
            assert body["temperature"] == (0.1 if self.key == keys[0] else 0.2)
            assert all(key not in request.content.decode() for key in keys)
            requests.append((self.key, body))
            output_tool = next(tool for tool in body["tools"] if tool["name"] not in {"nh_eval", "nh_assign"})
            return httpx2.Response(
                200,
                json={
                    "id": "resp_mock",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": "gpt-4o",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_mock",
                            "call_id": "call_mock",
                            "name": output_tool["name"],
                            "arguments": json.dumps({"result": {"kind": "return", "return_expression": "7"}}),
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
            )

        async def aclose(self) -> None:
            closed.append(self.key)

    @nh.natural_function
    async def workflow() -> int:
        """natural
        Return seven.
        """
        return 0

    async def execute() -> None:
        assert_credentials_absent()
        clients = [
            AsyncOpenAI(api_key=key, base_url="https://mock.invalid/v1", http_client=httpx2.AsyncClient(transport=Transport(key))) for key in keys
        ]
        try:
            selected_models = [OpenAIResponsesModel("gpt-4o", provider=OpenAIProvider(openai_client=client)) for client in clients]
            configurations = [
                nh.StepExecutorConfiguration(
                    model=model, model_settings={"openai_store": False, "temperature": 0.1 + index / 10}, tokenizer_encoding="o200k_base"
                )
                for index, model in enumerate(selected_models)
            ]
            parent = nh.AgentStepExecutor.from_configuration(configuration=configurations[0])
            with nh.run(parent):
                assert await workflow() == 7
                with nh.scope(step_executor_configuration=configurations[1]):
                    child = nh.get_step_executor()
                    assert isinstance(child, nh.AgentStepExecutor)
                    assert child.configuration.model is selected_models[1]
                    assert child.agent is not None and child.agent.model is selected_models[1]  # type: ignore[attr-defined]
                    assert await workflow() == 7
                assert nh.get_step_executor() is parent
                assert not closed
                for exception in (RuntimeError, nh.oversight.OversightRejectedError):
                    with pytest.raises(exception), nh.scope(step_executor_configuration=configurations[1]):
                        if exception is RuntimeError:
                            raise RuntimeError("child failure")
                        with nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Reject("rejected"))):
                            await workflow()
                    assert nh.get_step_executor() is parent
                derived = configurations[0].model_copy(
                    update={"prompts": nh.StepPromptTemplates(step_system_prompt_template="Replacement system prompt")}
                )
                with nh.scope(step_executor_configuration=derived):
                    assert derived.model is selected_models[0]
                    assert await workflow() == 7
                assert await workflow() == 7
                assert_credentials_absent()

            async def independent(configuration: nh.StepExecutorConfiguration) -> int:
                executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
                with nh.run(executor):
                    await asyncio.sleep(0)
                    result = await workflow()
                    assert nh.get_step_executor() is executor
                    return result

            assert await asyncio.gather(*(independent(configuration) for configuration in configurations)) == [7, 7]
            assert not closed
            assert all(not client.is_closed() for client in clients)
        finally:
            for client in clients:
                await client.close()
        assert closed == list(keys)

    asyncio.run(execute())
    assert [key for key, _ in requests[:5]] == [keys[0], keys[1], keys[1], keys[0], keys[0]]
    assert Counter(key for key, _ in requests[5:]) == Counter(keys)
    assert all(key not in caplog.text for key in keys)
