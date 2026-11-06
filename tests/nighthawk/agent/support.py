"""Helpers shared by the agent test modules."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

import httpx2
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict
from pydantic_ai.models.openai_decisions import OpenAIDecisionsModel
from pydantic_ai.providers.openai import OpenAIProvider

from nighthawk.agent import Grant, LedgerEntry, PerimeterPolicy, RunContract, RunHandle, ScaffoldComposition
from nighthawk.agent.runtime import Runner
from nighthawk.runtime.step_executor import StepExecutor


class EchoInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = "input"


class EchoResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = "ok"


def run_body(
    body: Callable[[EchoInput], EchoResult],
    *,
    runner: Runner,
    step_executor: StepExecutor | None = None,
    run_entry_grant: Grant | None = None,
    composition_perimeter_policy: PerimeterPolicy | None = None,
    function_definitions: Iterable[Callable[..., object]] = (),
    handle: RunHandle | None = None,
    run_input: EchoInput | None = None,
) -> EchoResult:
    composition = ScaffoldComposition(
        run_contract=RunContract(input_type=EchoInput, result_type=EchoResult),
        body=body,
        run_entry_grant=run_entry_grant if run_entry_grant is not None else Grant.EMPTY,
        composition_perimeter_policy=composition_perimeter_policy if composition_perimeter_policy is not None else PerimeterPolicy.permitting_all(),
        function_definition_tuple=tuple(function_definitions),
    )
    configured_runner = runner if step_executor is None else replace(runner, step_executor=step_executor)
    return configured_runner.run(composition, run_input if run_input is not None else EchoInput(), handle=handle)


def event_kinds(entries: Iterable[LedgerEntry]) -> list[str]:
    return [entry.event.kind for entry in entries]


class DecisionsTransport(httpx2.AsyncBaseTransport):
    """OpenAI Decisions endpoint double answering every predicate with one probability."""

    def __init__(self, probability: float = 0.8) -> None:
        self.probability = probability
        self.request_body_list: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.request_body_list.append(body)
        return httpx2.Response(
            200,
            json={
                "answers": [{"name": question["name"], "type": "predicate", "probability": self.probability} for question in body["questions"]],
                "model": body["model"],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 0,
                    "total_tokens": 12,
                    "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )


def decision_model(transport: DecisionsTransport) -> OpenAIDecisionsModel:
    """Return a decision model whose requests reach only ``transport``."""

    client = AsyncOpenAI(api_key="synthetic-test-key", base_url="https://mock.invalid/v1", http_client=httpx2.AsyncClient(transport=transport))
    return OpenAIDecisionsModel("gpt-6-luna", provider=OpenAIProvider(openai_client=client))
