[![PyPI](https://img.shields.io/pypi/v/nighthawk-python)](https://pypi.org/project/nighthawk-python)
[![PyPI Stats](https://img.shields.io/pypi/dm/nighthawk-python)](https://pypistats.org/packages/nighthawk-python)
[![license](https://img.shields.io/github/license/kurusugawa-computer/nighthawk-python.svg)](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/LICENSE)
[![issue resolution](https://img.shields.io/github/issues-closed-raw/kurusugawa-computer/nighthawk-python)](https://github.com/kurusugawa-computer/nighthawk-python/issues)

# Nighthawk

<div align="center">
<img src="https://github.com/kurusugawa-computer/nighthawk-python/raw/main/docs/assets/nighthawk_logo-128x128.png" alt="nighthawk-logo" width="128px" margin="10px"></img>
</div>

Nighthawk is a Python library where Python controls flow and LLMs or coding agents reason within constrained Natural blocks.

- **Hard control** (Python code): strict procedure, verification, and deterministic flow.
- **Soft reasoning** (an LLM or coding agent): semantic interpretation inside small embedded "Natural blocks".

The same mechanism handles lightweight LLM judgments ("classify this sentiment") and autonomous agent executions ("refactor this module and write tests"). See **[Philosophy](https://kurusugawa-computer.github.io/nighthawk-python/philosophy/)** for the full design rationale.

This repository is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Installation

Prerequisites: Python 3.13+

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

For other providers, see [Pydantic AI providers](https://kurusugawa-computer.github.io/nighthawk-python/pydantic-ai-providers/).

## Example

```py
import nighthawk as nh

def python_average(numbers):
    return sum(numbers) / len(numbers)

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-nano")
)

with nh.run(step_executor):

    @nh.natural_function
    def calculate_average(numbers):
        """natural
        Map each element of <numbers> to the number it represents,
        then compute <:result> by calling <python_average> with the mapped list.
        """
        return result

    calculate_average([1, "2", "three", "cuatro"])  # 2.5
```

See the **[Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/)** for setup details, credentials, and troubleshooting.

## Documentation

- **[Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/)** -- Setup and first example.
- **[Natural blocks](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/)** -- Block anatomy, bindings, functions, and writing guidelines.
- **[Executors](https://kurusugawa-computer.github.io/nighthawk-python/executors/)** -- Choose an execution backend.
- **[Runtime configuration](https://kurusugawa-computer.github.io/nighthawk-python/runtime-configuration/)** -- Scoping, patching, context limits, and execution identity.
- **[Patterns](https://kurusugawa-computer.github.io/nighthawk-python/patterns/)** -- Outcomes, async, composition, resilience, and common mistakes.
- **[Verification](https://kurusugawa-computer.github.io/nighthawk-python/verification/)** -- Mock tests, integration tests, and OpenTelemetry tracing.
- **[Pydantic AI providers](https://kurusugawa-computer.github.io/nighthawk-python/pydantic-ai-providers/)** -- LLM provider configuration.
- **[Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/)** -- Claude Code and Codex integration.
- **[Specification](https://kurusugawa-computer.github.io/nighthawk-python/specification/)** -- Canonical specification.
- **[API Reference](https://kurusugawa-computer.github.io/nighthawk-python/api/)** -- Auto-generated API documentation.
- **[For coding agents](https://kurusugawa-computer.github.io/nighthawk-python/for-coding-agents/)** -- Development guide for coding agents (LLMs) building Python projects with Nighthawk.
- **[Philosophy](https://kurusugawa-computer.github.io/nighthawk-python/philosophy/)** -- Design rationale and positioning.
- **[Roadmap](https://kurusugawa-computer.github.io/nighthawk-python/roadmap/)** -- Future directions.

## Development & Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, development commands, and contribution guidelines.

## References

- Nightjar (upstream concept): https://github.com/psg-mit/nightjarpy
