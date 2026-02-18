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
from nighthawk.runtime.step_context import StepContext
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
    if value_text == "true":
        return True
    if value_text == "false":
        return False
    if value_text.startswith('"') and value_text.endswith('"'):
        return json.loads(value_text)
    if value_text.startswith("["):
        return json.loads(value_text)
    if value_text.startswith("{"):
        return json.loads(value_text)
    try:
        return int(value_text)
    except Exception:
        return value_text


async def _run() -> None:
    prompt_text = sys.stdin.read()
    config = _parse_config_arguments(sys.argv)

    mcp_url_text = config.get("mcp_server_url") or config.get("mcp_servers.nighthawk.url") or '""'
    allowed_tools_text = config.get("allowed_tools") or config.get("mcp_servers.nighthawk.enabled_tools") or "[]"

    mcp_url = _decode_toml_literal(mcp_url_text)
    allowed_tools = _decode_toml_literal(allowed_tools_text)

    if not isinstance(mcp_url, str) or mcp_url == "":
        raise RuntimeError("Missing mcp_server_url")

    async with streamable_http_client(mcp_url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool("nh_eval", arguments={"expression": "1 + 1"})
            nh_eval_text = response.content[0].text

    payload = {
        "prompt_received": prompt_text.strip() != "",
        "tool_names": allowed_tools,
        "nh_eval_text": nh_eval_text,
    }

    agent_message_text = json.dumps(payload)

    events = [
        {"type": "thread.started", "thread_id": "t_123"},
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

    run_configuration = nh.RunConfiguration(model="codex:default")

    class StubExecutor:
        def run_step(self, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs
            raise AssertionError("StepExecutor should not be used by this test")

    environment_value = nh.Environment(
        run_configuration=run_configuration,
        step_executor=StubExecutor(),
        workspace_root=tmp_path,
    )

    with nh.run(environment_value):
        step_context = StepContext(
            step_id="test_codex_cli_model_contract_calls_tool_via_mcp",
            run_configuration=run_configuration,
            step_globals={"__builtins__": __builtins__},
            step_locals={},
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
            deps_type=StepContext,
            output_type=str,
        )

        result = agent.run_sync(
            "Compute 1 + 1 via nh_eval.",
            deps=step_context,
            toolsets=[toolset],
            model_settings=model_settings,
        )

    payload = json.loads(result.output)
    assert payload["prompt_received"] is True
    assert payload["tool_names"] == ["nh_eval"]

    tool_result = json.loads(payload["nh_eval_text"])
    assert tool_result["error"] is None
    assert tool_result["value"] == 2
