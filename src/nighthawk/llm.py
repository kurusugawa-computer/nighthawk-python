from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from .core import Configuration
from .tools import ToolContext


class NaturalEffect(BaseModel, extra="forbid"):
    type: Literal["continue", "break", "return"]
    value_json: str | None = None


class NaturalError(BaseModel, extra="forbid"):
    message: str
    type: str | None = None


class NaturalFinal(BaseModel, extra="forbid"):
    effect: NaturalEffect | None = None
    error: NaturalError | None = None


type NaturalAgent = Agent[ToolContext, NaturalFinal]


def make_agent(configuration: Configuration) -> NaturalAgent:
    agent: NaturalAgent = Agent(
        model=configuration.model,
        output_type=NaturalFinal,
        deps_type=ToolContext,
    )
    return agent
