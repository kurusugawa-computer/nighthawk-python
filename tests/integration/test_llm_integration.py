import os
from pathlib import Path
from typing import Literal

import logfire
import pytest
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.messages import BinaryContent

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from tests.integration.skip_helpers import requires_openai_integration

logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()


def _requires_openai_multimodal_integration():  # type: ignore[no-untyped-def]
    if os.getenv("NIGHTHAWK_OPENAI_MULTIMODAL_INTEGRATION_TESTS") != "1":
        pytest.skip("OpenAI multimodal integration tests are disabled")
    return requires_openai_integration()


def _build_single_pixel_png(*, red: int, green: int, blue: int) -> bytes:
    import struct
    import zlib

    def build_chunk(chunk_type: bytes, payload: bytes) -> bytes:
        import binascii

        chunk = chunk_type + payload
        return struct.pack(">I", len(payload)) + chunk + struct.pack(">I", binascii.crc32(chunk) & 0xFFFFFFFF)

    pixel_bytes = bytes([0, red, green, blue])
    compressed_pixel_bytes = zlib.compress(pixel_bytes)
    ihdr_payload = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            build_chunk(b"IHDR", ihdr_payload),
            build_chunk(b"IDAT", compressed_pixel_bytes),
            build_chunk(b"IEND", b""),
        ]
    )


class PixelColorClassification(BaseModel):
    first: Literal["red", "green", "blue"]
    second: Literal["red", "green", "blue"]
    third: Literal["red", "green", "blue"]


@nh.natural_function
def classify_pixel_colors(
    first_image: BinaryContent,
    second_image: BinaryContent,
    third_image: BinaryContent,
) -> PixelColorClassification:
    result = PixelColorClassification(
        first="blue",
        second="red",
        third="green",
    )
    """natural
    Inspect <first_image>, <second_image>, and <third_image> as actual images.
    These are three different single-color PNG images.
    Set <:result> so that `first`, `second`, and `third` are the lowercase color names of those images.
    Use only the words "red", "green", and "blue".
    """
    return result


@pytest.mark.asyncio
async def test_async_function_call():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="high")
        ),
    )
    with nh.run(step_executor):

        @nh.natural_function
        async def test_function():
            async def calculate(a: int, b: int) -> int:
                return a + b * 8

            """natural
            ---
            deny: [pass, raise]
            ---
            return the result of the `await calculate(1,2)` function call.
            """

        assert (await test_function()) == 17


def test_multiple_blocks_one_call_scope():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def f() -> int:
            x = 1
            """natural
            Set <:x> to <x> + 10.
            """

            """natural
            Set <:x> to <x> + 20.
            """

            return x

        assert f() == 31


def test_system_prompt_suffix_fragments():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor), nh.scope(system_prompt_suffix_fragments=["Hello suffix"]):

        @nh.natural_function
        def f() -> int:
            x = 1
            """natural
                Set <:x> to <x> + 10.
                """

            return x

        assert f() == 11


def test_user_prompt_suffix_fragments():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor), nh.scope(user_prompt_suffix_fragments=["Hello suffix"]):

        @nh.natural_function
        def f() -> int:
            x = 1
            """natural
                Set <:x> to <x> + 10.
                """

            return x

        assert f() == 11


def test_tool_visibility_scopes():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor):

        @nh.tool(name="hello")
        def hello(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "hello"

        with nh.scope(), nh.scope():

            @nh.natural_function
            def f() -> str:
                """natural
                Call hello.
                """
                return ""

            f()


def test_provided_tools_smoke():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor):

        @nh.tool(name="my_tool")
        def my_tool(run_context: RunContext[StepContext]) -> int:  # type: ignore[no-untyped-def]
            _ = run_context
            return 1

        @nh.natural_function
        def f() -> int:
            """natural
            Call my_tool() and set <:result> to my_tool() + 2.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f() == 3


def test_session_isolation(tmp_path):
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor):

        @nh.tool(name="tmp_write")
        def tmp_write(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            path = Path(tmp_path) / "hello.txt"
            path.write_text("hello", encoding="utf-8")
            return str(path)

        @nh.natural_function
        def f() -> str:
            """natural
            Call tmp_write() to create a file. Set <:result> to the return value of tmp_write().
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        result = f()
        assert "hello" in result


def test_provided_tools_do_not_leak_into_outer_scope(tmp_path):
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor):

        @nh.tool(name="tmp_write")
        def tmp_write(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            path = Path(tmp_path) / "hello.txt"
            path.write_text("hello", encoding="utf-8")
            return str(path)

        with nh.scope():

            @nh.natural_function
            def f() -> str:
                """natural
                Call tmp_write() and set <:result> to its return value.
                """
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            result = f()

    assert isinstance(result, str)


def test_provider_backed_executor_accepts_native_multimodal_user_prompt_content():
    openai_responses_model_settings_class = _requires_openai_multimodal_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-mini",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="high"),
        ),
    )

    first_image = BinaryContent(
        data=_build_single_pixel_png(red=255, green=0, blue=0),
        media_type="image/png",
        identifier="first_image",
    )
    second_image = BinaryContent(
        data=_build_single_pixel_png(red=0, green=255, blue=0),
        media_type="image/png",
        identifier="second_image",
    )
    third_image = BinaryContent(
        data=_build_single_pixel_png(red=0, green=0, blue=255),
        media_type="image/png",
        identifier="third_image",
    )

    with nh.run(step_executor):
        result = classify_pixel_colors(
            first_image=first_image,
            second_image=second_image,
            third_image=third_image,
        )

    assert result == PixelColorClassification(
        first="red",
        second="green",
        third="blue",
    )


def test_provider_backed_executor_accepts_native_multimodal_tool_result_content():
    openai_responses_model_settings_class = _requires_openai_multimodal_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-mini",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="high"),
        ),
    )

    @nh.tool(name="load_pixel_color_gallery")
    def load_pixel_color_gallery(run_context: RunContext[StepContext]) -> list[object]:  # type: ignore[no-untyped-def]
        _ = run_context
        return [
            "first",
            BinaryContent(
                data=_build_single_pixel_png(red=255, green=0, blue=0),
                media_type="image/png",
                identifier="first_tool_image",
            ),
            "second",
            BinaryContent(
                data=_build_single_pixel_png(red=0, green=255, blue=0),
                media_type="image/png",
                identifier="second_tool_image",
            ),
            "third",
            BinaryContent(
                data=_build_single_pixel_png(red=0, green=0, blue=255),
                media_type="image/png",
                identifier="third_tool_image",
            ),
        ]

    @nh.natural_function
    def classify_tool_returned_pixel_colors() -> PixelColorClassification:
        result = PixelColorClassification(
            first="blue",
            second="red",
            third="green",
        )
        """natural
        Call the tool `load_pixel_color_gallery()` directly. Do not use `nh_eval` to access it.
        The tool result contains three labeled images in this order: first, second, third.
        Determine the colors from the actual returned images, not from defaults.
        Set <:result> so that `first`, `second`, and `third` are the lowercase color names of those images.
        Use only the words "red", "green", and "blue".
        """
        return result

    with nh.run(step_executor):
        result = classify_tool_returned_pixel_colors()

    assert result == PixelColorClassification(
        first="red",
        second="green",
        third="blue",
    )
