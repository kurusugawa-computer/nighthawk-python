from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import BinaryContent
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets.function import FunctionToolset

import nighthawk as nh
from nighthawk.backends.codex import CodexModel, CodexModelSettings, _parse_codex_jsonl_lines
from nighthawk.runtime import StepCandidate, StepRequest
from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.declarations import get_visible_tools
from tests.support.codex import _write_executable_codex_stub

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


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


def test_codex_model_settings_default_sandbox_mode_is_none() -> None:
    settings = CodexModelSettings.from_model_settings(None)
    assert settings.sandbox_mode is None


def test_codex_model_settings_default_model_reasoning_effort_is_none() -> None:
    settings = CodexModelSettings.from_model_settings(None)
    assert settings.model_reasoning_effort is None


def test_codex_model_settings_accepts_none_sandbox_mode() -> None:
    model_settings = cast(ModelSettings, {"sandbox_mode": None})
    settings = CodexModelSettings.from_model_settings(model_settings)
    assert settings.sandbox_mode is None


def test_codex_model_settings_accepts_none_model_reasoning_effort() -> None:
    model_settings = cast(ModelSettings, {"model_reasoning_effort": None})
    settings = CodexModelSettings.from_model_settings(model_settings)
    assert settings.model_reasoning_effort is None


def test_codex_model_settings_accepts_model_reasoning_effort() -> None:
    model_settings = cast(ModelSettings, {"model_reasoning_effort": "high"})
    settings = CodexModelSettings.from_model_settings(model_settings)
    assert settings.model_reasoning_effort == "high"


def test_codex_model_settings_uses_default_when_sandbox_mode_key_is_omitted() -> None:
    model_settings = cast(ModelSettings, {"executable": "codex"})
    settings = CodexModelSettings.from_model_settings(model_settings)
    assert settings.sandbox_mode is None
    assert settings.model_reasoning_effort is None


def test_codex_model_settings_rejects_invalid_sandbox_mode() -> None:
    model_settings = cast(ModelSettings, {"sandbox_mode": "invalid-mode"})
    with pytest.raises(UserError, match="sandbox_mode"):
        CodexModelSettings.from_model_settings(model_settings)


def test_codex_model_settings_rejects_invalid_model_reasoning_effort() -> None:
    model_settings = cast(ModelSettings, {"model_reasoning_effort": "extreme"})
    with pytest.raises(UserError, match="model_reasoning_effort"):
        CodexModelSettings.from_model_settings(model_settings)


def _write_executable_codex_prompt_echo_stub(*, directory: Path) -> Path:
    stub_path = directory / "codex-cli-prompt-echo-stub"

    stub_code = (
        "#!"
        + sys.executable
        + "\n"
        + """from __future__ import annotations

import json
import sys


def main() -> None:
    prompt_text = sys.stdin.read()
    events = [
        {"type": "thread.started", "thread_id": "t_prompt"},
        {
            "type": "item.completed",
            "item": {"id": "i1", "type": "agent_message", "text": json.dumps({"prompt_text": prompt_text})},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},
    ]
    for event in events:
        print(json.dumps(event), flush=True)


if __name__ == "__main__":
    main()
"""
    )

    stub_path.write_text(stub_code, encoding="utf-8")
    stub_path.chmod(0o755)
    return stub_path


def test_codex_model_contract_calls_tool_via_mcp(tmp_path: Path) -> None:
    codex_executable = _write_executable_codex_stub(directory=tmp_path)

    nh.StepExecutorConfiguration(model="codex:default")

    class StubExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            _ = request
            raise AssertionError("StepExecutor should not be used by this test")

    step_executor = StubExecutor()

    with nh.run(step_executor):
        step_context = StepContext(
            execution_reference=nh.ExecutionReference(
                "test-run", "test-scope", "test_codex_model_contract_calls_tool_via_mcp", "test_codex_model_contract_calls_tool_via_mcp"
            ),
            step_globals={"__builtins__": __builtins__},
            step_locals={},
            binding_commit_targets=set(),
            read_binding_names=frozenset(),
            task_name_to_value={},
        )

        from typing import cast

        model_settings = cast(
            ModelSettings,
            {
                "executable": str(codex_executable),
                "allowed_tool_names": ("nh_eval",),
                "working_directory": str(tmp_path.resolve()),
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
    assert tool_result == 2


def test_codex_model_projects_multimodal_prompt_files_into_working_directory(tmp_path: Path) -> None:
    codex_executable = _write_executable_codex_prompt_echo_stub(directory=tmp_path)

    step_context = StepContext(
        execution_reference=nh.ExecutionReference(
            "test-run",
            "test-scope",
            "test_codex_model_projects_multimodal_prompt_files_into_working_directory",
            "test_codex_model_projects_multimodal_prompt_files_into_working_directory",
        ),
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        task_name_to_value={},
    )

    model_settings = cast(
        ModelSettings,
        {
            "executable": str(codex_executable),
            "working_directory": str(tmp_path.resolve()),
        },
    )

    agent = Agent(
        model=CodexModel(),
        deps_type=StepContext,
        output_type=str,
    )

    result = agent.run_sync(
        ("prefix ", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="cat"), " suffix"),
        deps=step_context,
        model_settings=model_settings,
    )

    payload = json.loads(result.output)
    prompt_text = payload["prompt_text"]
    assert "Local file path:" in prompt_text
    local_file_path_line = next(line for line in prompt_text.splitlines() if line.startswith("Local file path: "))
    local_file_path = Path(local_file_path_line.removeprefix("Local file path: ")).resolve()
    assert tmp_path.resolve() in local_file_path.parents


def test_codex_model_projects_multimodal_prompt_files_into_current_working_directory_when_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_executable = _write_executable_codex_prompt_echo_stub(directory=tmp_path)
    current_working_directory = tmp_path / "current-working-directory"
    current_working_directory.mkdir()
    monkeypatch.chdir(current_working_directory)

    step_context = StepContext(
        execution_reference=nh.ExecutionReference(
            "test-run",
            "test-scope",
            "test_codex_model_projects_multimodal_prompt_files_into_current_working_directory_when_unset",
            "test_codex_model_projects_multimodal_prompt_files_into_current_working_directory_when_unset",
        ),
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        task_name_to_value={},
    )

    model_settings = cast(
        ModelSettings,
        {
            "executable": str(codex_executable),
        },
    )

    agent = Agent(
        model=CodexModel(),
        deps_type=StepContext,
        output_type=str,
    )

    result = agent.run_sync(
        ("prefix ", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="cat"), " suffix"),
        deps=step_context,
        model_settings=model_settings,
    )

    payload = json.loads(result.output)
    prompt_text = payload["prompt_text"]
    local_file_path_line = next(line for line in prompt_text.splitlines() if line.startswith("Local file path: "))
    local_file_path = Path(local_file_path_line.removeprefix("Local file path: ")).resolve()
    assert current_working_directory.resolve() in local_file_path.parents
