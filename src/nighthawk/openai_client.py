from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from .configuration import Configuration
from .tools import ToolContext, assign_tool, dir_tool, eval_tool, help_tool


class NaturalEffect(BaseModel, extra="forbid"):
    """Final response 'effect' object."""

    type: Literal["continue", "break", "return"]
    value_json: str | None = None


class NaturalError(BaseModel, extra="forbid"):
    """Final response 'error' object."""

    message: str
    type: str | None = None


class NaturalFinal(BaseModel, extra="forbid"):
    effect: NaturalEffect | None = None
    error: NaturalError | None = None


def make_agent(configuration: Configuration) -> Agent[ToolContext, NaturalFinal]:
    agent: Agent[ToolContext, NaturalFinal] = Agent(
        model=configuration.model,
        output_type=NaturalFinal,
        deps_type=ToolContext,
    )

    @agent.tool(name="dir")
    def tool_dir(ctx: RunContext[ToolContext], expr: str) -> str:
        return dir_tool(ctx.deps, expr)

    @agent.tool(name="help")
    def tool_help(ctx: RunContext[ToolContext], expr: str) -> str:
        return help_tool(ctx.deps, expr)

    @agent.tool(name="eval")
    def tool_eval(ctx: RunContext[ToolContext], expr: str) -> str:
        return eval_tool(ctx.deps, expr)

    @agent.tool(name="assign")
    def tool_assign(
        ctx: RunContext[ToolContext],
        target: str,
        expression: str,
        type_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return assign_tool(
            ctx.deps,
            target,
            expression,
            type_hints=(type_hints or {}),
        )

    return agent
