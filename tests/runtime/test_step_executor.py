import pytest
from pydantic import BaseModel
from pydantic_ai.messages import BinaryContent, ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.runtime.model_outcome import ModelPass, ModelStepResponse
from nighthawk.runtime.scoping import get_usage_meter


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, TypeError])
@pytest.mark.parametrize("embedded", [False, True], ids=["natural-function", "embedded-step"])
@pytest.mark.parametrize("selection", ["selected_error", None])
async def test_executor_exposes_exception_binding_aliases(error_type: type[Exception], embedded: bool, selection: str | None) -> None:
    events: list[nh.lifecycle.StepFinished] = []

    async def respond(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
        output_tool = information.output_tools[0]
        variant = output_tool.parameters_json_schema["properties"]["result"]
        exception_schema = variant["properties"]["raise_error_type"]
        assert "selected_error" in exception_schema["enum"]
        assert None in exception_schema["enum"]
        return ModelResponse(
            parts=[ToolCallPart(output_tool.name, {"result": {"kind": "raise", "raise_message": "invalid input", "raise_error_type": selection}})]
        )

    @nh.function
    async def reject(selected_error: type[Exception]) -> None:
        """execute
        ---
        deny: [pass, return]
        ---
        Raise <selected_error> with message 'invalid input'.
        """

    with nh.run(execution_model=FunctionModel(respond)), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(events.append)):
        with pytest.raises(error_type if selection is not None else Exception, match="^invalid input$") as caught:
            if embedded:
                await nh.runtime.execute_step_async(
                    nh.runtime.StepDefinition(
                        "---\ndeny: [pass, return]\n---\nRaise <selected_error> with message 'invalid input'.",
                        input_binding_names=("selected_error",),
                    ),
                    name_to_value={"selected_error": error_type},
                    write_binding=lambda name, value: pytest.fail("Raising must not write bindings"),
                )
            else:
                await reject(error_type)
        assert len(events) == 1
        assert isinstance(events[0], nh.lifecycle.StepRaised)
        assert events[0].exception is caught.value
        assert type(caught.value) is (error_type if selection is not None else Exception)


def test_borrowed_agent_preserves_identity_without_constructing_a_managed_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    def unexpected(configuration):
        pytest.fail("Borrowing an agent must not construct a managed agent")

    monkeypatch.setattr("nighthawk.runtime.step_executor._create_execution_agent", unexpected)
    agent = FakeAgent()
    configuration = nh.StepExecutorConfiguration(execution_model="test:borrowed")
    step_executor = nh.AgentStepExecutor.from_agent(agent=agent, configuration=configuration)
    assert step_executor.agent is agent
    assert step_executor.configuration is configuration
    assert not step_executor.agent_is_managed


class RuntimeChildModel(BaseModel):
    value: int


class RuntimeResultModel(BaseModel, validate_assignment=True):
    child: RuntimeChildModel | None = None
    value: int = 0


def test_agent_executor_commits_write_binding_after_dotted_assignment() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.runtime import assign_expression

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_expression(deps, "result.value", "9")
            return FakeRunResult(ModelStepResponse(result=ModelPass(kind="pass")))

    with nh.run(nh.AgentStepExecutor.from_agent(agent=FakeAgent())):

        @nh.function
        def f() -> int:
            result: RuntimeResultModel = RuntimeResultModel(value=0)
            """execute
            <:result>
            Update the result value.
            """
            return result.value

        assert f() == 9


def test_agent_executor_records_run_result_usage() -> None:
    class FakeRunResult:
        output = ModelStepResponse(result=ModelPass(kind="pass"))
        usage = RunUsage(input_tokens=11, output_tokens=7)

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            assert deps is not None
            _ = user_prompt
            _ = kwargs
            return FakeRunResult()

    with nh.run(nh.AgentStepExecutor.from_agent(agent=FakeAgent())):

        @nh.function
        def f() -> None:
            """execute
            Say hi.
            """

        f()
        usage_meter = get_usage_meter()
        assert usage_meter is not None
        assert usage_meter.snapshot().total_tokens == 18


def test_agent_executor_passes_plain_string_to_text_only_custom_agent() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class RecordingAgent:
        def __init__(self) -> None:
            self.seen_prompt_type_list: list[type[object]] = []

        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            assert deps is not None
            _ = kwargs
            self.seen_prompt_type_list.append(type(user_prompt))
            return FakeRunResult(ModelStepResponse(result=ModelPass(kind="pass")))

    recording_agent = RecordingAgent()

    with nh.run(nh.AgentStepExecutor.from_agent(agent=recording_agent)):

        @nh.function
        def f() -> None:
            """execute
            Say hi.
            """

        f()

    assert recording_agent.seen_prompt_type_list == [str]


def test_agent_executor_passes_multimodal_tuple_to_custom_agent() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class RecordingAgent:
        def __init__(self) -> None:
            self.seen_prompt_list: list[object] = []

        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            assert deps is not None
            _ = kwargs
            self.seen_prompt_list.append(user_prompt)
            return FakeRunResult(ModelStepResponse(result=ModelPass(kind="pass")))

    recording_agent = RecordingAgent()
    image = BinaryContent(data=b"\x89PNG\r\n\x1a\n", media_type="image/png", identifier="img")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=recording_agent)):

        @nh.function
        def f(photo: BinaryContent) -> None:
            """execute
            Inspect <photo>.
            """

        f(image)

    assert len(recording_agent.seen_prompt_list) == 1
    seen_prompt = recording_agent.seen_prompt_list[0]
    assert isinstance(seen_prompt, tuple)
    assert any(isinstance(content, BinaryContent) for content in seen_prompt)


def test_natural_function_rejects_external_agent_model_switch() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class RecordingAgent:
        def __init__(self) -> None:
            self.seen_model_identifiers: list[str] = []

        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.runtime import assign_expression
            from nighthawk.runtime.model_outcome import ModelPass, ModelStepResponse

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            current_step_executor = nh.get_step_executor()
            assert isinstance(current_step_executor, nh.AgentStepExecutor)
            current_model_identifier = current_step_executor.configuration.execution_model
            assert isinstance(current_model_identifier, str)
            self.seen_model_identifiers.append(current_model_identifier)
            assign_expression(deps, "observed_model_identifier", repr(current_model_identifier))

            return FakeRunResult(ModelStepResponse(result=ModelPass(kind="pass")))

    initial_model_identifier = "openai-responses:gpt-6-luna"
    recording_agent = RecordingAgent()
    step_executor_configuration = nh.StepExecutorConfiguration(execution_model=initial_model_identifier)
    step_executor = nh.AgentStepExecutor.from_agent(
        agent=recording_agent,
        configuration=step_executor_configuration,
    )

    with nh.run(step_executor):

        @nh.function
        def f() -> tuple[str, str, str]:
            """execute
            <:observed_model_identifier>
            Record the current model identifier.
            """
            first_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            with nh.scope(step_executor_configuration=nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-6.1-sol")):
                """execute
                <:observed_model_identifier>
                Record the current model identifier.
                """
                second_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            """execute
            <:observed_model_identifier>
            Record the current model identifier.
            """
            third_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            return (
                first_model_identifier,
                second_model_identifier,
                third_model_identifier,
            )

        with pytest.raises(nh.NighthawkError, match="external agent owns model selection"):
            f()
        assert nh.get_step_executor() is step_executor

    assert recording_agent.seen_model_identifiers == [initial_model_identifier]


def test_external_agent_rejects_instance_configuration() -> None:
    class FakeAgent:
        def run_sync(self, *arguments, **keywords):
            raise AssertionError("No model execution expected")

    configuration = nh.StepExecutorConfiguration(execution_model=TestModel())
    with pytest.raises(nh.NighthawkError, match="external agent owns model selection"):
        nh.AgentStepExecutor.from_agent(configuration=configuration, agent=FakeAgent())


def test_agent_step_executor_receives_scoped_tools_in_toolset() -> None:
    def greet(target: str) -> str:
        return f"hello, {target}"

    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class FakeAgent:
        def __init__(self) -> None:
            self.seen_tool_names: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, toolsets=None, **kwargs):  # type: ignore[no-untyped-def]
            _ = (user_prompt, deps, kwargs)
            assert toolsets is not None
            self.seen_tool_names.append(",".join(sorted(toolsets[0].tools.keys())))
            return FakeRunResult(ModelStepResponse(result=ModelPass(kind="pass")))

    agent = FakeAgent()

    @nh.function
    def natural_pass_function() -> None:
        """execute
        Do nothing.
        """

    with nh.run(nh.AgentStepExecutor.from_agent(agent=agent)):
        natural_pass_function()
        with nh.scope(tools=[greet]):
            natural_pass_function()
        natural_pass_function()

    assert agent.seen_tool_names == [
        "nh_assign,nh_evaluate",
        "greet,nh_assign,nh_evaluate",
        "nh_assign,nh_evaluate",
    ]


class FakeAgent:
    def run_sync(self, *arguments, **keywords):
        raise AssertionError("No agent execution expected")


@pytest.mark.parametrize("invalid", [None, {}, {"model": "test:model"}, False, nh.Merge({})])
def test_executor_rejects_invalid_configuration_before_creating_agent(invalid: object, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(configuration):
        pytest.fail("Invalid configuration constructed an agent")

    monkeypatch.setattr("nighthawk.runtime.step_executor._create_execution_agent", unexpected)
    with pytest.raises(TypeError, match="configuration"):
        nh.AgentStepExecutor(configuration=invalid)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="configuration"):
        nh.AgentStepExecutor.from_agent(agent=FakeAgent(), configuration=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("borrowed", [False, True])
def test_executor_defaults_and_falsey_configuration_preserve_explicit_selection(borrowed: bool) -> None:
    class FalseyConfiguration(nh.StepExecutorConfiguration):
        def __bool__(self) -> bool:
            return False

    def construct(configuration: nh.StepExecutorConfiguration | nh.UnsetType = nh.UNSET) -> nh.AgentStepExecutor:
        return nh.AgentStepExecutor.from_agent(agent=FakeAgent(), configuration=configuration) if borrowed else nh.AgentStepExecutor(configuration)

    first = construct()
    second = construct(nh.UNSET)
    assert first.configuration == second.configuration
    assert first.configuration is not second.configuration
    configuration = FalseyConfiguration(execution_model_settings={"temperature": 0})
    assert construct(configuration).configuration is configuration
