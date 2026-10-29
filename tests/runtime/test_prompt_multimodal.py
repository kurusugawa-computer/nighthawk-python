import builtins
from collections import namedtuple
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai.messages import AudioUrl, BinaryContent, ImageUrl, TextContent, UploadedFile

import nighthawk as nh
from tests.support.prompts import (
    build_step_context,
    build_user_prompt_content,
    build_user_prompt_text,
    locals_section,
    prompt_content_to_text,
)

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_build_user_prompt_returns_tuple_for_text_only_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"text": "hello"},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    assert isinstance(prompt_content, tuple)
    assert all(isinstance(content, str) for content in prompt_content)
    assert f"{step_context.environment.runtime_reference}.inspect(value)" in prompt_content_to_text(prompt_content)


def test_locals_section_inlines_binary_content_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("photo: BinaryContent  # attached content: ")
    content_after_photo = prompt_content[photo_index + 1]
    assert isinstance(content_after_photo, str)
    assert content_after_photo.startswith("\n<<<NH:END_LOCALS>>>")
    assert "photo: BinaryContent  # attached content: <image>" in prompt_content_to_text(prompt_content)


def test_globals_section_inlines_image_url_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "photo": ImageUrl(url="https://example.com/cat.png")},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, ImageUrl))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("photo: ImageUrl  # attached content: ")
    assert prompt_content_to_text(prompt_content).endswith("photo: ImageUrl  # attached content: <image>\n<<<NH:END_GLOBALS>>>\n")


def test_locals_section_inlines_uploaded_file_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"photo": UploadedFile(file_id="file-123", provider_name="openai", media_type="image/png")},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, UploadedFile))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("photo: UploadedFile  # attached content: ")
    assert "photo: UploadedFile  # attached content: <image>" in prompt_content_to_text(prompt_content)


def test_locals_section_inlines_audio_url_as_file_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"clip": AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg")},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <clip>.",
        step_context=step_context,
    )

    clip_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, AudioUrl))
    content_before_clip = prompt_content[clip_index - 1]
    assert isinstance(content_before_clip, str)
    assert content_before_clip.endswith("clip: AudioUrl  # attached content: ")
    assert "clip: AudioUrl  # attached content: <file>" in prompt_content_to_text(prompt_content)


def test_locals_section_keeps_unreferenced_top_level_multimodal_bindings_by_design() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "extra": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
            "photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
        },
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    assert sum(1 for content in prompt_content if isinstance(content, BinaryContent)) == 2
    prompt_text = prompt_content_to_text(prompt_content)
    assert "extra: BinaryContent  # attached content: <image>" in prompt_text
    assert "photo: BinaryContent  # attached content: <image>" in prompt_text


def test_locals_section_inlines_top_level_user_content_sequence_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"gallery": ["prefix ", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"), " suffix"]},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <gallery>.",
        step_context=step_context,
    )

    image_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_image = prompt_content[image_index - 1]
    assert isinstance(content_before_image, str)
    assert content_before_image.endswith("gallery: list  # attached content: prefix ")
    content_after_image = prompt_content[image_index + 1]
    assert isinstance(content_after_image, str)
    assert content_after_image.startswith(" suffix\n<<<NH:END_LOCALS>>>")
    assert "gallery: list  # attached content: prefix <image> suffix" in prompt_content_to_text(prompt_content)


def test_top_level_text_only_sequence_remains_preview_only() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"lines": ["alpha", "beta"]},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <lines>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent | AudioUrl | ImageUrl | UploadedFile) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert 'lines: list = ["alpha","beta"]' in prompt_text


def test_dotted_multimodal_reference_adds_explicit_native_prompt_line() -> None:
    @dataclass
    class Holder:
        photo: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"))},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("holder.photo: BinaryContent  # attached content: ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder: Holder  # instance" in prompt_text
    assert "holder.photo: BinaryContent  # attached content: <image>" in prompt_text


def test_dotted_multimodal_reference_preserves_base_model_leaf_identity() -> None:
    class Holder(BaseModel):
        photo: object

        def describe(self) -> str:
            return "photo holder"

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"))},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("holder.photo: BinaryContent  # attached content: ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.describe:" in prompt_text
    assert "holder.model_config:" not in prompt_text
    assert "holder.model_dump:" not in prompt_text
    assert "holder.model_fields:" not in prompt_text
    assert "holder.model_validate:" not in prompt_text
    assert "holder.photo: BinaryContent  # attached content: <image>" in prompt_text


def test_dotted_non_multimodal_reference_remains_preview_only() -> None:
    @dataclass
    class Holder:
        count: int

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(count=3)},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.count>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.count: int = 3" in prompt_text


def test_dotted_multimodal_sequence_reference_is_hoisted() -> None:
    @dataclass
    class Holder:
        photos: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photos=[BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")])},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photos>.",
        step_context=step_context,
    )

    image_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_image = prompt_content[image_index - 1]
    assert isinstance(content_before_image, str)
    assert content_before_image.endswith("holder.photos: list  # attached content: ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.photos: list  # attached content: <image>" in prompt_text


def test_dotted_multimodal_sequence_reference_is_hoisted_from_globals() -> None:
    @dataclass
    class Holder:
        photos: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photos=[BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")])},
        python_locals={"message": "hello"},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photos> and <message>.",
        step_context=step_context,
    )

    image_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_image = prompt_content[image_index - 1]
    assert isinstance(content_before_image, str)
    assert content_before_image.endswith("holder.photos: list  # attached content: ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.photos: list  # attached content: <image>" in prompt_text


def test_dotted_mapping_key_multimodal_reference_is_not_hoisted() -> None:
    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "payload": {"photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")},
        },
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <payload.photo>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "payload: dict  # preview: " in prompt_text
    assert "payload.photo: BinaryContent  # attached content: <image>" not in prompt_text
    assert '"kind":"binary"' in prompt_text


def test_top_level_mixed_non_user_content_sequence_remains_preview_only() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "gallery": ["prefix ", {"label": "cat"}, BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")],
        },
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <gallery>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "gallery: list  # preview: [" in prompt_text
    assert '"label":"cat"' in prompt_text
    assert "<bytes; 8 bytes>" in prompt_text


def test_namedtuple_multimodal_reference_remains_record_preview_only() -> None:
    PhotoReport = namedtuple("PhotoReport", ["caption", "photo"])
    report = PhotoReport(
        caption="cat",
        photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
    )

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"report": report},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <report>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "report: PhotoReport  # preview: " in prompt_text
    assert '"caption":"cat"' in prompt_text
    assert '"kind":"binary"' in prompt_text


def test_deep_dotted_multimodal_reference_adds_explicit_native_prompt_line() -> None:
    @dataclass
    class Inner:
        photo: object

    @dataclass
    class Holder:
        inner: Inner

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(inner=Inner(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")))},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.inner.photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("holder.inner.photo: BinaryContent  # attached content: ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.inner:" in prompt_text
    assert "holder.inner.photo: BinaryContent  # attached content: <image>" in prompt_text


def test_depth_one_dotted_multimodal_reference_survives_object_max_fields_truncation() -> None:
    @dataclass
    class Holder:
        alpha: int
        beta: int
        photo: object

    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "holder": Holder(alpha=1, beta=2, photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")),
        },
        python_locals={},
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(object_max_fields=1),
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photo>.",
        step_context=step_context,
        configuration=tight_configuration,
    )

    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.photo: BinaryContent  # attached content: <image>" in prompt_text
    assert sum(1 for content in prompt_content if isinstance(content, BinaryContent)) == 1


def test_explicit_dotted_multimodal_reference_can_be_truncated_by_section_limits() -> None:
    @dataclass
    class Holder:
        photo: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "alpha": "hello",
            "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")),
        },
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_items=1,
            object_max_fields=0,
        ),
    )

    prompt_text = build_user_prompt_text(
        processed_natural_program="Inspect <holder.photo> and <alpha>.",
        step_context=step_context,
        configuration=tight_configuration,
    )

    assert "alpha: str = " in prompt_text
    assert "holder.photo: BinaryContent  # attached content: <image>" not in prompt_text
    assert "<snipped>" in locals_section(prompt_text)


def test_token_truncation_log_marks_dropped_multimodal(caplog) -> None:  # type: ignore[no-untyped-def]
    @dataclass
    class Holder:
        photo: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "alpha": "hello",
            "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")),
        },
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=20,
            locals_max_items=80,
            object_max_fields=0,
        ),
    )

    with caplog.at_level("INFO", logger="nighthawk"):
        prompt_text = build_user_prompt_text(
            processed_natural_program="Inspect <holder.photo> and <alpha>.",
            step_context=step_context,
            configuration=tight_configuration,
        )

    assert "alpha: str = " in prompt_text
    assert "holder.photo: BinaryContent  # attached content: <image>" not in prompt_text
    assert "<snipped>" in locals_section(prompt_text)

    truncation_log_record_list = [record for record in caplog.records if "prompt_context_truncated" in record.getMessage()]
    assert len(truncation_log_record_list) == 1
    message = truncation_log_record_list[0].getMessage()
    assert "'nighthawk.prompt_context.dropped_multimodal': True" in message


def test_multimodal_binding_charges_internal_token_budget() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
            "text_value": "hello",
        },
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(locals_max_tokens=10),
    )

    prompt_text = build_user_prompt_text(
        processed_natural_program="Inspect <photo> and <text_value>.",
        step_context=step_context,
        configuration=tight_configuration,
    )

    assert "<snipped>" in locals_section(prompt_text)


def test_coalesce_prompt_content_merges_text_content() -> None:
    from nighthawk.runtime._user_content import coalesce_user_content

    result = coalesce_user_content(["hello ", TextContent(content="world")])
    assert result == ("hello world",)


def test_count_prompt_line_tokens_handles_text_content() -> None:
    import tiktoken

    from nighthawk.runtime.prompt import _count_prompt_line_tokens

    encoding = tiktoken.get_encoding("o200k_base")
    tokens = _count_prompt_line_tokens(
        prompt_line=(TextContent(content="hello world"),),
        token_encoding=encoding,
    )
    assert tokens < 10
