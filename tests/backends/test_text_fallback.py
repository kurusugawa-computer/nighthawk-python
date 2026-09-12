from __future__ import annotations

import pytest
from pydantic_ai.messages import AudioUrl, DocumentUrl, FileUrl, ImageUrl, VideoUrl

from nighthawk.backends._text_fallback import format_file_url_as_text_lines


class GenericFileUrl(FileUrl):
    def _infer_media_type(self) -> str:
        return "application/octet-stream"

    @property
    def format(self) -> str:
        return "bin"


@pytest.mark.parametrize(
    ("content", "expected_lines"),
    [
        (
            ImageUrl(url="https://example.com/cat.png"),
            ["Image URL: https://example.com/cat.png"],
        ),
        (
            AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg"),
            ["Audio URL: https://example.com/sample.mp3", "Media type: audio/mpeg"],
        ),
        (
            DocumentUrl(url="https://example.com/report.pdf"),
            ["Document URL: https://example.com/report.pdf", "Media type: application/pdf"],
        ),
        (
            VideoUrl(url="https://example.com/movie.mp4"),
            ["Video URL: https://example.com/movie.mp4", "Media type: video/mp4"],
        ),
        (
            GenericFileUrl(url="https://example.com/archive.bin", media_type="application/octet-stream"),
            ["File URL: https://example.com/archive.bin", "Media type: application/octet-stream"],
        ),
        (
            VideoUrl(url="https://example.com/watch?id=3"),
            ["Video URL: https://example.com/watch?id=3"],
        ),
    ],
)
def test_format_file_url_as_text_lines(content: FileUrl, expected_lines: list[str]) -> None:
    assert format_file_url_as_text_lines(content=content) == expected_lines
