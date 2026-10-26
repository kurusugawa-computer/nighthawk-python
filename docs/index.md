# Nighthawk

<div align="center">
<img src="assets/nighthawk_logo-128x128.png" alt="logo" width="128px">
</div>

Nighthawk is a Python library that makes natural-language judgment composable with ordinary Python functions.

Use Natural blocks for interpretation, classification, and other judgments; combine actions, constraints, and verification through Python functions and existing libraries.
Python owns the surrounding control flow, state, and recovery.
The same typed boundaries support provider-backed LLM calls and coding-agent executions.

```py
import nighthawk as nh


def python_average(numbers: list[float]) -> float:
    return sum(numbers) / len(numbers)


@nh.function
def calculate_average(numbers: list[object]) -> float:
    result: float = 0.0
    """execute
    Map each element of <numbers> to the number it represents,
    then compute <:result> by calling <python_average> with the mapped list.
    """
    return result


with nh.run(execution_model="openai-responses:gpt-6-luna"):
    print(calculate_average([1, "2", "three", "cuatro"]))  # 2.5
```

Natural interprets the input, a Python helper calculates the average, and Pydantic validates the output binding.
Validation constrains the result's type, not the accuracy of the interpretation.

## When to use Nighthawk

Nighthawk fits Python programs whose functions need model judgment inside ordinary control flow, where shared bindings, validated writeback, and execution boundaries reduce the work of building, revising, and reusing those functions.
It composes with other approaches: an orchestration runtime can call Nighthawk functions as ordinary Python, a block can delegate to a coding agent that follows its native instruction files and skills, and Pydantic and Pydantic AI supply value contracts and model interaction.

Prefer another approach when:

- Explicit rules determine the result: write Python.
- Python with a model library meets the same needs with less total burden.
- You need the library itself to provide framework-managed routing, durable conversation state, or language-neutral orchestration: choose a runtime that supplies them and use Nighthawk functions within it where useful.
- You need the library itself to isolate untrusted programs: choose a host or backend that supplies that boundary before using Nighthawk within it.

[Philosophy](philosophy.md) explains Nighthawk's purpose, design rationale, and principles for evaluating tradeoffs and proposed changes.

## Getting started

- **[Quickstart](quickstart.md)** -- Setup and first example. Start here.
- **[Natural blocks](natural-blocks.md)** -- Learn what Natural blocks are: bindings, functions, writing guidelines, and binding function design.
- **[Typed decisions](decisions.md)** -- Answer typed questions using `decide` and inspect per-step evidence.
- **[Executors](executors.md)** -- Choose an execution backend: Pydantic AI provider, coding agent, or custom.

## Patterns and verification

- **[Patterns](patterns.md)** -- Apply Natural blocks in real workflows: outcomes, deny frontmatter, async, cross-block composition, resilience.
- **[Verification](verification.md)** -- Mock tests, integration tests, prompt inspection, and OpenTelemetry tracing.

## Configuration

- **[Pydantic AI providers](pydantic-ai-providers.md)** -- Provider-specific installation, model identifiers, credentials, and model settings.
- **[Coding agent backends](coding-agent-backends.md)** -- Claude Code and Codex backend configuration, skills, and MCP tool exposure.
- **[Runtime configuration](runtime-configuration.md)** -- Configure scopes, select models, and embed Nighthawk in a host application.

## Reference

- **[Specification](specification.md)** -- Runtime contracts and boundaries.
- **[API Reference](api.md)** -- Auto-generated API documentation from source docstrings.
- **[For coding agents](for-coding-agents.md)** -- Condensed, decision-oriented development reference for coding agents (LLMs) building Python projects with Nighthawk.

## Host integration

- **[Admission and completion](host-integration.md)** -- Function admission, model policy, request accounting, and interruption cleanup.
- **[Operation feedback](operation-feedback.md)** -- Recoverable refusals, step abortion, and feedback delivered to the model.
- **[Embedded execution](embedded-execution.md)** -- Execute trusted programs with explicit namespaces and host-owned writeback.

## Background and project

- **[Philosophy](philosophy.md)** -- Purpose, design rationale, and principles for evaluating tradeoffs and proposed changes.
- **[Design drivers](design-drivers.md)** -- Representative application needs, workspace information ownership, and development tooling as an application.
- **[Roadmap](roadmap.md)** -- Future directions and open questions.

## Origins

Nighthawk is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).
