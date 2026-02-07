from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets.function import FunctionToolset

import nighthawk as nh
from nighthawk.backends.codex import CodexModel, _parse_codex_jsonl_lines
from nighthawk.execution.context import ExecutionContext
from nighthawk.tools.registry import get_visible_tools


def test_parse_codex_jsonl_lines_extracts_agent_message_and_thread_id_and_usage() -> None:
    jsonl_lines = [
        json.dumps({"type": "thread.started", "thread_id": "t_123"}),
        json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "hello"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 5}}),
    ]

    outcome = _parse_codex_jsonl_lines(jsonl_lines)

    assert outcome["output_text"] == "hello"
    assert outcome["thread_id"] == "t_123"

    usage = outcome["usage"]
    assert usage.input_tokens == 10
    assert usage.cache_read_tokens == 2
    assert usage.output_tokens == 5


def test_parse_codex_jsonl_lines_fails_closed_without_agent_message() -> None:
    jsonl_lines = [
        json.dumps({"type": "thread.started", "thread_id": "t_123"}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 5}}),
    ]

    with pytest.raises(UnexpectedModelBehavior):
        _parse_codex_jsonl_lines(jsonl_lines)


def test_parse_codex_jsonl_lines_fails_closed_on_invalid_json_line() -> None:
    jsonl_lines = [
        "{not json}",
    ]

    with pytest.raises(UnexpectedModelBehavior):
        _parse_codex_jsonl_lines(jsonl_lines)


def test_parse_codex_jsonl_lines_fails_closed_on_turn_failed() -> None:
    jsonl_lines = [
        json.dumps({"type": "turn.failed", "error": {"message": "nope"}}),
    ]

    with pytest.raises(UnexpectedModelBehavior, match="nope"):
        _parse_codex_jsonl_lines(jsonl_lines)


def test_parse_codex_jsonl_lines_fails_closed_on_stream_error_event() -> None:
    jsonl_lines = [
        json.dumps({"type": "error", "message": "bad"}),
    ]

    with pytest.raises(UnexpectedModelBehavior, match="bad"):
        _parse_codex_jsonl_lines(jsonl_lines)


def _write_executable_codex_cli_stub(*, directory: Path) -> Path:
    """Write a test-only Codex CLI stub executable.

    NOTE: This is a contract test stub, not a real codex-cli integration.

    The stub emulates the minimal behavior needed by CodexModel:
    - Reads stdin (prompt text)
    - Extracts MCP server URL and enabled tool allowlist from --config arguments
    - Connects to the MCP server via Streamable HTTP
    - Calls nh_eval("1 + 1")
    - Emits JSONL events to stdout (thread.started, item.completed, turn.completed)
    """

    stub_path = directory / "codex-cli-stub"

    stub_code = (
        "#!"
        + sys.executable
        + "\n"
        + """from __future__ import annotations

import json
import sys

import anyio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _parse_config_arguments(argv: list[str]) -> dict[str, str]:
    configuration: dict[str, str] = {}

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--config":
            key_value = argv[i + 1]
            key, value = key_value.split("=", 1)
            configuration[key] = value
            i += 2
            continue
        i += 1

    return configuration


def _decode_toml_literal(value_text: str) -> object:
    # The backend writes TOML literals using JSON serialization.
    return json.loads(value_text)


async def _run() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] != "exec":
        raise RuntimeError(f"Unexpected argv: {argv!r}")

    configuration = _parse_config_arguments(argv)

    mcp_server_url_text = configuration.get("mcp_servers.nighthawk.url")
    if mcp_server_url_text is None:
        raise RuntimeError("Missing config mcp_servers.nighthawk.url")

    mcp_server_url = _decode_toml_literal(mcp_server_url_text)
    if not isinstance(mcp_server_url, str):
        raise RuntimeError("mcp_servers.nighthawk.url must be a string")

    enabled_tools: list[str] | None = None
    enabled_tools_text = configuration.get("mcp_servers.nighthawk.enabled_tools")
    if enabled_tools_text is not None:
        enabled_tools_object = _decode_toml_literal(enabled_tools_text)
        if not isinstance(enabled_tools_object, list) or not all(isinstance(x, str) for x in enabled_tools_object):
            raise RuntimeError("mcp_servers.nighthawk.enabled_tools must be a list[str]")
        enabled_tools = enabled_tools_object

    prompt_text = sys.stdin.read()

    async with streamable_http_client(mcp_server_url) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]

            if enabled_tools is not None and set(tool_names) != set(enabled_tools):
                raise RuntimeError(f"Tool list mismatch. server={tool_names!r} enabled={enabled_tools!r}")

            result = await session.call_tool("nh_eval", {"expression": "1 + 1"})
            text_chunks: list[str] = []
            for content in result.content:
                text = getattr(content, "text", None)
                if isinstance(text, str):
                    text_chunks.append(text)
            nh_eval_text = "".join(text_chunks)

    agent_message_text = json.dumps(
        {
            "prompt_received": bool(prompt_text.strip()),
            "tool_names": tool_names,
            "nh_eval_text": nh_eval_text,
        },
        sort_keys=True,
    )

    events = [
        {"type": "thread.started", "thread_id": "t_stub"},
        {
            "type": "item.completed",
            "item": {"id": "i1", "type": "agent_message", "text": agent_message_text},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},
    ]

    for event in events:
        print(json.dumps(event), flush=True)


def main() -> None:
    anyio.run(_run)


if __name__ == "__main__":
    main()
"""
    )

    stub_path.write_text(stub_code, encoding="utf-8")
    stub_path.chmod(0o755)
    return stub_path


def test_codex_model_contract_calls_tool_via_mcp(tmp_path: Path) -> None:
    codex_executable = _write_executable_codex_cli_stub(directory=tmp_path)

    execution_configuration = nh.ExecutionConfiguration(model="codex:default")

    class StubExecutor:
        def run_natural_block(self, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs
            raise AssertionError("ExecutionExecutor should not be used by this test")

    environment = nh.ExecutionEnvironment(
        execution_configuration=execution_configuration,
        execution_executor=StubExecutor(),
        workspace_root=tmp_path,
    )

    with nh.environment(environment):
        execution_context = ExecutionContext(
            execution_id="test_codex_cli_model_contract_calls_tool_via_mcp",
            execution_configuration=execution_configuration,
            execution_globals={"__builtins__": __builtins__},
            execution_locals={},
            binding_commit_targets=set(),
        )

        from typing import cast

        model_settings = cast(
            ModelSettings,
            {
                "codex_executable": str(codex_executable),
                "allowed_tool_names": ("nh_eval",),
            },
        )

        tools = get_visible_tools()
        toolset = FunctionToolset(tools)

        agent = Agent(
            model=CodexModel(),
            deps_type=ExecutionContext,
            output_type=str,
        )

        result = agent.run_sync(
            "Compute 1 + 1 via nh_eval.",
            deps=execution_context,
            toolsets=[toolset],
            model_settings=model_settings,
        )

    payload = json.loads(result.output)
    assert payload["prompt_received"] is True
    assert payload["tool_names"] == ["nh_eval"]

    tool_result = json.loads(payload["nh_eval_text"])
    assert tool_result["status"] == "success"
    assert tool_result["error"] is None
    assert tool_result["value"] == 2
