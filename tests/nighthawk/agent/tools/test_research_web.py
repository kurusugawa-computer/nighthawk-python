"""Tests for ``build_research_web`` closures."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

import pytest

from nighthawk.agent import Grant
from nighthawk.agent.tools.research_web import (
    WebFetchResult,
    WebSearchHit,
    build_research_web,
)


class _CapturingWebSearchClient:
    def __init__(self) -> None:
        self.last_query: str | None = None
        self.last_allowed: tuple[str, ...] = ()
        self.last_blocked: tuple[str, ...] = ()
        self.last_maximum: int | None = None

    def search(
        self,
        query: str,
        *,
        allowed_domains: Sequence[str],
        blocked_domains: Sequence[str],
        maximum_result_count: int,
    ) -> Sequence[WebSearchHit]:
        self.last_query = query
        self.last_allowed = tuple(allowed_domains)
        self.last_blocked = tuple(blocked_domains)
        self.last_maximum = maximum_result_count
        return (WebSearchHit(title="example", url=f"https://{allowed_domains[0] if allowed_domains else 'example.com'}/", snippet=""),)


class _CapturingWebFetchClient:
    def __init__(self, body: str = "hi") -> None:
        self.body = body
        self.last_url: str | None = None
        self.last_focus: str | None = None
        self.last_maximum: int | None = None

    def fetch(self, url: str, *, focus: str, maximum_character_count: int) -> WebFetchResult:
        self.last_url = url
        self.last_focus = focus
        self.last_maximum = maximum_character_count
        return WebFetchResult(url=url, text=self.body)


def _bundle_search(bundle: Grant) -> Callable[..., tuple[WebSearchHit, ...]]:
    return cast(Callable[..., tuple[WebSearchHit, ...]], bundle.capabilities["web_search"].function)


def _bundle_fetch(bundle: Grant) -> Callable[..., WebFetchResult]:
    return cast(Callable[..., WebFetchResult], bundle.capabilities["web_fetch"].function)


def test_web_search_query_length_cap() -> None:
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=_CapturingWebFetchClient(),
        maximum_query_character_count=8,
    )
    with pytest.raises(ValueError):
        _bundle_search(bundle)("a" * 9)


def test_web_search_result_count_cap() -> None:
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=_CapturingWebFetchClient(),
        maximum_search_result_count=3,
    )
    with pytest.raises(ValueError):
        _bundle_search(bundle)("hello", maximum_result_count=4)


def test_web_search_natural_narrowing_rejects_widening() -> None:
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=_CapturingWebFetchClient(),
        allowed_domains=frozenset({"example.com"}),
    )
    with pytest.raises(ValueError):
        _bundle_search(bundle)("hello", allowed_domains=("other.com",))


def test_web_search_natural_narrowing_accepts_subset() -> None:
    search_client = _CapturingWebSearchClient()
    bundle = build_research_web(
        search_client=search_client,
        fetch_client=_CapturingWebFetchClient(),
        allowed_domains=frozenset({"example.com", "another.com"}),
    )
    _bundle_search(bundle)("hello", allowed_domains=("example.com",))
    assert search_client.last_allowed == ("example.com",)


def test_web_search_block_list_unioned() -> None:
    search_client = _CapturingWebSearchClient()
    bundle = build_research_web(
        search_client=search_client,
        fetch_client=_CapturingWebFetchClient(),
        blocked_domains=frozenset({"bad.com"}),
    )
    _bundle_search(bundle)("hello", blocked_domains=("worse.com",))
    assert set(search_client.last_blocked) == {"bad.com", "worse.com"}


def test_web_fetch_scheme_check() -> None:
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=_CapturingWebFetchClient(),
    )
    with pytest.raises(ValueError):
        _bundle_fetch(bundle)("http://example.com/", focus="x")


def test_web_fetch_no_hostname_raises_value_error() -> None:
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=_CapturingWebFetchClient(),
    )
    with pytest.raises(ValueError):
        _bundle_fetch(bundle)("https:///path", focus="x")


def test_web_fetch_block_wins() -> None:
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=_CapturingWebFetchClient(),
        blocked_domains=frozenset({"bad.com"}),
    )
    with pytest.raises(ValueError):
        _bundle_fetch(bundle)("https://bad.com/x", focus="x")


def test_web_fetch_subdomain_match() -> None:
    fetch_client = _CapturingWebFetchClient()
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=fetch_client,
        allowed_domains=frozenset({"example.com"}),
    )
    _bundle_fetch(bundle)("https://api.example.com/x", focus="x")
    assert fetch_client.last_url == "https://api.example.com/x"
    with pytest.raises(ValueError):
        _bundle_fetch(bundle)("https://badexample.com/x", focus="x")


def test_web_fetch_idna_match() -> None:
    fetch_client = _CapturingWebFetchClient()
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=fetch_client,
        allowed_domains=frozenset({"bücher.example"}),
    )
    _bundle_fetch(bundle)("https://xn--bcher-kva.example/path", focus="x")
    assert fetch_client.last_url == "https://xn--bcher-kva.example/path"


def test_web_fetch_empty_allow_list_unconstrained() -> None:
    fetch_client = _CapturingWebFetchClient()
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=fetch_client,
    )
    _bundle_fetch(bundle)("https://anything.example/x", focus="x")
    assert fetch_client.last_url == "https://anything.example/x"


def test_web_fetch_truncates_response() -> None:
    fetch_client = _CapturingWebFetchClient(body="x" * 100)
    bundle = build_research_web(
        search_client=_CapturingWebSearchClient(),
        fetch_client=fetch_client,
        maximum_fetch_character_count=200,
    )
    result = _bundle_fetch(bundle)("https://example.com/", focus="x", maximum_character_count=10)
    assert isinstance(result, WebFetchResult)
    assert len(result.text) == 10


@pytest.mark.parametrize("retained,flag", [(None, None), ("complete", False), ("complete", True), ("", False)])
def test_fetch_wrapper_preserves_retention_and_completeness(retained: str | None, flag: bool | None) -> None:
    class RetainingClient:
        def fetch(self, url: str, *, focus: str, maximum_character_count: int) -> WebFetchResult:
            return WebFetchResult(url=url, text=retained if retained is not None else "preview", full_text=retained, truncated=flag)

    bundle = build_research_web(search_client=_CapturingWebSearchClient(), fetch_client=RetainingClient())
    fetch = _bundle_fetch(bundle)
    unchanged = fetch("https://example.com", focus="", maximum_character_count=20)
    assert unchanged.full_text == retained and unchanged.truncated is flag
    shortened = fetch("https://example.com", focus="", maximum_character_count=1)
    assert shortened.full_text == retained
    assert shortened.truncated is (True if len(unchanged.text) > 1 else flag)
