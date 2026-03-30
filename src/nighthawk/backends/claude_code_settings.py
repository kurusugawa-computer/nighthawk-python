"""Shared model settings and type aliases for Claude Code backends (CLI and SDK)."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from .base import BackendModelSettings

type PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]
type SettingSource = Literal["user", "project", "local"]


class ClaudeCodeModelSettings(BackendModelSettings):
    """Settings shared between Claude Code CLI and SDK backends.

    Attributes:
        max_turns: Maximum conversation turns.
        permission_mode: Claude Code permission mode.
        setting_sources: Configuration sources to load.
    """

    max_turns: int | None = None
    permission_mode: PermissionMode | None = None
    setting_sources: list[SettingSource] | None = None

    @field_validator("max_turns")
    @classmethod
    def _validate_max_turns(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("max_turns must be greater than 0")
        return value
