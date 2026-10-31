"""Settings and request helpers shared by the Claude Code CLI and SDK backends."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field
from pydantic_ai.models import ModelRequestParameters

from .base import BackendModelSettings

type PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]
type SettingSource = Literal["user", "project", "local"]


class ClaudeCodeModelSettings(BackendModelSettings):
    """Settings shared between Claude Code CLI and SDK backends.

    Attributes:
        max_turns: Maximum conversation turns.
        max_budget_usd: Maximum dollar amount to spend on API calls.
        permission_mode: Claude Code permission mode.
        setting_sources: Configuration sources to load.
        claude_allowed_tool_names: Additional Claude Code native tool names to allow.
    """

    max_turns: int | None = Field(default=None, gt=0)
    max_budget_usd: float | None = Field(default=None, gt=0)
    permission_mode: PermissionMode | None = None
    setting_sources: list[SettingSource] | None = None
    claude_allowed_tool_names: tuple[str, ...] | None = None


NIGHTHAWK_MCP_TOOL_PREFIX = "mcp__nighthawk__"

TOOL_ACCESS_SYSTEM_PROMPT_FRAGMENT = (
    "\n\nTool access:\n"
    f"- Nighthawk tools are exposed via MCP; tool names are prefixed with: {NIGHTHAWK_MCP_TOOL_PREFIX}\n"
    f"- Example: to call nh_evaluate(...), use: {NIGHTHAWK_MCP_TOOL_PREFIX}nh_evaluate"
)


def output_json_schema(model_request_parameters: ModelRequestParameters) -> dict[str, Any] | None:
    output_object = model_request_parameters.output_object
    if output_object is None:
        return None
    schema = dict(output_object.json_schema)
    if output_object.name:
        schema["title"] = output_object.name
    if output_object.description:
        schema["description"] = output_object.description
    return schema
