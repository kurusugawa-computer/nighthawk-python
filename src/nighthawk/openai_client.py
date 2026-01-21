from __future__ import annotations

import os

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from .configuration import Configuration


class NaturalFinal(BaseModel):
    effect: dict | None = None
    error: dict | None = None


def make_agent(configuration: Configuration) -> Agent[None, NaturalFinal]:
    model_name = os.getenv("NIGHTHAWK_MODEL", configuration.model)
    model = OpenAIChatModel(model_name)
    return Agent(model=model, output_type=NaturalFinal)
