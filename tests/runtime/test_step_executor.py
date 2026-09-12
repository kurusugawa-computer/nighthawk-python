import pytest
from pydantic import BaseModel
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.runtime.scoping import get_usage_meter
from nighthawk.runtime.step_contract import PassStepOutcome, StepFinalResult


def test_agent_step_executor_constructor_supports_standard_path_with_agent() -> None:
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    step_executor = nh.AgentStepExecutor(agent=FakeAgent())
    assert step_executor.configuration.model == "openai-responses:gpt-5.6-luna"


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
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_tool(deps, "result.value", "9")
            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    with nh.run(nh.AgentStepExecutor.from_agent(agent=FakeAgent())):

        @nh.natural_function
        def f() -> int:
            result: RuntimeResultModel = RuntimeResultModel(value=0)
            """natural
            <:result>
            Update the result value.
            """
            return result.value

        assert f() == 9


def test_agent_executor_records_run_result_usage() -> None:
    class FakeRunResult:
        output = StepFinalResult(result=PassStepOutcome(kind="pass"))
        usage = RunUsage(input_tokens=11, output_tokens=7)

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            assert deps is not None
            _ = user_prompt
            _ = kwargs
            return FakeRunResult()

    with nh.run(nh.AgentStepExecutor.from_agent(agent=FakeAgent())):

        @nh.natural_function
        def f() -> None:
            """natural
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
            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    recording_agent = RecordingAgent()

    with nh.run(nh.AgentStepExecutor.from_agent(agent=recording_agent)):

        @nh.natural_function
        def f() -> None:
            """natural
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
            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    recording_agent = RecordingAgent()
    image = BinaryContent(data=b"\x89PNG\r\n\x1a\n", media_type="image/png", identifier="img")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=recording_agent)):

        @nh.natural_function
        def f(photo: BinaryContent) -> None:
            """natural
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
            from nighthawk.runtime.step_contract import PassStepOutcome, StepFinalResult
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            current_step_executor = nh.get_step_executor()
            assert isinstance(current_step_executor, nh.AgentStepExecutor)
            current_model_identifier = current_step_executor.configuration.model
            assert isinstance(current_model_identifier, str)
            self.seen_model_identifiers.append(current_model_identifier)
            assign_tool(deps, "observed_model_identifier", repr(current_model_identifier))

            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    initial_model_identifier = "openai-responses:gpt-5.6-luna"
    recording_agent = RecordingAgent()
    step_executor_configuration = nh.StepExecutorConfiguration(model=initial_model_identifier)
    step_executor = nh.AgentStepExecutor.from_agent(
        agent=recording_agent,
        configuration=step_executor_configuration,
    )

    with nh.run(step_executor):

        @nh.natural_function
        def f() -> tuple[str, str, str]:
            """natural
            <:observed_model_identifier>
            Record the current model identifier.
            """
            first_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            with nh.scope(step_executor_configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-terra")):
                """natural
                <:observed_model_identifier>
                Record the current model identifier.
                """
                second_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            """natural
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
    configuration = nh.StepExecutorConfiguration(model=TestModel())
    for construct in (nh.AgentStepExecutor, nh.AgentStepExecutor.from_agent):
        with pytest.raises(nh.NighthawkError, match="external agent owns model selection"):
            construct(configuration=configuration, agent=object())  # type: ignore[arg-type]
