from __future__ import annotations

import sys
from pathlib import Path


def _write_executable_codex_stub(*, directory: Path) -> Path:
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

    async with streamable_http_client(mcp_url) as (read, write):
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
