from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from .context import ExecutionContext
from .environment import NaturalExecutionEnvironment


class NaturalEffect(BaseModel, extra="forbid"):
    type: Literal["continue", "break", "return"]
    value_json: str | None = None


class NaturalError(BaseModel, extra="forbid"):
    message: str
    type: str | None = None


class NaturalFinal(BaseModel, extra="forbid"):
    effect: NaturalEffect | None = None
    error: NaturalError | None = None


type NaturalAgent = Agent[ExecutionContext, NaturalFinal]


def make_agent(environment: NaturalExecutionEnvironment) -> NaturalAgent:
    agent: NaturalAgent = Agent(
        model=environment.natural_execution_configuration.model,
        output_type=NaturalFinal,
        deps_type=ExecutionContext,
        system_prompt=(environment.natural_execution_configuration.prompts.natural_execution_system_prompt_template),
    )
    return agent
