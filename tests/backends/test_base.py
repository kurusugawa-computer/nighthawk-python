from __future__ import annotations

import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    CachePoint,
    ImageUrl,
    ModelRequest,
    UploadedFile,
    UserPromptPart,
)

from nighthawk.backends.base import _collect_request_prompt_part_list

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_collect_request_prompt_part_list_accepts_multimodal_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert len(request_prompt_part_list) == 1
    assert isinstance(request_prompt_part_list[0], tuple)
    assert isinstance(request_prompt_part_list[0][0], BinaryContent)


def test_collect_request_prompt_part_list_accepts_image_url_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([ImageUrl(url="https://example.com/cat.png")])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert len(request_prompt_part_list) == 1
    assert isinstance(request_prompt_part_list[0], tuple)
    assert isinstance(request_prompt_part_list[0][0], ImageUrl)


def test_collect_request_prompt_part_list_accepts_audio_url_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg")])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert len(request_prompt_part_list) == 1
    assert isinstance(request_prompt_part_list[0], tuple)
    assert isinstance(request_prompt_part_list[0][0], AudioUrl)


def test_collect_request_prompt_part_list_accepts_cache_point_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart(["before", CachePoint(), "after"])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert request_prompt_part_list == [("before", CachePoint(), "after")]


def test_collect_request_prompt_part_list_rejects_uploaded_file_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([UploadedFile(file_id="file-123", provider_name="openai", media_type="image/png")])])

    with pytest.raises(UserError, match="UploadedFile user prompt content"):
        _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
