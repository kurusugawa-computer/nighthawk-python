from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Annotated, Any, Literal

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai.messages import BinaryContent, ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai_decisions import OpenAIDecisionsModel
from pydantic_ai.providers.openai_decisions import OpenAIDecisionsProvider

import nighthawk as nh
from nighthawk.natural.blocks import parse_block
from nighthawk.runtime import Pass, StepCandidate, StepDefinition, execute_step
from tests.support.executors import InspectingExecutor


class DecisionsTransport(httpx2.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mode = "success"
        self.probability = 0.8

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/decisions"
        body = json.loads(request.content)
        self.requests.append(body)
        answers = []
        for question in body["questions"]:
            if self.mode == "missing":
                continue
            if self.mode == "refusal":
                answers.append({"name": question["name"], "type": "refusal"})
            elif question["type"] == "predicate":
                answers.append({"name": question["name"], "type": "predicate", "probability": self.probability})
            elif question["type"] == "choice":
                choices = question["choices"]
                answers.append(
                    {
                        "name": question["name"],
                        "type": "choice",
                        "choice": choices[0]["value"],
                        "confidence": 1.0,
                        "probabilities": [{"value": choice["value"], "probability": float(index == 0)} for index, choice in enumerate(choices)],
                    }
                )
            else:
                answers.append(
                    {
                        "name": question["name"],
                        "type": "score",
                        "score": 0.75,
                        "confidence": 0.5,
                        "probabilities": [
                            {"value": index, "label": level["label"], "probability": 0.25 if index == 0 else 0.75}
                            for index, level in enumerate(question["levels"])
                        ],
                    }
                )
        return httpx2.Response(
            200,
            json={
                "answers": answers,
                "model": body["model"],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 0,
                    "total_tokens": 12,
                    "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )


@pytest.fixture
def decision_backend() -> Iterator[tuple[OpenAIDecisionsModel, DecisionsTransport]]:
    transport = DecisionsTransport()
    client = AsyncOpenAI(api_key="synthetic-test-key", base_url="https://mock.invalid/v1", http_client=httpx2.AsyncClient(transport=transport))
    yield OpenAIDecisionsModel("gpt-6-luna", provider=OpenAIDecisionsProvider(openai_client=client)), transport
    asyncio.run(client.close())


@nh.function
def supported(document: str) -> bool:
    result: bool = False
    """decide <:result>
    Does <document> support the claim?
    """
    return result


@nh.function
async def supported_async(document: str) -> bool:
    result: bool
    """decide <:result>
    Does <document> support the claim?
    """
    return result  # noqa: F821  # pyright: ignore[reportUnboundVariable]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_decision_uses_typed_answer_full_inputs_and_per_step_details(decision_backend, asynchronous: bool) -> None:
    model, transport = decision_backend
    finished: list[nh.lifecycle.StepFinished] = []
    evidence = "unique evidence " * 2000 + "END_OF_EVIDENCE"
    with nh.run(decision_model=model), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished.append)):
        assert (asyncio.run(supported_async(evidence)) if asynchronous else supported(evidence)) is True
        assert supported("second") is True
        assert nh.get_usage_meter().total_tokens == 24
    assert len(finished) == 2
    assert finished[0].execution_reference.step_execution_id != finished[1].execution_reference.step_execution_id
    assert all(record.block_kind == "decide" for record in finished)
    first = finished[0]
    assert isinstance(first, nh.lifecycle.StepCompleted)
    assert first.assigned_binding_name_to_value == {"result": True}
    assert first.model_result is not None and first.model_result.output is True
    assert first.model_result.response.provider_details is not None
    assert first.model_result.response.provider_details["confidence"]
    assert evidence in transport.requests[0]["input"]
    assert "Does <document> support" in transport.requests[0]["questions"][0]["instructions"]
    assert "<:result>" not in json.dumps(transport.requests)
    assert "nh_assign" not in json.dumps(transport.requests)
    assert "False" not in transport.requests[0]["input"]


class Assessment(BaseModel):
    """Assess factual support."""

    supported: bool = Field(description="Is the claim supported?")
    probability: Annotated[float, Field(ge=0, le=1, description="Is the claim supported?")]
    route: Literal["accept", "review"] = Field(description="Which action is appropriate?")
    quality: Annotated[Literal[0], Field(description="Unsupported")] | Annotated[Literal[1], Field(description="Fully supported")]


@nh.function
def assess(document: str) -> Assessment:
    assessment: Assessment
    """decide <:assessment>
    Assess the evidence in <document>.
    """
    return assessment  # noqa: F821  # pyright: ignore[reportUnboundVariable]


def test_standard_pydantic_contracts_and_raw_scores(decision_backend) -> None:
    model, transport = decision_backend
    finished = []
    with nh.run(decision_model=model), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished.append)):
        assessment = assess("evidence")
    assert assessment == Assessment(supported=True, probability=0.8, route="accept", quality=1)
    assert len(transport.requests[0]["questions"]) == 4
    details = finished[0].model_result.response.provider_details
    assert details["scores"] == {"quality": 0.75}
    assert details["probabilities"]["route"] == {"accept": 1.0, "review": 0.0}


@pytest.mark.parametrize("mode,probability", [("missing", 0.8), ("refusal", 0.8), ("success", 2.0)])
def test_invalid_or_absent_answer_never_reuses_initialized_output(decision_backend, mode: str, probability: float) -> None:
    model, transport = decision_backend
    transport.mode, transport.probability = mode, probability
    finished = []
    with (
        nh.run(decision_model=model),
        nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished.append)),
        pytest.raises(nh.ExecutionError),
    ):
        supported("evidence")
    assert len(finished) == 1 and isinstance(finished[0], nh.lifecycle.StepFailed)
    assert not finished[0].assigned_binding_name_to_value


@pytest.mark.parametrize("bindings", [nh.UNSET, {}])
def test_custom_executor_cannot_substitute_existing_value(bindings) -> None:
    executor = InspectingExecutor(lambda request: StepCandidate(outcome=Pass(), binding_name_to_value=bindings))
    with nh.run(executor), pytest.raises(nh.ExecutionError, match="explicit answer"):
        supported("evidence")


def test_missing_or_incompatible_model_and_unsupported_type_fail_before_request(decision_backend) -> None:
    model, transport = decision_backend
    with nh.run(), pytest.raises(nh.ExecutionError, match="decision_model"):
        supported("evidence")
    with (
        nh.run(decision_model=FunctionModel(lambda messages, information: ModelResponse(parts=[]))),
        pytest.raises(nh.ExecutionError, match="DecisionModel"),
    ):
        supported("evidence")
    definition = StepDefinition("Write an essay.", block_kind="decide", output_binding_names=("answer",), binding_name_to_type={"answer": str})
    with nh.run(decision_model=model), pytest.raises(nh.ExecutionError):
        execute_step(definition, name_to_value={}, write_binding=lambda name, value: pytest.fail("unexpected write"))
    assert transport.requests == []


def test_models_inherit_independently_and_decision_settings_apply(decision_backend) -> None:
    model, transport = decision_backend
    configuration = nh.StepExecutorConfiguration(decision_model=model, decision_model_settings={"decision_boolean_threshold": 0.9})
    with nh.run(step_executor_configuration=configuration):
        parent = nh.get_step_executor()
        assert supported("evidence") is False
        with nh.scope(execution_model=FunctionModel(lambda messages, information: ModelResponse(parts=[]))):
            assert supported("evidence") is False
        assert nh.get_step_executor() is parent
        with nh.scope(decision_model=None), pytest.raises(nh.ExecutionError, match="decision_model"):
            supported("evidence")
        assert nh.get_step_executor() is parent
    assert len(transport.requests) == 2


def test_execute_multiple_assignments_then_decide_share_lifecycle(decision_backend) -> None:
    model, _ = decision_backend
    responses = iter(
        [
            [ToolCallPart("nh_assign", {"target_path": "claims", "expression": "['claim']"})],
            [ToolCallPart("nh_assign", {"target_path": "summary", "expression": "claims[0]"})],
            [ToolCallPart("nh_assign", {"target_path": "summary", "expression": "summary + ' verified'"})],
            [ToolCallPart("final_result", {"result": {"kind": "pass"}})],
        ]
    )
    execution_model = FunctionModel(lambda messages, information: ModelResponse(parts=next(responses)))

    @nh.function
    def workflow(document: str) -> tuple[list[str], str, bool]:
        claims: list[str] = []
        summary: str = ""
        """execute
        Extract <document> into <:claims>.
        Summarize <claims> into <:summary>, then revise <:summary>.
        """
        accepted: bool
        """decide <:accepted>
        Does <document> support <summary>?
        """
        return claims, summary, accepted  # noqa: F821  # pyright: ignore[reportUnboundVariable]

    finished = []
    with (
        nh.run(execution_model=execution_model, decision_model=model),
        nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished.append)),
    ):
        assert workflow("claim verified") == (["claim"], "claim verified", True)
    assert [record.block_kind for record in finished] == ["execute", "decide"]
    assert all(isinstance(record, nh.lifecycle.StepCompleted) for record in finished)


def test_embedded_decision_images_and_write_failure_keep_model_evidence(decision_backend) -> None:
    model, transport = decision_backend
    image = BinaryContent(data=b"synthetic-image", media_type="image/png")
    definition = StepDefinition(
        "Does <image> show a bird?",
        block_kind="decide",
        input_binding_names=("image",),
        output_binding_names=("answer",),
        binding_name_to_type={"answer": bool},
    )
    finished = []

    def fail_write(name, value):
        raise OSError("storage unavailable")

    with (
        nh.run(decision_model=model),
        nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished.append)),
        pytest.raises(nh.ExecutionError, match="storage unavailable"),
    ):
        execute_step(definition, name_to_value={"image": image}, write_binding=fail_write)
    assert "data:image/png;base64," in json.dumps(transport.requests[0]["input"])
    assert isinstance(finished[0], nh.lifecycle.StepFailed)
    assert finished[0].model_result is not None
    assert finished[0].model_result.output is True
    assert not finished[0].assigned_binding_name_to_value


@pytest.mark.parametrize("source", ["decide\nQuestion", "decide <:a> <:b>\nQuestion", "decide <:a>\nWrite <:a>.", "decide <:a>\n"])
def test_invalid_decision_declarations(source: str) -> None:
    with pytest.raises(nh.NaturalParseError):
        parse_block(source)


def test_decision_commit_oversight_requires_destination(decision_backend) -> None:
    model, _ = decision_backend
    finished = []
    with (
        nh.run(decision_model=model),
        nh.scope(
            lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished.append),
            oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(binding_name_to_value={})),
        ),
        pytest.raises(nh.ExecutionError, match="retain its answer"),
    ):
        supported("evidence")
    assert isinstance(finished[0], nh.lifecycle.StepFailed)
    assert finished[0].model_result is not None
    assert not finished[0].assigned_binding_name_to_value


def test_scoped_execution_tools_and_previews_do_not_enter_decision(decision_backend) -> None:
    model, transport = decision_backend

    def forbidden_action() -> None:
        pytest.fail("decision called an action")

    with (
        nh.run(decision_model=model),
        nh.scope(
            tools=[forbidden_action],
            task_references={"unreferenced": "PRIVATE_VALUE"},
            system_prompt_suffix_fragments=["EXECUTION_ONLY_SYSTEM"],
            user_prompt_suffix_fragments=["EXECUTION_ONLY_USER"],
        ),
    ):
        assert supported("evidence")
    wire = json.dumps(transport.requests)
    assert all(marker not in wire for marker in ("PRIVATE_VALUE", "EXECUTION_ONLY", "forbidden_action"))


def test_request_hooks_cannot_switch_decision_to_execution(decision_backend) -> None:
    from dataclasses import replace

    from pydantic_ai.capabilities import Hooks

    model, transport = decision_backend
    execution_model = FunctionModel(lambda messages, information: pytest.fail("execution request was sent"))

    async def switch(context, request):
        return replace(request, model=execution_model)

    with (
        nh.run(decision_model=model),
        nh.scope(capabilities=[Hooks(before_model_request=switch)]),
        pytest.raises(nh.ExecutionError, match="DecisionModel"),
    ):
        supported("evidence")
    assert transport.requests == []


def test_decision_header_is_static_and_fstring_body_keeps_binding_contract(decision_backend) -> None:
    model, _ = decision_backend

    @nh.function
    def judge(document: str, criterion: str) -> bool:
        result = False
        f"""decide <:result>
        Does <document> satisfy this criterion: {criterion}?
        """
        return result

    with nh.run(decision_model=model):
        assert judge("evidence", "is supported")
    with pytest.raises(nh.NaturalParseError, match="static"):

        @nh.function
        def invalid(name: str):
            pass
            f"""decide {name}
            Is this supported?
            """


def test_decision_escaped_markers_are_literal_instructions_without_evidence_lookup(decision_backend) -> None:
    model, transport = decision_backend

    @nh.function
    def judge() -> bool:
        result = False
        r"""decide <:result>
        Are \<missing>, \<:result>, and \<module.field> examples of angle-bracket syntax?
        """
        return result

    with nh.run(decision_model=model):
        assert judge() is True
    request = transport.requests[0]
    assert "No input bindings." in request["input"]
    assert "Are <missing>, <:result>, and <module.field>" in request["questions"][0]["instructions"]


def test_concurrent_decision_scopes_and_nested_runs_restore_selection(decision_backend) -> None:
    model, _ = decision_backend

    async def execute():
        with nh.run(decision_model=model):
            parent = nh.get_step_executor()

            async def child(threshold: float):
                configuration = nh.StepExecutorConfiguration(decision_model=model, decision_model_settings={"decision_boolean_threshold": threshold})
                with nh.scope(step_executor_configuration=configuration):
                    await asyncio.sleep(0)
                    result = await supported_async("evidence")
                assert nh.get_step_executor() is parent
                return result

            assert await asyncio.gather(child(0.5), child(0.9)) == [True, False]
            with nh.run(), pytest.raises(nh.ExecutionError, match="decision_model"):
                await supported_async("evidence")
            assert nh.get_step_executor() is parent
            assert await supported_async("evidence")

    asyncio.run(execute())


def test_nonserializable_evidence_fails_without_provider_request(decision_backend) -> None:
    model, transport = decision_backend
    definition = StepDefinition(
        "Is <evidence> sufficient?",
        block_kind="decide",
        input_binding_names=("evidence",),
        output_binding_names=("answer",),
        binding_name_to_type={"answer": bool},
    )
    with nh.run(decision_model=model), pytest.raises(nh.ExecutionError):
        execute_step(definition, name_to_value={"evidence": object()}, write_binding=lambda name, value: pytest.fail("unexpected write"))
    assert transport.requests == []


def test_cancelled_decision_is_interrupted_without_assignment(decision_backend) -> None:
    model, transport = decision_backend
    finished = []

    async def execute():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def wait_for_transport(request):
            entered.set()
            await release.wait()
            raise AssertionError("cancelled request resumed")

        transport.handle_async_request = wait_for_transport
        with nh.run(decision_model=model), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished.append)):
            task = asyncio.create_task(supported_async("evidence"))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert nh.get_usage_meter().total_tokens == 0

    asyncio.run(execute())
    assert len(finished) == 1 and isinstance(finished[0], nh.lifecycle.StepInterrupted)
    assert finished[0].block_kind == "decide"
    assert finished[0].model_result is None
    assert not finished[0].assigned_binding_name_to_value


def test_scripted_decision_reports_block_kind_and_explicit_evidence() -> None:
    from nighthawk.testing import ScriptedExecutor, pass_response

    executor = ScriptedExecutor([pass_response(binding_name_to_value={"result": True})])
    with nh.run(executor):
        assert supported("evidence")
    assert executor.calls[0].block_kind == "decide"
    assert executor.calls[0].input_binding_name_to_value == {"document": "evidence"}
