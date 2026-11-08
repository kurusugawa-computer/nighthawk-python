from __future__ import annotations

import ast
import asyncio
import re
from collections import namedtuple
from contextlib import nullcontext
from typing import Any

import anyio
import pytest
import tiktoken
from pydantic_ai import Agent, RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    ModelRequest,
    ToolReturnPart,
    tool_return_ta,
)
from pydantic_ai.messages import (
    TextContent as PydanticTextContent,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.presentation import ToolResultRenderingPolicy, rendering_scope
from nighthawk.adapters.pydantic_ai.tools.contracts import (
    ToolOutcome,
)
from nighthawk.adapters.pydantic_ai.tools.execution import ToolResultWrapperToolset, _normalize_tool_success, _reference_path
from nighthawk.adapters.pydantic_ai.tools.provided import get_provided_tools
from nighthawk.core import oversight
from nighthawk.core.environment import StepEnvironment
from nighthawk.core.operation_contract import OperationRequest
from nighthawk.core.step_context import StepContext
from tests.support.executors import StubExecutor
from tests.support.prompts import build_step_context
from tests.support.tools import _new_run_context, _new_step_context

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _call_wrapped_tool(
    *,
    name: str,
    function: Any,
    tool_args: dict[str, object] | None = None,
    inherit_run: bool = False,
) -> object:
    run_context = _new_run_context()
    toolset = FunctionToolset([function])
    wrapped_toolset = ToolResultWrapperToolset(toolset)

    async def run() -> object:
        tool_name_to_tool = await wrapped_toolset.get_tools(run_context)
        tool = tool_name_to_tool[name]
        with nullcontext() if inherit_run else nh.run(StubExecutor()), set_current_run_context(run_context):
            return await wrapped_toolset.call_tool(name, tool_args or {}, run_context, tool)

    return anyio.run(run)


def _call_wrapped_tool_outcome(
    *,
    name: str,
    function: Any,
    tool_args: dict[str, object] | None = None,
    inherit_run: bool = False,
) -> ToolOutcome:
    run_context = _new_run_context()
    toolset = FunctionToolset([function])
    wrapped_toolset = ToolResultWrapperToolset(toolset)

    async def run() -> ToolOutcome:
        tool_name_to_tool = await wrapped_toolset.get_tools(run_context)
        tool = tool_name_to_tool[name]
        with nullcontext() if inherit_run else nh.run(StubExecutor()), set_current_run_context(run_context):
            return await wrapped_toolset.call_tool_outcome(name, tool_args or {}, run_context, tool)

    return anyio.run(run)


def test_wrapper_returns_success_payload_with_placeholder_for_unknown_value() -> None:
    class NotJson:
        pass

    def test_unknown_return() -> object:
        return NotJson()

    result = _call_wrapped_tool(name="test_unknown_return", function=test_unknown_return)
    assert result == "<opaque NotJson>"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n", "<bytes; 8 bytes>"),
        (bytearray(b"\x89PNG\r\n\x1a\n"), "<bytearray; 8 bytes>"),
        (["caption", b"\x89PNG\r\n\x1a\n"], "['caption', <bytes; 8 bytes>]"),
        (("caption", b"\x89PNG\r\n\x1a\n"), "('caption', <bytes; 8 bytes>)"),
    ],
)
def test_wrapper_returns_success_payload_with_placeholder_for_raw_bytes(payload: object, expected: object) -> None:
    def test_bytes_return() -> object:
        return payload

    result = _call_wrapped_tool(name="test_bytes_return", function=test_bytes_return)
    assert result == expected


def test_wrapper_call_tool_outcome_reports_application_exception() -> None:
    def test_raises() -> object:
        raise RuntimeError("boom")

    tool_outcome = _call_wrapped_tool_outcome(name="test_raises", function=test_raises)
    error = tool_outcome["error"]
    assert tool_outcome["payload"] is None and error is not None
    assert error["kind"] == "exception" and error["message"] == "RuntimeError: boom" and error.get("evaluation_started") is True


def test_wrapper_call_tool_outcome_preserves_tool_failed_signal() -> None:
    def test_fails() -> object:
        raise ToolFailed("resource missing")

    with pytest.raises(ToolFailed, match="resource missing"):
        _call_wrapped_tool_outcome(name="test_fails", function=test_fails)


def test_wrapper_call_tool_outcome_normalizes_bare_text_content_to_string() -> None:
    def test_text_content_return() -> object:
        return PydanticTextContent(content="hello")

    tool_outcome = _call_wrapped_tool_outcome(name="test_text_content_return", function=test_text_content_return)

    assert tool_outcome["error"] is None
    assert tool_outcome["payload"] == "hello"


def test_wrapper_call_tool_outcome_normalizes_text_content_in_multimodal_payload() -> None:
    def test_mixed_return() -> list[object]:
        return [
            PydanticTextContent(content="before"),
            BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
            PydanticTextContent(content="after"),
        ]

    tool_outcome = _call_wrapped_tool_outcome(name="test_mixed_return", function=test_mixed_return)

    assert tool_outcome["error"] is None
    payload = tool_outcome["payload"]
    assert isinstance(payload, list)
    assert payload[0] == "before"
    assert isinstance(payload[1], BinaryContent)
    assert payload[2] == "after"


def test_wrapper_call_tool_outcome_does_not_treat_namedtuple_as_top_level_multimodal_sequence() -> None:
    NamedImageReport = namedtuple("NamedImageReport", ["caption", "image"])

    def test_namedtuple_return() -> object:
        return NamedImageReport(
            caption="before",
            image=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
        )

    tool_outcome = _call_wrapped_tool_outcome(name="test_namedtuple_return", function=test_namedtuple_return)

    assert tool_outcome["error"] is None
    payload = tool_outcome["payload"]
    assert isinstance(payload, str)
    assert payload.startswith("NamedImageReport(caption='before', image=BinaryContent(data=<bytes; 8 bytes>, media_type='image/png'")
    assert "kind='binary'" in payload


def test_wrapper_call_tool_reports_application_exception() -> None:
    def test_raises() -> object:
        raise RuntimeError("boom")

    result = _call_wrapped_tool(name="test_raises", function=test_raises)
    assert isinstance(result, str) and result.startswith("error = {'kind': 'exception'")
    assert "'message': 'RuntimeError: boom'" in result


def test_wrapper_call_tool_outcome_propagates_oversight_abort() -> None:
    tool_call_count = 0

    def test_denied_tool(run_context) -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        _ = run_context
        tool_call_count += 1
        return 1

    def reject_tool_call(tool_call: OperationRequest) -> oversight.AbortStep:
        assert tool_call.tool_name == "test_denied_tool"
        return oversight.AbortStep("human rejected tool")

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=oversight.Oversight(inspect_operation=reject_tool_call)),
        pytest.raises(oversight.StepAbortedError, match="human rejected tool"),
    ):
        _call_wrapped_tool_outcome(name="test_denied_tool", function=test_denied_tool, inherit_run=True)
    assert tool_call_count == 0


def test_wrapper_call_tool_propagates_oversight_abort() -> None:
    def test_denied_tool(run_context) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return 1

    def reject_tool_call(tool_call: OperationRequest) -> oversight.AbortStep:
        assert tool_call.tool_name == "test_denied_tool"
        return oversight.AbortStep("human rejected tool")

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=oversight.Oversight(inspect_operation=reject_tool_call)),
        pytest.raises(oversight.StepAbortedError, match="human rejected tool"),
    ):
        _call_wrapped_tool(name="test_denied_tool", function=test_denied_tool, inherit_run=True)


def test_wrapper_preserves_top_level_binary_content_as_file() -> None:
    def test_image_return() -> object:  # type: ignore[no-untyped-def]
        return BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")

    result = _call_wrapped_tool(name="test_image_return", function=test_image_return)
    tool_return_part = ToolReturnPart(tool_name="test_image_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "image/png"


def test_wrapper_preserves_list_binary_content_as_file() -> None:
    def test_mixed_return() -> list[object]:  # type: ignore[no-untyped-def]
        return ["caption", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")]

    result = _call_wrapped_tool(name="test_mixed_return", function=test_mixed_return)
    tool_return_part = ToolReturnPart(tool_name="test_mixed_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "image/png"
    dumped = tool_return_ta.dump_python(tool_return_part.content, mode="json")
    assert dumped[0] == "caption"


def test_wrapper_preserves_tuple_binary_content_as_file() -> None:
    def test_tuple_return() -> tuple[str, object]:
        return ("caption", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"))

    result = _call_wrapped_tool(name="test_tuple_return", function=test_tuple_return)
    assert isinstance(result, list)
    tool_return_part = ToolReturnPart(tool_name="test_tuple_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "image/png"
    dumped = tool_return_ta.dump_python(tool_return_part.content, mode="json")
    assert dumped[0] == "caption"


def test_wrapper_preserves_top_level_audio_url_as_file() -> None:
    def test_audio_return() -> object:  # type: ignore[no-untyped-def]
        return AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg")

    result = _call_wrapped_tool(name="test_audio_return", function=test_audio_return)
    tool_return_part = ToolReturnPart(tool_name="test_audio_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "audio/mpeg"


def test_wrapper_does_not_promote_nested_binary_content_to_file() -> None:
    def test_nested_binary() -> dict[str, object]:  # type: ignore[no-untyped-def]
        return {
            "value": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
            "error": None,
        }

    result = _call_wrapped_tool(name="test_nested_binary", function=test_nested_binary)
    tool_return_part = ToolReturnPart(tool_name="test_nested_binary", content=result)
    assert tool_return_part.files == []
    assert isinstance(result, str) and result.startswith("{'value': BinaryContent(data=<bytes; 8 bytes>")
    assert "kind='binary'" in result and result.endswith("'error': None}")


def test_wrapper_preserves_none_return_as_success_without_error() -> None:
    def test_none_return() -> None:  # type: ignore[no-untyped-def]
        return None

    tool_return_part = ToolReturnPart(
        tool_name="test_none_return",
        content=_call_wrapped_tool(name="test_none_return", function=test_none_return),
    )
    assert tool_return_part.content is None
    assert tool_return_part.files == []


def test_wrapper_call_tool_shim_preserves_error_shaped_success_payload() -> None:
    def test_error_shaped_success() -> dict[str, str]:
        return {
            "kind": "user_requested",
            "message": "Operation completed with status",
            "guidance": "Next step is to review",
        }

    result = _call_wrapped_tool(name="test_error_shaped_success", function=test_error_shaped_success)
    assert result == "{'kind': 'user_requested', 'message': 'Operation completed with status', 'guidance': 'Next step is to review'}"


def test_provider_tool_loop_reports_application_failure_and_continues() -> None:
    tool_call_count = 0

    def test_failing_tool() -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        tool_call_count += 1
        raise RuntimeError("tool crashed")

    step_context = _new_step_context()
    wrapped_toolset = ToolResultWrapperToolset(FunctionToolset([test_failing_tool]))

    agent = Agent(
        model=TestModel(call_tools="all", custom_output_text="done"),
        deps_type=StepContext,
        output_type=str,
    )

    async def run_agent() -> tuple[object, list[ModelRequest]]:
        result = await agent.run("hello", deps=step_context, toolsets=[wrapped_toolset])
        model_request_list = [message for message in result.all_messages() if isinstance(message, ModelRequest)]
        return result.output, model_request_list

    with nh.run(StubExecutor()):
        output, model_request_list = anyio.run(run_agent)
    assert output == "done" and tool_call_count == 1
    returned = [part.content for request in model_request_list for part in request.parts if isinstance(part, ToolReturnPart)]
    assert len(returned) == 1 and isinstance(returned[0], str)
    assert returned[0].startswith("error = {'kind': 'exception'") and "'message': 'RuntimeError: tool crashed'" in returned[0]
    assert step_context._operation_state.failure is None


_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_provider_backed_tool_return_preserves_mixed_list_order_via_content_items() -> None:
    """Provider-backed models read ``ToolReturnPart.content_items``; ``resolve_tool_return_segments`` short-circuits only the MCP path."""
    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img-1")
    normalized = _normalize_tool_success(["caption", image, "tail"], step_context=_new_step_context())

    tool_return_part = ToolReturnPart(tool_name="sample_tool", content=normalized["payload"])
    items = tool_return_part.content_items(mode="str")

    assert items == ["caption", image, "tail"]


def test_tool_success_preserves_multimodal_items_in_list_subclass() -> None:
    class MediaList(list[object]):
        pass

    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img-1")

    normalized = _normalize_tool_success(MediaList(["caption", image, "tail"]), step_context=_new_step_context())

    assert normalized["payload"] == ["caption", image, "tail"]
    tool_return_part = ToolReturnPart(tool_name="sample_tool", content=normalized["payload"])
    assert tool_return_part.content_items(mode="str") == ["caption", image, "tail"]


def test_provider_evaluation_keeps_discovery_references_and_pagination() -> None:
    context = build_step_context(python_globals={}, python_locals={})
    context.environment = StepEnvironment(
        python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)}
    )
    policy = ToolResultRenderingPolicy("o200k_base", 128, 8)

    async def execute() -> None:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(FunctionToolset(get_provided_tools()))
        tool_name_to_tool = await toolset.get_tools(run_context)
        result: Any = await toolset.call_tool(
            "nh_evaluate",
            {"expression": f"{context.environment.runtime_reference}.environment.discover(source='task')"},
            run_context,
            tool_name_to_tool["nh_evaluate"],
        )
        assert isinstance(result, str)
        description = ast.literal_eval(result)
        assert description["metadata_omitted"] is True
        namespace = context.environment._expression_namespace()
        assert eval(description["continuation"], namespace, namespace)["names"][0]["name"] == "capability_01"
        assert eval(description["references"][0], namespace, namespace) is context.environment.task_name_to_value["capability_00"]

    with rendering_scope(policy):
        asyncio.run(execute())


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("request", "request"), ("o.b[0]['key']", "o.b[0]['key']"), ("f(x)", None), ("items[-1]", None), ("items[1:]", None), ("await value", None)],
)
def test_only_name_attribute_and_constant_subscript_expressions_name_the_result(expression: str, expected: str | None) -> None:
    assert _reference_path(expression) == expected


def test_provider_evaluation_bounds_text_and_states_where_to_continue() -> None:
    context = build_step_context(python_globals={}, python_locals={"request": "line of the request\n" * 2_000})
    policy = ToolResultRenderingPolicy("o200k_base", 200, 8)

    async def execute() -> str:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(FunctionToolset(get_provided_tools()))
        tool_name_to_tool = await toolset.get_tools(run_context)
        result = await toolset.call_tool("nh_evaluate", {"expression": "request"}, run_context, tool_name_to_tool["nh_evaluate"])
        assert isinstance(result, str)
        return result

    with rendering_scope(policy):
        result = asyncio.run(execute())
    shown, marker = result.rsplit("\n", 1)
    assert len(tiktoken.get_encoding("o200k_base").encode(result)) <= 200
    assert marker == f"<+{40_000 - len(shown)} characters; request[{len(shown)}:]>"


def _continuation(result: str) -> str:
    match = re.search(r"<\+\d+ (?:characters|items|entries); ([^>]+)>", result)
    assert match is not None, result
    return match[1]


def test_call_results_keep_omitted_parts_readable_without_repeating_the_call() -> None:
    call_list: list[object] = []

    def load_report() -> str:
        report = "".join(f"line {index}\n" for index in range(2_000))
        call_list.append(report)
        return report

    def list_rows() -> list[dict[str, int]]:
        row_list = [{"row": index} for index in range(300)]
        call_list.append(row_list)
        return row_list

    context = build_step_context(python_globals={}, python_locals={"load_report": load_report, "list_rows": list_rows})
    policy = ToolResultRenderingPolicy("o200k_base", 200, 8)

    async def execute() -> None:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(FunctionToolset(get_provided_tools()))
        evaluate = (await toolset.get_tools(run_context))["nh_evaluate"]

        async def evaluate_text(expression: str) -> str:
            result = await toolset.call_tool("nh_evaluate", {"expression": expression}, run_context, evaluate)
            assert isinstance(result, str)
            return result

        report_text = await evaluate_text("load_report()")
        report_continuation = _continuation(report_text)
        assert report_continuation.startswith("nh_results[0][")
        shown = report_text.rsplit("\n", 1)[0]
        rest = await evaluate_text(report_continuation)
        assert call_list[0][len(shown) :].startswith(rest.rsplit("\n", 1)[0])  # type: ignore[index]
        assert _continuation(rest).startswith("nh_results[1][")

        assert await evaluate_text("len(list_rows())") == "300"
        assert context.environment._next_result_path() == "nh_results[2]"

        row_continuation = _continuation(await evaluate_text("list_rows()"))
        assert row_continuation.startswith("nh_results[2][")
        namespace = context.environment._expression_namespace()
        retained = namespace["nh_results"]
        assert isinstance(retained, tuple) and retained[0] is call_list[0] and retained[2] is call_list[2]
        omitted_start = int(row_continuation.removeprefix("nh_results[2][").removesuffix(":]"))
        assert eval(row_continuation, namespace, namespace) == call_list[2][omitted_start:]  # type: ignore[index]
        assert len(call_list) == 3

    with rendering_scope(policy):
        asyncio.run(execute())


def test_native_tool_results_and_self_references_name_the_retained_result() -> None:
    fetch_count = 0

    def fetch_document() -> str:
        """Return the document."""
        nonlocal fetch_count
        fetch_count += 1
        return "word " * 5_000

    def build_loop() -> list[object]:
        """Return a list that contains itself."""
        loop: list[object] = ["start"]
        loop.append(loop)
        return loop

    context = build_step_context(python_globals={}, python_locals={})
    policy = ToolResultRenderingPolicy("o200k_base", 200, 8)

    async def execute() -> None:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        native_toolset = ToolResultWrapperToolset(FunctionToolset([fetch_document, build_loop]))
        native_tool_name_to_tool = await native_toolset.get_tools(run_context)
        with nh.run(StubExecutor()), set_current_run_context(run_context):
            document = await native_toolset.call_tool("fetch_document", {}, run_context, native_tool_name_to_tool["fetch_document"])
            loop_text = await native_toolset.call_tool("build_loop", {}, run_context, native_tool_name_to_tool["build_loop"])
        assert isinstance(document, str) and isinstance(loop_text, str)
        assert _continuation(document).startswith("nh_results[0][")
        assert loop_text == "['start', nh_results[1]]"

        provided_toolset = ToolResultWrapperToolset(FunctionToolset(get_provided_tools()))
        evaluate = (await provided_toolset.get_tools(run_context))["nh_evaluate"]
        rest = await provided_toolset.call_tool("nh_evaluate", {"expression": _continuation(document)}, run_context, evaluate)
        assert isinstance(rest, str) and rest.strip().startswith("word")
        identity = await provided_toolset.call_tool("nh_evaluate", {"expression": "nh_results[1][1] is nh_results[1]"}, run_context, evaluate)
        assert identity == "True"
        assert fetch_count == 1

    with rendering_scope(policy):
        asyncio.run(execute())


def test_path_results_and_complete_results_are_not_retained() -> None:
    context = build_step_context(python_globals={}, python_locals={"request": "text " * 2_000, "small": [1, 2]})
    policy = ToolResultRenderingPolicy("o200k_base", 200, 8)

    async def execute() -> None:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(FunctionToolset(get_provided_tools()))
        evaluate = (await toolset.get_tools(run_context))["nh_evaluate"]
        for expression in ("request", "list(small)", "len(request)"):
            result = await toolset.call_tool("nh_evaluate", {"expression": expression}, run_context, evaluate)
            assert isinstance(result, str) and "nh_results" not in result
        error = await toolset.call_tool("nh_evaluate", {"expression": "1 / 0"}, run_context, evaluate)
        assert isinstance(error, str) and error.startswith("error = ")
        assert context.environment._next_result_path() == "nh_results[0]"

    with rendering_scope(policy):
        asyncio.run(execute())
