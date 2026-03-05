# Nighthawk

<div align="center">
<img src="docs/assets/nighthawk_logo-128x128.png" alt="logo" width="128px" margin="10px"></img>
</div>

Nighthawk is an experimental Python library exploring a simple split:

- Use **hard control** (Python code) for strict procedure, verification, and deterministic flow.
- Use **soft reasoning** (an LLM) for semantic interpretation inside small embedded "Natural blocks".

This repository is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Documentation

- **Quickstart** (hands-on guide): `docs/quickstart.md`
- **Specification** (canonical): `docs/design.md`
- **Roadmap** (future only): `docs/roadmap.md`

## Quick start

Prereqs: git, uv, Python 3.13+

```bash
git clone https://github.com/kurusugawa-computer/nighthawk-python.git
cd nighthawk-python
uv sync --extra openai
```

Create a `.env` file with your API key (gitignored by default):

```bash
OPENAI_API_KEY=sk-xxxxxxxxx
```

Minimal example:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini")
)

with nh.run(step_executor):

    @nh.natural_function
    def summarize_post(post: str) -> str:
        summary = ""
        """natural
        Read <post> and set <:summary> to a concise summary.
        """
        return summary

    print(summarize_post("Ship the patch by Friday and include migration notes."))
```

Run tests:

```bash
uv run pytest -q
```

## What is in this repository

- Package: `src/nighthawk/`
- Tests: `tests/`
- Design/spec docs: `docs/`

Constraints / defaults:

- Supported Python version: 3.13+ (by design).
- Default model: `openai-responses:gpt-5-nano`.
- Recommended model (quality): `openai-responses:gpt-5-mini`.
- Optional backends (extras):
  - `openai`: `pip install "nighthawk[openai] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`
  - `vertexai`: `pip install "nighthawk[vertexai] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`
  - `claude-code`: `pip install "nighthawk[claude-code] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`
  - `codex`: `pip install "nighthawk[codex] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`

Model identifiers:

- `StepExecutorConfiguration(model=...)` uses `provider:model`.
- For `claude-code` and `codex`, you can use `:default` to use the backend/provider default model.
  - Examples: `claude-code:default`, `codex:default`.

## Safety model

This project assumes the Natural DSL source and any imported markdown are trusted, repository-managed assets.

Do not feed user-generated content (web forms, chat logs, CLI input, database text, external API responses) into Natural blocks or any host-side interpolation helpers you define.

## Development

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
- Agent Skills (external article): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
