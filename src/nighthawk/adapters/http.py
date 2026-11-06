"""Optional HTTPX-backed fetch implementation; install nighthawk-python[http]."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from nighthawk.parts.web import WebFetchResult

__all__ = ["HttpxWebFetchClient"]


@dataclass(frozen=True, slots=True)
class HttpxWebFetchClient:
    """Stream bounded text previews through HTTPX, optionally retaining a complete response.

    ``maximum_response_character_count`` caps captured decoded text in both modes.
    Without retention, reading stops after the preview and a bounded chunk of lookahead.
    With retention, ``full_text`` is present only if the response fits the capture cap.
    Redirect bodies are closed without consumption; redirects use HTTPX's redirect count limit.
    A supplied ``check_url`` runs before every request on its normalized URL and may raise to refuse communication.
    """

    timeout_seconds: float = 30.0
    user_agent: str = "nighthawk-python reference web fetch"
    follow_redirects: bool = True
    retain_full_text: bool = False
    maximum_response_character_count: int = 1_000_000

    def __post_init__(self) -> None:
        if self.maximum_response_character_count <= 0:
            raise ValueError("maximum_response_character_count must be positive")

    def fetch(self, url: str, *, focus: str, maximum_character_count: int, check_url: Callable[[str], None] | None = None) -> WebFetchResult:
        try:
            import httpx
        except ImportError as error:
            raise RuntimeError("HttpxWebFetchClient requires nighthawk-python[http].") from error
        if maximum_character_count <= 0:
            raise ValueError("maximum_character_count must be positive")
        capture_limit = self.maximum_response_character_count
        if not self.retain_full_text:
            capture_limit = min(capture_limit, maximum_character_count)
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False, headers={"User-Agent": self.user_agent}) as client:
            request = client.build_request("GET", url)
            redirect_count = 0
            while True:
                if check_url is not None:
                    check_url(str(request.url))
                with closing(client.send(request, stream=True)) as response:
                    if self.follow_redirects and response.next_request is not None:
                        if redirect_count >= client.max_redirects:
                            raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.", request=request)
                        redirect_count += 1
                        request = response.next_request
                        continue
                    response.raise_for_status()
                    chunk_list: list[str] = []
                    character_count = 0
                    complete = True
                    for chunk in response.iter_text(chunk_size=min(8192, capture_limit + 1)):
                        remaining = capture_limit - character_count
                        chunk_list.append(chunk[:remaining])
                        character_count += min(len(chunk), remaining)
                        if len(chunk) > remaining:
                            complete = False
                            break
                    text = "".join(chunk_list)
                    return WebFetchResult(
                        url=str(response.url),
                        text=text[:maximum_character_count],
                        full_text=text if self.retain_full_text and complete else None,
                        truncated=not complete or len(text) > maximum_character_count,
                    )
