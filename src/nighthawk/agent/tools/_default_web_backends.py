"""Reference HTTP backends for ``research_web`` capabilities.

Lives in a separate module from :mod:`_default_backends` so the
file-patch reference does not pull an HTTP client transitively. The
``httpx`` dependency is opt-in: install ``nighthawk-python[http]`` to
use the reference :class:`WebFetchClient` returned by
:func:`build_default_web_fetch_client`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .research_web import WebFetchResult

__all__ = ["build_default_web_fetch_client"]


def _import_httpx() -> object:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "build_default_web_fetch_client requires the optional 'http' extra. "
            "Install nighthawk-python[http] to enable the reference WebFetchClient.",
        ) from exc
    return httpx


@dataclass(frozen=True, slots=True)
class _DefaultWebFetchClient:
    """Reference :class:`WebFetchClient` backed by ``httpx.Client``."""

    timeout_seconds: float = 30.0
    user_agent: str = "nighthawk-agent reference web fetch"
    follow_redirects: bool = True
    retain_full_text: bool = False
    maximum_response_character_count: int = 1_000_000

    def fetch(self, url: str, *, focus: str, maximum_character_count: int) -> WebFetchResult:
        if maximum_character_count <= 0:
            raise ValueError("maximum_character_count must be positive")
        del focus  # caller-visible hint only; not forwarded to the remote endpoint
        httpx_module = _import_httpx()
        with httpx_module.Client(  # type: ignore[attr-defined]
            timeout=self.timeout_seconds,
            follow_redirects=self.follow_redirects,
            headers={"User-Agent": self.user_agent},
        ) as client:
            if self.retain_full_text:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    chunk_list: list[str] = []
                    character_count = 0
                    complete = True
                    for chunk in response.iter_text(chunk_size=min(8192, self.maximum_response_character_count + 1)):
                        remaining = self.maximum_response_character_count - character_count
                        chunk_list.append(chunk[:remaining])
                        character_count += min(len(chunk), remaining)
                        if len(chunk) > remaining:
                            complete = False
                            break
                    text = "".join(chunk_list)
                return WebFetchResult(
                    url=url,
                    text=text[:maximum_character_count],
                    full_text=text if complete else None,
                    truncated=not complete or len(text) > maximum_character_count,
                )
            response = client.get(url)
            response.raise_for_status()
            text = response.text
        return WebFetchResult(url=url, text=text[:maximum_character_count], truncated=len(text) > maximum_character_count)


def build_default_web_fetch_client(
    *,
    timeout_seconds: float = 30.0,
    user_agent: str = "nighthawk-agent reference web fetch",
    follow_redirects: bool = True,
    retain_full_text: bool = False,
    maximum_response_character_count: int = 1_000_000,
) -> _DefaultWebFetchClient:
    """Return a reference :class:`WebFetchClient` backed by ``httpx``.

    Requires the optional ``http`` extra. Previews obey the request's character
    bound. Opt-in full retention reads incrementally under a caller-fixed cap;
    a response exceeding that cap has no full value and a truncated preview.
    The cap concerns decoded characters, not network bytes or total memory.
    Callers needing retries, custom authentication, or HTML conversion can
    implement :class:`WebFetchClient` directly.
    """

    if maximum_response_character_count <= 0:
        raise ValueError("maximum_response_character_count must be positive")
    return _DefaultWebFetchClient(
        timeout_seconds=timeout_seconds,
        user_agent=user_agent,
        follow_redirects=follow_redirects,
        retain_full_text=retain_full_text,
        maximum_response_character_count=maximum_response_character_count,
    )
