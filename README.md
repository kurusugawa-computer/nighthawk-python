[![PyPI](https://img.shields.io/pypi/v/nighthawk-python)](https://pypi.org/project/nighthawk-python)
[![PyPI Stats](https://img.shields.io/pypi/dm/nighthawk-python)](https://pypistats.org/packages/nighthawk-python)
[![license](https://img.shields.io/github/license/kurusugawa-computer/nighthawk-python.svg)](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/LICENSE)
[![issue resolution](https://img.shields.io/github/issues-closed-raw/kurusugawa-computer/nighthawk-python)](https://github.com/kurusugawa-computer/nighthawk-python/issues)

# Nighthawk

<div align="center">
<img src="https://github.com/kurusugawa-computer/nighthawk-python/raw/main/docs/assets/nighthawk_logo-128x128.png" alt="nighthawk-logo" width="128px" margin="10px"></img>
</div>

Nighthawk is a Python library that makes natural-language judgment composable with ordinary Python functions.

Use Natural blocks for interpretation, classification, and other judgments; combine actions, constraints, and verification through Python functions and existing libraries.
Python owns the surrounding control flow, state, and recovery.
The same typed boundaries support provider-backed LLM calls and coding-agent executions.

## Installation

Prerequisites: Python 3.13+

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

For other providers, see [Pydantic AI providers](https://kurusugawa-computer.github.io/nighthawk-python/pydantic-ai-providers/).

## Example

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


with nh.run(execution_model="openai-responses:gpt-5.6-luna"):
    print(calculate_average([1, "2", "three", "cuatro"]))  # 2.5
```

Natural interprets the mixed representations, `python_average` performs the deterministic calculation, and the output binding validates `result` as a `float` before Python resumes.
Type validation constrains the result's shape; it does not prove the interpretation is correct.

See the **[Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/)** for setup details, credentials, and troubleshooting.

## Embedding in an application

Hosts can admit whole function calls, select models without losing scoped controls, and observe block outcomes and model usage.
See [Runtime configuration](https://kurusugawa-computer.github.io/nighthawk-python/runtime-configuration/) for usage and [execution boundaries](https://kurusugawa-computer.github.io/nighthawk-python/specification/#embedded-execution-boundaries) for the guarantees.
The [host integration guide](https://kurusugawa-computer.github.io/nighthawk-python/host-integration/#whole-function-admission) includes executable examples that run without credentials.

## Documentation

- [Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/) -- Install and run a Natural function.
- [Typed decisions](https://kurusugawa-computer.github.io/nighthawk-python/decisions/) -- Integrate typed judgments with `decide` and scoped decision models.
- [Natural blocks](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/) -- Design prompts, bindings, and Python helpers.
- [Patterns](https://kurusugawa-computer.github.io/nighthawk-python/patterns/) and [Verification](https://kurusugawa-computer.github.io/nighthawk-python/verification/) -- Compose, test, and debug workflows.
- [Specification](https://kurusugawa-computer.github.io/nighthawk-python/specification/) and [API reference](https://kurusugawa-computer.github.io/nighthawk-python/api/) -- Runtime contracts and public interfaces.
- [For coding agents](https://kurusugawa-computer.github.io/nighthawk-python/for-coding-agents/) -- A compact operational guide.
- [Philosophy](https://kurusugawa-computer.github.io/nighthawk-python/philosophy/) and [Design drivers](https://kurusugawa-computer.github.io/nighthawk-python/design-drivers/) -- Principles, application needs, and Core responsibility.

## Development & Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, development commands, and contribution guidelines.

Inspired by [Nightjar](https://github.com/psg-mit/nightjarpy).
