from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from .configuration import Configuration


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


def make_agent(configuration: Configuration) -> Agent[None, NaturalFinal]:
    return Agent(model=configuration.model, output_type=NaturalFinal)
