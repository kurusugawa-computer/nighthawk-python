# Nighthawk

Nighthawk is a compact reimplementation of the core ideas of nightjarpy.

It keeps the "Nightjar" concept: write Python control flow, and embed a small Natural DSL inside functions (docstring Natural blocks and inline Natural blocks) that is executed by an LLM.

References:

- nightjarpy (upstream concept): https://github.com/psg-mit/nightjarpy

## Status

- This repo contains the library implementation, tests, and design docs.
- Targets OpenAI only (via `pydantic-ai-slim[openai]`).
- Default model: `gpt-5.2`.
- Configuration is provided by constructing a `Configuration` and wiring an `ExecutionEnvironment` explicitly (see `docs/design.md`).
- Supported Python version: 3.14+ (by design).

## Documentation

- Design spec: `docs/design.md`
- Roadmap: `docs/roadmap.md`

## Code layout

- Library code: `src/nighthawk/`
  - `configuration.py`: configuration and prompt templates
  - `execution/`: runtime execution (environment, context, orchestrator, executors, LLM integration)
  - `natural/`: Natural block parsing and AST transformation
  - `tools/`: tool registry, provided tools, and assignment helpers
- Tests: `tests/`
  - `public/`: public API behavior (`import nighthawk as nh`)
  - `execution/`: runtime behavior
  - `natural/`: Natural parsing and transform behavior
  - `tools/`: tool registry and tool behavior
  - `integration/`: optional integration tests (guarded by `NIGHTHAWK_RUN_INTEGRATION_TESTS=1`)

## Safety model

This project assumes the Natural DSL source and any imported markdown are trusted, repository-managed assets.

### Trusted inputs

Assume Natural blocks and any included markdown are trusted, repository-managed assets.
Do not feed user-generated content (web forms, chat logs, CLI input, database text, external API responses) into template preprocessing or include helpers.

The current implementation is intentionally permissive:

- The Natural DSL may be preprocessed by evaluating Python template strings.
- The LLM may be given access to Python tools and to a tool-local workspace.

This is powerful and can be dangerous. Do not use with untrusted inputs.

## Hygiene

- Ensure secrets (e.g., `OPENAI_API_KEY`) are never committed to the repository.

## Quick example (concept)

This is the intended style (API names may change; see `docs/design.md` for the specification).

```py
import nighthawk as nh

@nh.fn
def calculate_average(numbers):
    """natural
    Consider the values of <numbers> and compute the semantic average as <:result>.
    """
    return result
```
