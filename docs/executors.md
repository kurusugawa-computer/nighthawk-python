# Executors

> This page assumes you have completed [Quickstart](quickstart.md) and [Natural blocks](natural-blocks.md).

A **step executor** is the strategy object that executes Natural blocks. Nighthawk supports three approaches: Pydantic AI provider-backed executors, coding agent backends, and custom backends. Choose per block, not per project.

## Capability matrix

| Backend | Typical work | Skills, MCP, project files | Relative cost | Relative latency |
|---|---|---|---|---|
| Pydantic AI provider | Classification, extraction, summarization | No backend-native support | Low | Low |
| Coding agent | Repository investigation, command use, autonomous tasks | [Backend capabilities](coding-agent-backends.md#shared-capabilities) | High | High |
| Custom | Application-defined step execution | Implementation-defined | Varies | Varies |

## Decision tree

Start with a provider-backed executor for bounded judgments.
Choose a coding agent when a block needs autonomous file or command work, or a custom backend when the application must control step execution itself.

See [Philosophy](philosophy.md) for the purpose and composition principles, and [Coding agent backends](coding-agent-backends.md) for backend capabilities and tool exposure.

## StepExecutorConfiguration basics

```py
import nighthawk as nh

configuration = nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna")
step_executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
```

The `model` field accepts a `provider:model` identifier or a borrowed Pydantic AI `Model` instance whose client lifetime remains with the host.
The default model is `openai-responses:gpt-5.6-luna`.

Use [Runtime configuration](runtime-configuration.md) to establish a run, select child models, or mix executors.
Host wrappers can support model selection through the [configuration resolution protocol](specification.md#model-declarations-and-observation).

## Custom backends

For most cases, wrap a Pydantic AI `Agent` using `AgentStepExecutor`:

```py
from pydantic_ai import Agent

import nighthawk as nh

agent = Agent(model="openai-responses:gpt-5.6-luna")
executor = nh.AgentStepExecutor.from_agent(agent=agent)
```

For full control, implement `AsyncStepExecutor` directly. See [Specification Section 14.3](specification.md#143-custom-backends) for semantics and [API reference](api.md#runtime-executor-boundary) for the protocol.

## Next steps

- **[Pydantic AI providers](pydantic-ai-providers.md)** -- Installation, model identifiers, credentials, and model settings for each Pydantic AI provider.
- **[Coding agent backends](coding-agent-backends.md)** -- Backend-specific settings, skills, MCP tool exposure, and working directory configuration.
- **[For coding agents](for-coding-agents.md)** -- Condensed, decision-oriented operational guide for coding agents (LLMs) building Python projects with Nighthawk.

Continue to **[Runtime configuration](runtime-configuration.md)** for scope composition, model selection, function admission, and request accounting.
