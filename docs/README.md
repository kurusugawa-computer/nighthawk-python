# Nighthawk

Nighthawk is a compact reimplementation of the core ideas of nightjarpy.

It keeps the "Nightjar" concept: write Python control flow, and embed a small Natural DSL inside functions (docstring Natural blocks and inline Natural blocks) that is executed by an LLM.

References:

- nightjarpy (upstream concept): https://github.com/psg-mit/nightjarpy

## Status

- This repo currently contains design docs and project scaffolding.
- MVP targets OpenAI only (via `pydantic-ai-slim[openai]`).
- MVP default model: `gpt-5.2`.
- Configuration is provided via `NIGHTHAWK_*` environment variables.
- Supported Python version: 3.14+ (by design).

## Documentation

- Design spec (MVP): `docs/design.md`
- Roadmap and non-MVP items: `docs/roadmap.md`

## Safety model (MVP)

MVP assumes the Natural DSL source and any imported markdown are trusted, repository-managed assets.

### Trusted inputs (MVP)

MVP assumes Natural blocks and any included markdown are trusted, repository-managed assets.
Do not feed user-generated content (web forms, chat logs, CLI input, database text, external API responses) into template preprocessing or include helpers.

The MVP is intentionally permissive:

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
