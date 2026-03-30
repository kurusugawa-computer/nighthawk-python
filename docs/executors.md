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

configuration = nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-nano")
step_executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
```

The `model` field accepts a `provider:model` format identifier. The default model is `openai-responses:gpt-5.4-nano`.

All Natural functions must be called inside a `with nh.run(step_executor):` context:

```py
with nh.run(step_executor):
    result = classify(text)
```

## Mixing executors

Different blocks can use different executors within a single run. See [Runtime configuration](runtime-configuration.md#mixing-executors) for scoped overrides and examples.

## Custom backends

For most cases, wrap a Pydantic AI `Agent` using `AgentStepExecutor`:

```py
from pydantic_ai import Agent

agent = Agent(model="openai-responses:gpt-5.4-nano", ...)
executor = nh.AgentStepExecutor.from_agent(agent=agent)
```

For full control, implement `AsyncStepExecutor` directly. See [Specification Section 14.3](specification.md#143-custom-backends) for the protocol shape and [API Reference](api.md#base) for the full protocol definition.

## Next steps

- **[Pydantic AI providers](pydantic-ai-providers.md)** -- Installation, model identifiers, credentials, and model settings for each Pydantic AI provider.
- **[Coding agent backends](coding-agent-backends.md)** -- Backend-specific settings, skills, MCP tool exposure, and working directory configuration.
- **[For coding agents](for-coding-agents.md)** -- Condensed, decision-oriented operational guide for coding agents (LLMs) building Python projects with Nighthawk.

After choosing and configuring your executor, continue to **[Runtime configuration](runtime-configuration.md)** for scoping, patching, context limits, and execution identity.
