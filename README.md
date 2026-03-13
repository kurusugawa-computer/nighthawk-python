[![PyPI](https://img.shields.io/pypi/v/nighthawk-python)](https://pypi.org/project/nighthawk-python)
![PyPI - Downloads](https://img.shields.io/pypi/dm/nighthawk-python)
[![license](https://img.shields.io/github/license/kurusugawa-computer/nighthawk-python.svg)](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/LICENSE)
[![issue resolution](https://img.shields.io/github/issues-closed-raw/kurusugawa-computer/nighthawk-python)](https://github.com/kurusugawa-computer/nighthawk-python/issues)

# Nighthawk

<div align="center">
<img src="https://github.com/kurusugawa-computer/nighthawk-python/raw/main/docs/assets/nighthawk_logo-128x128.png" alt="nighthawk-logo" width="128px" margin="10px"></img>
</div>

Nighthawk is an experimental Python library exploring a clear separation between **hard control** (Python code) for strict procedure and deterministic flow, and **soft reasoning** (an LLM) for semantic interpretation inside small embedded "Natural blocks". It is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Quickstart

Prerequisites: Python 3.13+

Install Nighthawk and a provider:

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

Save as `quickstart.py`:

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
```

Run with your API key:

```bash
export OPENAI_API_KEY=sk-xxxxxxxxx
python quickstart.py
# => 20
```

For backends, credentials, model identifiers, and detailed guidance, see the [documentation site](https://kurusugawa-computer.github.io/nighthawk-python/).

## Development & Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, development commands, and contribution guidelines.

## References

- Nightjar (upstream concept): https://github.com/psg-mit/nightjarpy
