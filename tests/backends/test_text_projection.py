from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    CachePoint,
    DocumentUrl,
    ImageUrl,
    ToolReturnPart,
    UploadedFile,
    VideoUrl,
)

from nighthawk.backends.text_projection import project_request_prompt_part_list_to_text


def test_project_request_prompt_part_list_to_text_ignores_cache_point() -> None:
    projected_request = project_request_prompt_part_list_to_text([("before", CachePoint(), "after")])

    assert projected_request.prompt_text == "beforeafter"


def test_project_request_prompt_part_list_to_text_projects_tool_return_files_to_local_paths() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="test_image_return",
        content=["caption", BinaryContent(data=b"png", media_type="image/png", identifier="img123")],
    )

    projected_request = project_request_prompt_part_list_to_text([("hello",), tool_return_part])

    try:
        assert "hello" in projected_request.prompt_text
        assert "Tool return from test_image_return:" in projected_request.prompt_text
        assert "caption" in projected_request.prompt_text
        assert "img123" in projected_request.prompt_text
        assert "Local file path:" in projected_request.prompt_text
        assert projected_request.temporary_directory is not None
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_projects_user_prompt_images_to_local_paths() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), " suffix")]
    )

    try:
        assert "prefix \n<image>\nLocal file path:" in projected_request.prompt_text
        assert "Media type: image/png\n suffix" in projected_request.prompt_text
        assert projected_request.temporary_directory is not None
    finally:
        projected_request.cleanup()


@pytest.mark.parametrize(
    ("media_type", "expected_extension"),
    [
        ("image/png", ".png"),
        ("audio/mpeg", ".mp3"),
        ("application/pdf", ".pdf"),
    ],
)
def test_project_request_prompt_part_list_to_text_uses_deterministic_extensions(media_type: str, expected_extension: str) -> None:
    projected_request = project_request_prompt_part_list_to_text([(BinaryContent(data=b"payload", media_type=media_type, identifier="item"),)])
    try:
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        assert local_file_path_line.endswith(expected_extension)
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_uses_provided_staging_root_directory(tmp_path: Path) -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), " suffix")],
        staging_root_directory=tmp_path,
    )

    try:
        assert projected_request.temporary_directory is not None
        temporary_directory_path = Path(projected_request.temporary_directory.name).resolve()
        assert temporary_directory_path.parent == tmp_path.resolve()
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: ")).resolve()
        assert local_file_path.parent == temporary_directory_path
        assert local_file_path.exists()
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_cleanup_removes_staging_directory(tmp_path: Path) -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), " suffix")],
        staging_root_directory=tmp_path,
    )

    assert projected_request.temporary_directory is not None
    temporary_directory_path = Path(projected_request.temporary_directory.name)
    assert temporary_directory_path.exists()

    projected_request.cleanup()

    assert not temporary_directory_path.exists()


def test_project_request_prompt_part_list_to_text_cleans_up_staging_directory_on_projection_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write_bytes = Path.write_bytes
    staged_path_list: list[Path] = []

    def fail_on_second_binary(path: Path, data: bytes) -> int:
        if path.name == "second.png":
            raise OSError("disk full")
        staged_path_list.append(path)
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_on_second_binary)

    with pytest.raises(OSError, match="disk full"):
        project_request_prompt_part_list_to_text(
            [
                (
                    BinaryContent(data=b"first", media_type="image/png", identifier="first"),
                    BinaryContent(data=b"second", media_type="image/png", identifier="second"),
                )
            ],
            staging_root_directory=tmp_path,
        )

    assert staged_path_list
    temporary_directory_path = staged_path_list[0].parent
    assert not temporary_directory_path.exists()


def test_project_request_prompt_part_list_to_text_separates_adjacent_multimodal_items() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [
            (
                BinaryContent(data=b"first", media_type="image/png", identifier="img123"),
                BinaryContent(data=b"second", media_type="image/png", identifier="img456"),
            )
        ]
    )

    try:
        assert "Media type: image/png\n\n<image>\nLocal file path:" in projected_request.prompt_text
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_preserves_tool_return_item_order() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="mixed_tool_return",
        content=["before", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), "after"],
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert "Tool return from mixed_tool_return:\nbefore\n<image>\nLocal file path: " in projected_request.prompt_text
        assert "Media type: image/png" in projected_request.prompt_text
        assert "Media type: image/png\nafter" in projected_request.prompt_text
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_preserves_non_multimodal_tool_result_structure() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="structured_tool_return",
        content=["before", {"x": 1}, "after"],
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert projected_request.prompt_text == 'Tool return from structured_tool_return:\n["before",{"x":1},"after"]'
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_replaces_uploaded_file_with_fallback_text() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="uploaded_file_tool_return",
        content=[
            "before text",
            UploadedFile(file_id="file-123", provider_name="openai", media_type="image/png", identifier="upload1"),
            "after text",
        ],
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert (
            "Tool return from uploaded_file_tool_return:\nbefore text\n<image>\nUploadedFile: provider=openai, file_id=file-123 (not resolvable by this backend)"
            in projected_request.prompt_text
        )
        assert "Media type: image/png\nafter text" in projected_request.prompt_text
    finally:
        projected_request.cleanup()


@pytest.mark.parametrize(
    ("payload", "expected_prompt_text"),
    [
        (None, "Tool return from empty_tool:"),
        ("", "Tool return from empty_tool:"),
        ([], "Tool return from empty_tool:"),
        (0, "Tool return from empty_tool:\n0"),
        (False, "Tool return from empty_tool:\nfalse"),
    ],
)
def test_project_request_prompt_part_list_to_text_handles_empty_success_consistently(
    payload: object,
    expected_prompt_text: str,
) -> None:
    tool_return_part = ToolReturnPart(
        tool_name="empty_tool",
        content=payload,
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert projected_request.prompt_text == expected_prompt_text
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_sanitizes_binary_content_file_name() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="../escape"), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        temporary_directory_path = Path(projected_request.temporary_directory.name).resolve()
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: ")).resolve()
        assert local_file_path.parent == temporary_directory_path
        assert local_file_path.name == "escape.png"
        assert local_file_path.exists()
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_strips_control_characters_from_file_stem() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="hello\x00world\x1f"), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: "))
        assert "helloworld" in local_file_path.stem
        assert "\x00" not in local_file_path.name
        assert "\x1f" not in local_file_path.name
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_truncates_long_file_stem() -> None:
    long_identifier = "a" * 300
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier=long_identifier), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: "))
        assert len(local_file_path.stem) <= 200
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_falls_back_for_all_control_character_identifier() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="\x00\x01"), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: "))
        assert local_file_path.stem == "file"
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_avoids_binary_content_file_name_collisions() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [
            (
                BinaryContent(data=b"first", media_type="image/png", identifier="same"),
                BinaryContent(data=b"second", media_type="image/png", identifier="same"),
            )
        ]
    )

    try:
        local_file_path_list = [
            Path(line.removeprefix("Local file path: ")).resolve()
            for line in projected_request.prompt_text.splitlines()
            if line.startswith("Local file path: ")
        ]
        assert len(local_file_path_list) == 2
        assert local_file_path_list[0] != local_file_path_list[1]
        assert local_file_path_list[0].name == "same.png"
        assert local_file_path_list[1].name == "same_2.png"
        assert local_file_path_list[0].read_bytes() == b"first"
        assert local_file_path_list[1].read_bytes() == b"second"
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_projects_image_url_to_text() -> None:
    projected_request = project_request_prompt_part_list_to_text([("prefix ", ImageUrl(url="https://example.com/cat.png"), " suffix")])

    try:
        assert "prefix " in projected_request.prompt_text
        assert "<image>" in projected_request.prompt_text
        assert "Image URL: https://example.com/cat.png" in projected_request.prompt_text
        assert " suffix" in projected_request.prompt_text
        assert projected_request.temporary_directory is None
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_projects_audio_url_to_text() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg"), " suffix")]
    )

    try:
        assert "prefix " in projected_request.prompt_text
        assert "<file>" in projected_request.prompt_text
        assert "Audio URL: https://example.com/sample.mp3" in projected_request.prompt_text
        assert "Media type: audio/mpeg" in projected_request.prompt_text
        assert " suffix" in projected_request.prompt_text
        assert projected_request.temporary_directory is None
    finally:
        projected_request.cleanup()


@pytest.mark.parametrize(
    ("url_factory", "expected_label", "url"),
    [
        (AudioUrl, "Audio", "https://example.com/download?id=1"),
        (DocumentUrl, "Document", "https://example.com/download?id=2"),
        (VideoUrl, "Video", "https://example.com/watch?id=3"),
    ],
)
def test_project_request_prompt_part_list_to_text_tolerates_uninferrable_file_url_media_type(
    url_factory: Any,
    expected_label: str,
    url: str,
) -> None:
    projected_request = project_request_prompt_part_list_to_text([("prefix ", url_factory(url=url), " suffix")])

    try:
        assert "prefix " in projected_request.prompt_text
        assert "<file>" in projected_request.prompt_text
        assert f"{expected_label} URL: {url}" in projected_request.prompt_text
        assert "Media type:" not in projected_request.prompt_text
        assert " suffix" in projected_request.prompt_text
    finally:
        projected_request.cleanup()
