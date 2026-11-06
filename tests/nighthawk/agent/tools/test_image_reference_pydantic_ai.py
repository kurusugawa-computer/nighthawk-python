"""Verify :class:`ImageReference.content` flows correctly through Pydantic AI.

Uses :class:`pydantic_ai.models.test.TestModel` so the integration is
deterministic and does not require a live LLM. The intent is to catch
regressions where ``ImageReference`` would emit a value that is not a
valid Pydantic AI ``UserContent`` element.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from nighthawk.agent.tools import build_inspect_workspace
from nighthawk.agent.tools.inspect_workspace import ImageReference


def _view_image(workspace: Path, image_url_mapper: object = None) -> ImageReference:
    bundle = build_inspect_workspace(
        workspace_root=workspace,
        image_url_mapper=cast(Any, image_url_mapper),
    )
    view_image = cast(Any, bundle.capabilities["view_image"].function)
    return cast(ImageReference, view_image(workspace / "image.png"))


def _make_one_pixel_png() -> bytes:
    # 1x1 transparent PNG produced by hand from the standard layout.
    return bytes.fromhex(
        "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
    )


def test_binary_content_round_trips_through_test_model(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "image.png").write_bytes(_make_one_pixel_png())
    reference = _view_image(workspace)
    assert isinstance(reference.content, BinaryContent)

    agent = Agent(TestModel())
    result = agent.run_sync(["describe this:", reference.content])
    assert result.output

    user_messages = [message for message in result.all_messages() if isinstance(message, ModelRequest)]
    assert user_messages
    parts: list[object] = []
    for message in user_messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                content = part.content
                if isinstance(content, list):
                    parts.extend(content)
                else:
                    parts.append(content)
    binary_parts = [part for part in parts if isinstance(part, BinaryContent)]
    assert binary_parts, "BinaryContent must reach the TestModel via UserPromptPart"
    assert binary_parts[0].media_type == "image/png"


def test_image_url_round_trips_through_test_model(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "image.png").write_bytes(_make_one_pixel_png())

    def url_mapper(file_path: Path) -> ImageUrl:
        return ImageUrl(url=f"https://example.com/{file_path.name}")

    reference = _view_image(workspace, image_url_mapper=url_mapper)
    assert isinstance(reference.content, ImageUrl)

    agent = Agent(TestModel())
    result = agent.run_sync(["describe this:", reference.content])
    assert result.output

    parts: list[object] = []
    for message in result.all_messages():
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content
                    if isinstance(content, list):
                        parts.extend(content)
                    else:
                        parts.append(content)
    image_url_parts = [part for part in parts if isinstance(part, ImageUrl)]
    assert image_url_parts, "ImageUrl must reach the TestModel via UserPromptPart"
    assert image_url_parts[0].url == "https://example.com/image.png"
