# Executors

> This page assumes you have completed [Quickstart](quickstart.md) and [Natural blocks](natural-blocks.md).

A **step executor** is the strategy object that executes Natural blocks. Nighthawk supports three approaches: Pydantic AI provider-backed executors, coding agent backends, and custom backends. Choose per block, not per project.

## Capability matrix

| Capability | Pydantic AI provider | Coding agent backend | Custom backend | Details |
|---|---|---|---|---|
| Natural block execution | Yes | Yes | Yes | |
| Skill execution | No | Yes | Depends on implementation | [Skills](coding-agent-backends.md#skills) |
| MCP tool exposure | No | Yes | Depends on implementation | [Shared capabilities](coding-agent-backends.md#shared-capabilities) |
| Project-scoped files (CLAUDE.md, AGENTS.md) | No | Yes | Depends on implementation | [Shared capabilities](coding-agent-backends.md#shared-capabilities) |
| Model settings | Pydantic AI standard | Backend-specific | User-defined | [Backend settings](coding-agent-backends.md) |
| Relative cost | Low | High | Varies | |
| Relative latency | Low | High | Varies | |

## Decision tree

| Use case | Preferred executor | Why |
|---|---|---|
| Bounded judgment, extraction, labeling, summarization, structured output | Pydantic AI provider-backed executor | Lower cost, lower latency, tighter surface area |
| Repository inspection, multi-file reasoning, command use, adaptive long-horizon work | Coding agent backend | The block becomes an autonomous agent execution with tools and its own reasoning loop |
| Full control over step execution | Custom backend | Implement the `SyncStepExecutor` or `AsyncStepExecutor` protocol directly |

**Recommended default:** start with a Pydantic AI provider-backed executor for most blocks. Escalate only the blocks that truly need autonomous agent behavior to a coding agent backend. Do not default an entire workflow to coding agent backends just because one block is deep.

See [Philosophy](philosophy.md) for the full design rationale, including the design landscape and tool exposure tradeoffs.

## StepExecutorConfiguration basics

```py
import nighthawk as nh

configuration = nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna")
step_executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
```

The `model` field accepts a `provider:model` identifier or a borrowed Pydantic AI `Model` instance whose client lifetime remains with the host.
The default model is `openai-responses:gpt-5.6-luna`.

An active `nh.run(step_executor)` is required when a Natural block executes or a function's declared model selection resolves:

```py
with nh.run(step_executor):
    result = classify(text)
```

A [function-call handler](runtime-configuration.md#whole-function-admission) may establish that run after accepting the invocation and before calling its continuation.
Definition lookup, refusal before continuation entry, and functions with neither an executed block nor a model declaration need no run.

## Mixing executors

Different blocks can use different executors within a single run. See [Runtime configuration](runtime-configuration.md#mixing-executors) for scoped overrides and examples.

## Custom backends

For most cases, wrap a Pydantic AI `Agent` using `AgentStepExecutor`:

```py
from pydantic_ai import Agent

agent = Agent(model="openai-responses:gpt-5.6-luna", ...)
executor = nh.AgentStepExecutor.from_agent(agent=agent)
```

For full control, implement `AsyncStepExecutor` directly. See [Specification Section 14.3](specification.md#143-custom-backends) for the protocol shape and [API Reference](api.md#base) for the full protocol definition.

## Preserving host controls during model selection

The optional `nh.ConfigurableStepExecutor` protocol adds `resolve_model_selection(selection: nh.ModelSelection) -> nh.StepExecutor`.
Core calls it on the active executor and validates the result; it never searches wrapper attributes for another resolver.
The resolver may reject a model name or map it to a borrowed model, then return another wrapper sharing its host policy and observation objects.
`selection.apply(current_configuration)` performs inert model-only inheritance or full replacement.
The executor is responsible for provider construction only after resolution is permitted.
A direct `AgentStepExecutor` supports this protocol; subclasses must implement it deliberately so their execution behavior cannot silently disappear into a reconstructed base instance.
External agents retain their model ownership restrictions.

The mandatory synchronous and asynchronous step protocols are unchanged.
Executors return block candidates; completion of an executor does not prove generated assignments, notifications, or the containing Python function succeeded.
Use `FunctionCallHandler` for the whole invocation and `StepLifecycle` for validated block facts.
The standalone `examples/embedded_execution.py` contains a preserving host wrapper and a recording request capability using public APIs only.

Provider-backed requests are observable at the Pydantic AI capability layer.
SDK retries or separate HTTP attempts inside one visible request may be hidden.
Codex and Claude Code backends can perform internal model requests that are not exposed as individual Core request observations.
Strict per-transport-attempt accounting therefore requires a backend-specific facility with stronger evidence; Core does not manufacture those records.

## Next steps

- **[Pydantic AI providers](pydantic-ai-providers.md)** -- Installation, model identifiers, credentials, and model settings for each Pydantic AI provider.
- **[Coding agent backends](coding-agent-backends.md)** -- Backend-specific settings, skills, MCP tool exposure, and working directory configuration.
- **[For coding agents](for-coding-agents.md)** -- Condensed, decision-oriented operational guide for coding agents (LLMs) building Python projects with Nighthawk.

Continue to **[Runtime configuration](runtime-configuration.md)** for scope composition, model selection, function admission, and request accounting.
