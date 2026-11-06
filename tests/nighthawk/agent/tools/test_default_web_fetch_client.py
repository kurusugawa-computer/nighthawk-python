"""Tests for the reference :func:`build_default_web_fetch_client`.

httpx is a transitive dependency of the dev environment, so the live
HTTP path is not exercised here. The factory smoke test confirms the
returned object satisfies :class:`WebFetchClient`. The truncation and
URL forwarding semantics are validated against an injected fake
``httpx`` module to keep the suite hermetic.
"""

from __future__ import annotations

import sys
import types
from typing import Self

import pytest

from nighthawk.agent.tools.research_web import (
    WebFetchClient,
    WebFetchResult,
    build_default_web_fetch_client,
)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeHttpxClient:
    def __init__(self, *, responses: dict[str, str], log: list[dict[str, object]]) -> None:
        self._responses = responses
        self._log = log

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        self._log.append({"url": url})
        return _FakeResponse(self._responses[url])


def _install_fake_httpx(
    monkeypatch: pytest.MonkeyPatch,
    *,
    responses: dict[str, str],
) -> list[dict[str, object]]:
    log: list[dict[str, object]] = []
    fake_module = types.ModuleType("httpx")

    def client_factory(*args: object, **kwargs: object) -> _FakeHttpxClient:
        log.append({"client_kwargs": kwargs})
        return _FakeHttpxClient(responses=responses, log=log)

    fake_module.Client = client_factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", fake_module)
    return log


def test_factory_returns_protocol_satisfying_object() -> None:
    client = build_default_web_fetch_client()
    assert isinstance(client, WebFetchClient)


def test_fetch_returns_full_text_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    log = _install_fake_httpx(monkeypatch, responses={"https://example.com": "hello world"})
    client = build_default_web_fetch_client()
    result = client.fetch("https://example.com", focus="discard", maximum_character_count=100)
    assert result == WebFetchResult(url="https://example.com", text="hello world", truncated=False)
    assert log[1]["url"] == "https://example.com"


def test_fetch_truncates_to_maximum_character_count(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = "A" * 1000
    _install_fake_httpx(monkeypatch, responses={"https://example.com": payload})
    client = build_default_web_fetch_client()
    result = client.fetch("https://example.com", focus="x", maximum_character_count=64)
    assert result.text == "A" * 64
    assert len(result.text) == 64


def test_fetch_raises_when_httpx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    def _blocking_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "httpx":
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)  # type: ignore[misc]

    monkeypatch.setattr("builtins.__import__", _blocking_import)
    monkeypatch.delitem(sys.modules, "httpx", raising=False)
    client = build_default_web_fetch_client()
    with pytest.raises(RuntimeError, match="nighthawk-python\\[http\\]"):
        client.fetch("https://example.com", focus="x", maximum_character_count=10)


@pytest.mark.parametrize(
    "payload,capacity,full_text,truncated",
    [
        ("日本語", 3, "日本語", True),
        ("日本語", 2, None, True),
        ("", 3, "", False),
        ("a", 3, "a", False),
    ],
)
def test_opt_in_incremental_full_retention(
    monkeypatch: pytest.MonkeyPatch, payload: str, capacity: int, full_text: str | None, truncated: bool
) -> None:
    import httpx

    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    monkeypatch.setattr(httpx, "Client", lambda **options: real_client(transport=transport, **options))
    client = build_default_web_fetch_client(retain_full_text=True, maximum_response_character_count=capacity)
    result = client.fetch("https://example.com", focus="", maximum_character_count=1)
    assert result.full_text == full_text
    assert result.truncated is truncated
    assert result.text == payload[:1]


def test_response_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        build_default_web_fetch_client(maximum_response_character_count=0)


def test_retention_stops_reading_and_closes_response_at_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class IncrementalStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.consumed = 0
            self.closed = False

        def __iter__(self):
            for _ in range(1000):
                self.consumed += 1
                yield b"x"

        def close(self) -> None:
            self.closed = True

    stream = IncrementalStream()
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    monkeypatch.setattr(httpx, "Client", lambda **options: real_client(transport=transport, **options))
    client = build_default_web_fetch_client(retain_full_text=True, maximum_response_character_count=10)
    result = client.fetch("https://example.com", focus="", maximum_character_count=4)
    assert result.text == "xxxx" and result.full_text is None and result.truncated
    assert stream.consumed == 11 and stream.closed
