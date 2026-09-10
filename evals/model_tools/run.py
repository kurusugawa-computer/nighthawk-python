"""Run bounded, randomized Pydantic AI experiments and retain synthetic traces."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import math
import random
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, StructuredDict
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from nighthawk.runtime.step_contract import build_step_json_schema, build_step_system_prompt_suffix_fragment

from .cases import CASES, Case, check_outcome, make_workspace
from .prototype import ApprovalRejectedError, build_tools, system_prompt, user_prompt


def trace_snapshot(value: Any) -> Any:
    """Serialize unexpected runtime values without invoking arbitrary repr."""

    def convert(item: Any, ancestors: frozenset[int]) -> Any:
        if type(item) in (str, int, float, bool, type(None)):
            return item
        if id(item) in ancestors:
            return {"runtime_type": type(item).__name__, "circular": True}
        ancestors = ancestors | {id(item)}
        if type(item) is dict:
            return {str(key): convert(child, ancestors) for key, child in item.items()}
        if type(item) in (list, tuple):
            return [convert(child, ancestors) for child in item]
        return {"runtime_type": type(item).__name__}

    return convert(value, frozenset())


def close_unawaited_values(value: Any, seen: set[int] | None = None) -> None:
    """Dispose only synthetic coroutine values left behind by invalid model operations."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if inspect.iscoroutine(value):
        value.close()
    elif isinstance(value, dict):
        for item in value.values():
            close_unawaited_values(item, seen)
    elif isinstance(value, (list, tuple)):
        for item in value:
            close_unawaited_values(item, seen)


async def run_trial(
    case: Case,
    model: str,
    surface: Any,
    guidance: Any,
    phrasing: int,
    repetition: int,
    timeout: float,
    model_settings: OpenAIChatModelSettings | None = None,
    total_token_limit: int = 45000,
) -> dict[str, Any]:
    workspace = make_workspace(case, surface)
    allowed = ("pass", "return", "raise", "break", "continue") if case.outcome == "continue" else ("pass", "return", "raise")
    schema = build_step_json_schema(allowed_kinds=allowed, raise_error_type_binding_names=())
    instructions = system_prompt(workspace) + build_step_system_prompt_suffix_fragment(allowed_kinds=allowed, raise_error_type_binding_names=())
    tools = build_tools(workspace, guidance)
    agent = Agent(
        model,
        tools=tools,
        output_type=StructuredDict(schema),
        instructions=instructions,
        model_settings=model_settings or {"max_tokens": 2500, "timeout": 30},
        retries=1,
    )
    prompt = user_prompt(workspace, case.instructions[phrasing])
    usage = RunUsage()
    outcome = "error"
    returned_value = None
    outputs: dict[str, Any] = {}
    completion: Any = None
    exception_type = None
    response_metadata = []
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            result = await agent.run(
                prompt, usage=usage, usage_limits=UsageLimits(request_limit=45, tool_calls_limit=90, total_tokens_limit=total_token_limit)
            )
            response_metadata = [
                {"model_name": message.model_name, "finish_reason": message.finish_reason}
                for message in result.all_messages()
                if isinstance(message, ModelResponse)
            ]
            completion = result.output
            outcome = completion["result"]["kind"]
            if outcome == "return":
                returned_value = await workspace.evaluate(completion["result"]["return_expression"])
            if outcome != "raise":
                outputs = workspace.output_values()
    except ApprovalRejectedError:
        outcome = "rejected"
    except Exception as exception:
        # Connection exceptions can contain request headers in third-party
        # libraries. Persist the type, never their uncontrolled repr or body.
        exception_type = type(exception).__name__
    elapsed = time.perf_counter() - started
    failures = check_outcome(case, workspace, outcome, returned_value, outputs)
    if exception_type:
        failures.append(exception_type)
    error_count = sum(operation.get("status") == "error" for operation in workspace.operations)
    expected_error_count = 1 if case.name in ("unbound", "validation", "partial_effect") else 0
    record = trace_snapshot(
        {
            "case": case.name,
            "model": model,
            "surface": surface,
            "guidance": guidance,
            "phrasing": phrasing,
            "repetition": repetition,
            "passed": not failures,
            "state_passed": not [failure for failure in failures if not failure.startswith("Expected outcome ")],
            "failures": failures,
            "outcome": outcome,
            "completion": completion,
            "returned_value": returned_value,
            "outputs": outputs,
            "operations": workspace.operations,
            "effects": workspace.effects,
            "function_name_to_count": workspace.function_name_to_count,
            "tool_calls": len(workspace.operations),
            "tool_errors": error_count,
            "unexpected_tool_errors": max(0, error_count - expected_error_count),
            "duplicate_domain_calls": sum(
                max(0, count - (10 if name == "block_two" else 1)) for name, count in workspace.function_name_to_count.items()
            ),
            "seconds": round(elapsed, 3),
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "usage_details": usage.details,
            "response_metadata": response_metadata,
            "prompt": prompt,
            "instructions": instructions,
            "tools": [{"name": tool.name, "description": tool.description, "schema": tool.function_schema.json_schema} for tool in tools],
            "exception_type": exception_type,
        }
    )
    close_unawaited_values([workspace.step_name_to_value, workspace.effects, returned_value])
    return record


def wilson_interval(successes: int, count: int) -> tuple[float, float]:
    if not count:
        return 0.0, 1.0
    score = successes / count
    squared = 1.96**2
    denominator = 1 + squared / count
    center = (score + squared / (2 * count)) / denominator
    radius = 1.96 * math.sqrt(score * (1 - score) / count + squared / (4 * count**2)) / denominator
    return center - radius, center + radius


def write_summary(path: Path, records: list[dict[str, Any]], configuration: dict[str, Any]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["model"], record["surface"], record["guidance"]].append(record)
    lines = [
        "# Model-facing tool experiment",
        "",
        f"Completed trials: {len(records)}",
        "",
        "Prototype experiment on synthetic tasks; not a production-runtime benchmark.",
        "Wilson intervals below are descriptive trial-level intervals; repeated task formulations are correlated.",
        "",
        "Runtime operation counts exclude the structured completion tool and schema-rejected calls that never entered the workspace. Error counts include intentional error scenarios.",
        "",
        "| Model | Surface | Guidance | Pass | State/effects | 95% interval | Runtime operations/trial | Observed errors | Duplicate domain calls | Input tokens | Output tokens | Seconds/trial |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (model, surface, guidance), rows in sorted(groups.items()):
        successes = sum(row["passed"] for row in rows)
        lower, upper = wilson_interval(successes, len(rows))
        lines.append(
            f"| {model} | {surface} | {guidance} | {successes}/{len(rows)} | {sum(row['state_passed'] for row in rows)}/{len(rows)} | {lower:.1%}-{upper:.1%} | "
            f"{sum(row['tool_calls'] for row in rows) / len(rows):.2f} | {sum(row['tool_errors'] for row in rows)} | "
            f"{sum(row['duplicate_domain_calls'] for row in rows)} | {sum(row['input_tokens'] for row in rows)} | "
            f"{sum(row['output_tokens'] for row in rows)} | {sum(row['seconds'] for row in rows) / len(rows):.2f} |"
        )
    lines.extend(["", "## Failures", ""])
    for row in records:
        if not row["passed"]:
            lines.append(
                f"- {row['model']} / {row['surface']} / {row['guidance']} / {row['case']} / phrasing {row['phrasing']} / repetition {row['repetition']}: "
                + "; ".join(row["failures"])
            )
    if all(row["passed"] for row in records):
        lines.append("None.")
    lines.extend(["", "## Configuration", "", "```json", json.dumps(configuration, indent=2), "```", ""])
    path.write_text("\n".join(lines))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=["openai:gpt-4.1-mini", "openai:gpt-4.1"])
    parser.add_argument("--surfaces", nargs="+", choices=["original", "refined", "merged"], default=["original", "refined", "merged"])
    parser.add_argument("--guidance", nargs="+", choices=["short", "examples"], default=["examples"])
    parser.add_argument("--cases", nargs="+", choices=[case.name for case in CASES])
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--phrasings", type=int, choices=[1, 2], default=2)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--model-timeout", type=float, default=30)
    parser.add_argument("--max-output-tokens", type=int, default=2500)
    parser.add_argument("--total-token-limit", type=int, default=45000)
    parser.add_argument("--reasoning-effort", choices=["none", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if (
        min(
            arguments.concurrency,
            arguments.repetitions,
            arguments.timeout,
            arguments.model_timeout,
            arguments.max_output_tokens,
            arguments.total_token_limit,
        )
        <= 0
    ):
        parser.error("concurrency, repetitions, timeouts, and token limits must be positive")
    model_settings: OpenAIChatModelSettings = {"max_tokens": arguments.max_output_tokens, "timeout": arguments.model_timeout}
    if arguments.reasoning_effort is not None:
        model_settings["openai_reasoning_effort"] = arguments.reasoning_effort
    arguments.output.mkdir(parents=True, exist_ok=False)
    jobs = [
        (case, model, surface, guidance, phrasing, repetition)
        for case in CASES
        if not arguments.cases or case.name in arguments.cases
        for model in arguments.models
        for surface in arguments.surfaces
        for guidance in arguments.guidance
        for phrasing in range(arguments.phrasings)
        for repetition in range(arguments.repetitions)
    ]
    random.Random(arguments.seed).shuffle(jobs)
    configuration = {
        **vars(arguments),
        "output": str(arguments.output),
        "started": datetime.now(UTC).isoformat(),
        "trials": len(jobs),
        "pydantic_ai_version": importlib.metadata.version("pydantic-ai-slim"),
        "model_settings": model_settings,
        "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in Path(__file__).parent.glob("*.py")},
    }
    (arguments.output / "configuration.json").write_text(json.dumps(configuration, indent=2))
    source_directory = arguments.output / "source"
    source_directory.mkdir()
    for path in Path(__file__).parent.glob("*.py"):
        (source_directory / path.name).write_bytes(path.read_bytes())
    semaphore = asyncio.Semaphore(arguments.concurrency)
    records: list[dict[str, Any]] = []

    async def execute(job: tuple[Any, ...]) -> None:
        async with semaphore:
            record = await run_trial(*job, timeout=arguments.timeout, model_settings=model_settings, total_token_limit=arguments.total_token_limit)
            records.append(record)
            with (arguments.output / "trials.jsonl").open("a") as stream:
                stream.write(json.dumps(record, ensure_ascii=True) + "\n")
            write_summary(arguments.output / "summary.md", records, configuration)
            print(
                json.dumps(
                    {
                        "completed": len(records),
                        "total": len(jobs),
                        "case": record["case"],
                        "surface": record["surface"],
                        "model": record["model"],
                        "passed": record["passed"],
                        "exception": record["exception_type"],
                    }
                ),
                flush=True,
            )

    await asyncio.gather(*(execute(job) for job in jobs))


if __name__ == "__main__":
    asyncio.run(main())
