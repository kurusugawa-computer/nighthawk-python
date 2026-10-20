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

configuration = nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-5.6-luna")
step_executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
```

The `execution_model` and `decision_model` fields accept a `provider:model` identifier or a borrowed Pydantic AI `Model` instance whose client lifetime remains with the host.
Neither model is selected by default. A missing model fails when its block kind is executed. For ordinary use, select models directly with `nh.run(execution_model=..., decision_model=...)`.
`model_settings` defaults to an empty dictionary; `None` and `UNSET` are invalid field values.
Optional executor configuration arguments use omission or `nh.UNSET` to create a fresh default configuration.
An explicit configuration replaces the complete value; raw dictionaries and `None` are rejected before constructing an agent.

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

## Output selection

Executors return `StepCandidate(outcome=..., binding_name_to_value=...)`.
The output mapping selects the complete set of values to validate and consider for writeback.

| Selection | Meaning |
|---|---|
| Omitted or `nh.UNSET` | Select initialized declared outputs from the working environment at finalization. |
| `{}` | Select no writes, even when declared outputs already have values. |
| `{"result": value}` | Select only `result`; do not supplement other initialized outputs. |
| `None` | Invalid. |

The standard agent executor omits the selection, so its tracked assignments and other initialized declared outputs receive final validation.
An explicit selection validates only its entries; output names must still be declared, and commit inspection still applies.
Neither an empty selection nor a later rejection undoes earlier calls or in-place object mutations.
`Raise` permits only omitted or empty output selections.

For an explicit combination of current outputs and replacements, use ordinary Python:

```py
from nighthawk.runtime import Pass, StepCandidate, StepRequest


def select_result(request: StepRequest, result: object) -> StepCandidate:
    return StepCandidate(
        outcome=Pass(),
        binding_name_to_value={**request.snapshot_output_bindings(), "result": result},
    )
```

`snapshot_output_bindings()` returns a fresh dictionary in declaration order, preferring tracked assignments over current Python values, including live globals and cells.
Unbound outputs, temporaries, and task-only references are excluded.
The method preserves value identity and performs no validation or writes.
A snapshot fixes associations at the time it is taken; omission selects them later, during finalization.
`Rewrite` uses the same complete-mapping semantics after initial validation, with omission retaining the current commit field.

## Next steps

- **[Pydantic AI providers](pydantic-ai-providers.md)** -- Installation, model identifiers, credentials, and model settings for each Pydantic AI provider.
- **[Coding agent backends](coding-agent-backends.md)** -- Backend-specific settings, skills, MCP tool exposure, and working directory configuration.
- **[For coding agents](for-coding-agents.md)** -- Condensed, decision-oriented operational guide for coding agents (LLMs) building Python projects with Nighthawk.

Continue to **[Runtime configuration](runtime-configuration.md)** for scope composition, model selection, function admission, and request accounting.
