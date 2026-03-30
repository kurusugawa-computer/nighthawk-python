from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior

from nighthawk.backends.claude_code_cli import (
    ClaudeCodeCliModelSettings,
    _parse_claude_code_json_output,
)

# --- _parse_claude_code_json_output tests ---


def test_parse_json_output_extracts_result_text() -> None:
    output = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "hello", "usage": {}, "modelUsage": {}})
    outcome = _parse_claude_code_json_output(output)
    assert outcome["output_text"] == "hello"


def test_parse_json_output_extracts_usage() -> None:
    output = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": "ok",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
            },
            "modelUsage": {},
        }
    )
    outcome = _parse_claude_code_json_output(output)
    assert outcome["usage"].input_tokens == 100
    assert outcome["usage"].output_tokens == 50
    assert outcome["usage"].cache_read_tokens == 10
    assert outcome["usage"].cache_write_tokens == 5


def test_parse_json_output_extracts_model_name_from_model_usage() -> None:
    output = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": "ok",
            "usage": {},
            "modelUsage": {"claude-sonnet-4-6": {"inputTokens": 10}},
        }
    )
    outcome = _parse_claude_code_json_output(output)
    assert outcome["model_name"] == "claude-sonnet-4-6"


def test_parse_json_output_returns_none_model_when_model_usage_empty() -> None:
    output = json.dumps({"type": "result", "is_error": False, "result": "ok", "usage": {}, "modelUsage": {}})
    outcome = _parse_claude_code_json_output(output)
    assert outcome["model_name"] is None


def test_parse_json_output_raises_on_missing_result() -> None:
    output = json.dumps({"type": "result", "is_error": False, "usage": {}})
    with pytest.raises(UnexpectedModelBehavior, match="did not produce a result string"):
        _parse_claude_code_json_output(output)


def test_parse_json_output_raises_on_is_error() -> None:
    output = json.dumps({"type": "result", "is_error": True, "result": "something went wrong", "usage": {}})
    with pytest.raises(UnexpectedModelBehavior, match="something went wrong"):
        _parse_claude_code_json_output(output)


def test_parse_json_output_raises_on_invalid_json() -> None:
    with pytest.raises(UnexpectedModelBehavior, match="invalid JSON"):
        _parse_claude_code_json_output("not json at all")


def test_parse_json_output_raises_on_non_object() -> None:
    with pytest.raises(UnexpectedModelBehavior, match="non-object JSON"):
        _parse_claude_code_json_output('"just a string"')


# --- ClaudeCodeCliModelSettings validation tests ---


def test_settings_defaults() -> None:
    settings = ClaudeCodeCliModelSettings()
    assert settings.executable == "claude"
    assert settings.working_directory == ""
    assert settings.max_turns is None
    assert settings.max_budget_usd is None
    assert settings.permission_mode is None
    assert settings.setting_sources is None
    assert settings.allowed_tool_names is None


def test_settings_rejects_empty_executable() -> None:
    with pytest.raises(ValidationError):
        ClaudeCodeCliModelSettings(executable="  ")


def test_settings_rejects_relative_working_directory() -> None:
    with pytest.raises(ValidationError):
        ClaudeCodeCliModelSettings(working_directory="relative/path")


def test_settings_accepts_absolute_working_directory() -> None:
    settings = ClaudeCodeCliModelSettings(working_directory="/abs/path")
    assert settings.working_directory == "/abs/path"


def test_settings_rejects_non_positive_max_turns() -> None:
    with pytest.raises(ValidationError):
        ClaudeCodeCliModelSettings(max_turns=0)
    with pytest.raises(ValidationError):
        ClaudeCodeCliModelSettings(max_turns=-1)


def test_settings_accepts_positive_max_turns() -> None:
    settings = ClaudeCodeCliModelSettings(max_turns=10)
    assert settings.max_turns == 10


def test_settings_rejects_non_positive_max_budget() -> None:
    with pytest.raises(ValidationError):
        ClaudeCodeCliModelSettings(max_budget_usd=0)
    with pytest.raises(ValidationError):
        ClaudeCodeCliModelSettings(max_budget_usd=-1.0)


def test_settings_accepts_positive_max_budget() -> None:
    settings = ClaudeCodeCliModelSettings(max_budget_usd=5.0)
    assert settings.max_budget_usd == 5.0


def test_settings_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ClaudeCodeCliModelSettings(unknown_field="value")  # type: ignore[call-arg]
