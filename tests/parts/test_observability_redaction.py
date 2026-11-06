from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nighthawk.harness.errors import CapabilityRejectedError
from nighthawk.harness.grants import Grant
from nighthawk.parts.commands import CommandOutput, CommandPolicy, CommandRequest, command_capability
from nighthawk.parts.web import WebFetchResult, WebRequest, WebRequestPolicy, WebResponse, WebSearchHit, build_research_web, web_request_capability
from nighthawk.parts.workspace import FileSlice, build_inspect_workspace


def _all_span_attribute_strings(exported: list[ReadableSpan]) -> list[str]:
    rendered: list[str] = []
    for span in exported:
        rendered.extend(str(value) for value in (span.attributes or {}).values())
        for event in span.events:
            rendered.extend(str(value) for value in (event.attributes or {}).values())
    return rendered


class _CommandExecutorRecording:
    def execute(self, request: CommandRequest) -> CommandOutput:
        return CommandOutput(exit_code=0)


def test_run_command_does_not_leak_argv(in_memory_span_exporter: InMemorySpanExporter, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    grant = Grant(
        (
            command_capability(
                policy=CommandPolicy(allow_any_command_prefix=True, command_working_directory_allow_list=(workspace,)),
                command_executor=_CommandExecutorRecording(),
            ),
        )
    )
    grant.capabilities["run_command"].function(CommandRequest(command=("echo", "SECRET_VALUE"), working_directory=workspace))
    for value in _all_span_attribute_strings(list(in_memory_span_exporter.get_finished_spans())):
        assert "SECRET_VALUE" not in value


def test_read_file_uses_workspace_relative_paths(in_memory_span_exporter: InMemorySpanExporter, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "doc.txt"
    target.write_text("hello world")
    read_file = cast(Callable[..., FileSlice], build_inspect_workspace(workspace_root=workspace).capabilities["read_file"].function)
    read_file(target)
    rendered = _all_span_attribute_strings(list(in_memory_span_exporter.get_finished_spans()))
    for value in rendered:
        assert str(target.resolve()) not in value
        assert "hello world" not in value
    assert any("doc.txt" in value for value in rendered)


class _CapturingFetchClient:
    def fetch(self, url: str, *, focus: str, maximum_character_count: int, check_url: Callable[[str], None]) -> WebFetchResult:
        check_url(url)
        return WebFetchResult(url=url, text="body content")


class _NoopSearchClient:
    def search(self, query: str, *, allowed_domains: object, blocked_domains: object, maximum_result_count: int) -> list[WebSearchHit]:
        return []


def test_web_fetch_records_only_host(in_memory_span_exporter: InMemorySpanExporter) -> None:
    grant = build_research_web(search_client=_NoopSearchClient(), fetch_client=_CapturingFetchClient())
    web_fetch = cast(Callable[..., WebFetchResult], grant.capabilities["web_fetch"].function)
    web_fetch("https://www.example.com/path/with/query?secret=42", focus="query the docs")
    rendered = _all_span_attribute_strings(list(in_memory_span_exporter.get_finished_spans()))
    for value in rendered:
        assert "secret=42" not in value
        assert "/path/with/query" not in value
    assert any("www.example.com" in value for value in rendered)


class _UnreachableWebRequestClient:
    def send(self, request: WebRequest, *, check_request: Callable[[WebRequest], None]) -> WebResponse:
        raise AssertionError("policy must reject before delegating")


def test_web_request_rejection_does_not_leak_url_or_body(in_memory_span_exporter: InMemorySpanExporter) -> None:
    grant = Grant(
        (
            web_request_capability(
                policy=WebRequestPolicy(allowed_domains=frozenset({"approved.example.com"})), web_request_client=_UnreachableWebRequestClient()
            ),
        )
    )
    with pytest.raises(CapabilityRejectedError) as captured:
        grant.capabilities["web_request"].function(
            WebRequest(url="https://secret-host.example.com/endpoint?token=SECRET_TOKEN", method="POST", body="confidential-payload")
        )
    assert captured.value.reason_code == "domain_not_allowed"
    for value in _all_span_attribute_strings(list(in_memory_span_exporter.get_finished_spans())):
        assert "SECRET_TOKEN" not in value
        assert "confidential-payload" not in value
        assert "secret-host.example.com" not in value
        assert "/endpoint" not in value
