[![PyPI](https://img.shields.io/pypi/v/nighthawk-python)](https://pypi.org/project/nighthawk-python)
[![PyPI Stats](https://img.shields.io/pypi/dm/nighthawk-python)](https://pypistats.org/packages/nighthawk-python)
[![license](https://img.shields.io/github/license/kurusugawa-computer/nighthawk-python.svg)](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/LICENSE)
[![issue resolution](https://img.shields.io/github/issues-closed-raw/kurusugawa-computer/nighthawk-python)](https://github.com/kurusugawa-computer/nighthawk-python/issues)

# Nighthawk

<div align="center">
<img src="https://github.com/kurusugawa-computer/nighthawk-python/raw/main/docs/assets/nighthawk_logo-128x128.png" alt="nighthawk-logo" width="128px" margin="10px"></img>
</div>

Nighthawk is an experimental Python library exploring a clear separation:

- Use **hard control** (Python code) for strict procedure, verification, and deterministic flow.
- Use **soft reasoning** (an LLM or coding agent) for semantic interpretation inside small embedded "Natural blocks".

Python controls all flow; the LLM or coding agent is constrained to small Natural blocks with explicit input/output boundaries. The same mechanism handles lightweight LLM judgments ("classify this sentiment") and autonomous agent executions ("refactor this module and write tests"). See **[Philosophy](https://kurusugawa-computer.github.io/nighthawk-python/philosophy/)** for the full design rationale.

This repository is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Installation

Prerequisites: Python 3.13+

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

For other providers, see [Providers](https://kurusugawa-computer.github.io/nighthawk-python/providers/).

## Example

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini")
)

with nh.run(step_executor):

    @nh.natural_function
    def calculate_total(items: str) -> int:
        total = 0
        """natural
        Read <items> and set <:total> to the sum of all quantities mentioned.
        """
        return total

    print(calculate_total("three apples, a dozen eggs, and 5 oranges"))
    # => 20
```

See the **[Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/)** for setup details, credentials, and troubleshooting.

## Documentation

- **[Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/)** — Setup and first example.
- **[Tutorial](https://kurusugawa-computer.github.io/nighthawk-python/tutorial/)** — Learn from first principles.
- **[Practices](https://kurusugawa-computer.github.io/nighthawk-python/practices/)** — Guidelines, patterns, and testing.
- **[Providers](https://kurusugawa-computer.github.io/nighthawk-python/providers/)** — LLM providers and configuration.
- **[Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/)** — Claude Code and Codex integration.
- **[Philosophy](https://kurusugawa-computer.github.io/nighthawk-python/philosophy/)** — Design rationale and positioning.
- **[Design](https://kurusugawa-computer.github.io/nighthawk-python/design/)** — Canonical specification.
- **[API Reference](https://kurusugawa-computer.github.io/nighthawk-python/api/)** — Auto-generated API documentation.
- **[Roadmap](https://kurusugawa-computer.github.io/nighthawk-python/roadmap/)** — Future directions.

## Development & Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, development commands, and contribution guidelines.

## References

- Nightjar (upstream concept): https://github.com/psg-mit/nightjarpy
