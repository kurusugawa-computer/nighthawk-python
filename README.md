# Nighthawk

<div align="center">
<img src="docs/assets/nighthawk_logo-128x128.png" alt="logo" width="128px" margin="10px"></img>
</div>

Nighthawk is an experimental Python library exploring a clear separation between **hard control** (Python code) for strict procedure and deterministic flow, and **soft reasoning** (an LLM) for semantic interpretation inside small embedded "Natural blocks". It is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Quickstart

Prerequisites: Python 3.13+

Install with the OpenAI backend:

```bash
pip install "nighthawk[openai] @ git+https://github.com/kurusugawa-computer/nighthawk-python"
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

## Development

Run tests:

```bash
uv run pytest -q
```

Run an OTel collector UI (otel-tui) for observability:

```bash
docker run --rm -it -p 4318:4318 --name otel-tui ymtdzzz/otel-tui:latest
```

Then run integration tests with `OTEL_EXPORTER_OTLP_ENDPOINT` set:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run pytest -q tests/integration/test_llm_integration.py
```

## References

- Nightjar (upstream concept): https://github.com/psg-mit/nightjarpy
