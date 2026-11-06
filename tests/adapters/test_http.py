from __future__ import annotations

import builtins
from collections.abc import Callable, Iterator

import httpx
import pytest

from nighthawk.adapters.http import HttpxWebFetchClient
from nighthawk.parts.web import WebFetchResult, WebSearchHit, build_research_web


def _use_transport(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **options: client(transport=transport, **options))


def test_fetch_forwards_url_and_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="hello world")

    _use_transport(monkeypatch, respond)
    client = HttpxWebFetchClient(timeout_seconds=7, user_agent="test-agent")
    result = client.fetch("https://example.com/page", focus="local hint", maximum_character_count=100)
    assert result == WebFetchResult(url="https://example.com/page", text="hello world", truncated=False)
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://example.com/page"
    assert request.headers["User-Agent"] == "test-agent"
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 7)
    assert request.content == b""


@pytest.mark.parametrize("retain_full_text", [False, True])
@pytest.mark.parametrize(
    "payload,capacity,preview_limit",
    [("Unicode: \N{SNOWMAN}", 10, 1), ("abcd", 3, 10), ("abcd", 4, 4), ("abcd", 10, 4), ("", 3, 1), ("a", 3, 1)],
)
def test_fetch_bounds_preview_and_retains_only_complete_text(
    monkeypatch: pytest.MonkeyPatch, payload: str, capacity: int, preview_limit: int, retain_full_text: bool
) -> None:
    _use_transport(monkeypatch, lambda request: httpx.Response(200, text=payload))
    client = HttpxWebFetchClient(retain_full_text=retain_full_text, maximum_response_character_count=capacity)
    result = client.fetch("https://example.com", focus="", maximum_character_count=preview_limit)
    assert result.text == payload[: min(capacity, preview_limit)]
    assert result.full_text == (payload if retain_full_text and len(payload) <= capacity else None)
    assert result.truncated is (len(payload) > min(capacity, preview_limit))


class _IncrementalStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.consumed = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(1000):
            self.consumed += 1
            yield b"x"

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("retain_full_text,expected_consumption", [(False, 5), (True, 11)])
def test_fetch_stops_reading_and_closes_response_at_capacity(
    monkeypatch: pytest.MonkeyPatch, retain_full_text: bool, expected_consumption: int
) -> None:
    stream = _IncrementalStream()
    _use_transport(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    client = HttpxWebFetchClient(retain_full_text=retain_full_text, maximum_response_character_count=10)
    result = client.fetch("https://example.com", focus="", maximum_character_count=4)
    assert result.text == "xxxx" and result.full_text is None and result.truncated
    assert stream.consumed == expected_consumption
    assert stream.closed


def test_fetch_closes_http_error_without_reading_body(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _IncrementalStream()
    _use_transport(monkeypatch, lambda request: httpx.Response(404, stream=stream))
    with pytest.raises(httpx.HTTPStatusError):
        HttpxWebFetchClient().fetch("https://example.com", focus="", maximum_character_count=4)
    assert stream.consumed == 0
    assert stream.closed


def test_fetch_raises_when_httpx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def blocking_import(name: str, *arguments, **keywords):
        if name == "httpx":
            raise ImportError("simulated absence")
        return original_import(name, *arguments, **keywords)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    with pytest.raises(RuntimeError, match=r"nighthawk-python\[http\]"):
        HttpxWebFetchClient().fetch("https://example.com", focus="", maximum_character_count=10)


@pytest.mark.parametrize("capacity", [0, -1])
def test_response_capacity_must_be_positive(capacity: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        HttpxWebFetchClient(maximum_response_character_count=capacity)


@pytest.mark.parametrize("preview_limit", [0, -1])
def test_preview_limit_must_be_positive(preview_limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        HttpxWebFetchClient().fetch("https://example.com", focus="", maximum_character_count=preview_limit)


class _UnusedSearchClient:
    def search(self, query: str, *, allowed_domains, blocked_domains, maximum_result_count: int) -> list[WebSearchHit]:
        raise AssertionError("fetch must not search")


@pytest.mark.parametrize("destination", ["https://other.example/", "http://allowed.example/", "https://blocked.allowed.example/"])
def test_fetch_rejects_redirect_before_contacting_disallowed_destination(monkeypatch: pytest.MonkeyPatch, destination: str) -> None:
    contacted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contacted.append(str(request.url))
        if len(contacted) == 1:
            return httpx.Response(302, headers={"Location": destination})
        return httpx.Response(200, text="outside policy")

    _use_transport(monkeypatch, respond)
    grant = build_research_web(
        search_client=_UnusedSearchClient(),
        fetch_client=HttpxWebFetchClient(),
        allowed_domains=frozenset({"allowed.example"}),
        blocked_domains=frozenset({"blocked.allowed.example"}),
    )
    with pytest.raises(ValueError, match="policy"):
        grant.capabilities["web_fetch"].function("https://allowed.example/", focus="")
    assert contacted == ["https://allowed.example/"]


def test_fetch_follows_relative_redirect_without_consuming_redirect_body(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect_body = _IncrementalStream()
    contacted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contacted.append(str(request.url))
        if len(contacted) == 1:
            return httpx.Response(302, headers={"Location": "/final"}, stream=redirect_body)
        return httpx.Response(200, text="final text")

    _use_transport(monkeypatch, respond)
    result = HttpxWebFetchClient().fetch("https://example.com/start", focus="", maximum_character_count=4)
    assert contacted == ["https://example.com/start", "https://example.com/final"]
    assert result == WebFetchResult(url="https://example.com/final", text="fina", truncated=True)
    assert redirect_body.consumed == 0 and redirect_body.closed


def test_fetch_checks_normalized_initial_url_and_each_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[str] = []
    contacted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contacted.append(str(request.url))
        assert checked == contacted
        if len(contacted) == 1:
            return httpx.Response(302, headers={"Location": "https://sub.example.com/final"})
        return httpx.Response(200, text="complete")

    _use_transport(monkeypatch, respond)
    result = HttpxWebFetchClient().fetch("https://EXAMPLE.COM:443/start", focus="", maximum_character_count=20, check_url=checked.append)
    assert checked == ["https://example.com/start", "https://sub.example.com/final"]
    assert result.url == checked[-1]


def test_fetch_refused_initial_url_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str) -> None:
        raise ValueError("refused")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise AssertionError("refused requests must not be sent")

    _use_transport(monkeypatch, unreachable)
    with pytest.raises(ValueError, match="refused"):
        HttpxWebFetchClient().fetch("https://example.com", focus="", maximum_character_count=4, check_url=refuse)


@pytest.mark.parametrize("follow_redirects", [False, True])
def test_fetch_redirect_limits_close_every_response(monkeypatch: pytest.MonkeyPatch, follow_redirects: bool) -> None:
    body_list: list[_IncrementalStream] = []
    with httpx.Client() as client:
        maximum_redirect_count = client.max_redirects

    def respond(request: httpx.Request) -> httpx.Response:
        body = _IncrementalStream()
        body_list.append(body)
        return httpx.Response(302, headers={"Location": "/again"}, stream=body)

    _use_transport(monkeypatch, respond)
    with pytest.raises(httpx.TooManyRedirects if follow_redirects else httpx.HTTPStatusError):
        HttpxWebFetchClient(follow_redirects=follow_redirects).fetch("https://example.com", focus="", maximum_character_count=4)
    assert len(body_list) == (maximum_redirect_count + 1 if follow_redirects else 1)
    assert all(body.closed and body.consumed == 0 for body in body_list)


@pytest.mark.parametrize(
    "url,allowed_domains,blocked_domains",
    [
        ("https://blocked.example./", frozenset(), frozenset({"blocked.example"})),
        ("https://xn--fa-hia.example/", frozenset(), frozenset({"fa\N{LATIN SMALL LETTER SHARP S}.example"})),
        ("https://fa\N{LATIN SMALL LETTER SHARP S}.example/", frozenset({"fass.example"}), frozenset()),
    ],
)
def test_fetch_domain_policy_uses_transport_host_identity(
    monkeypatch: pytest.MonkeyPatch, url: str, allowed_domains: frozenset[str], blocked_domains: frozenset[str]
) -> None:
    contacted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contacted.append(str(request.url))
        return httpx.Response(200, text="outside policy")

    _use_transport(monkeypatch, respond)
    grant = build_research_web(
        search_client=_UnusedSearchClient(),
        fetch_client=HttpxWebFetchClient(),
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )
    with pytest.raises(ValueError, match="policy"):
        grant.capabilities["web_fetch"].function(url, focus="")
    assert not contacted
