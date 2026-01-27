# Nighthawk

Nighthawk is a compact reimplementation of the core ideas of nightjarpy.

It keeps the "Nightjar" concept: write Python control flow, and embed a small Natural DSL inside functions (docstring Natural blocks and inline Natural blocks) that is executed by an LLM.

References:

- nightjarpy (upstream concept): https://github.com/psg-mit/nightjarpy

## Status

- This repo contains the library implementation, tests, and design docs.
- Targets OpenAI only (via `pydantic-ai-slim[openai]`).
- Default model: `openai:gpt-5-nano`.
- Recommended model: `openai:gpt-5.2`.
- Supported Python version: 3.14+ (by design).

## Documentation

- Design spec (canonical): `docs/design.md`
  - The implementation in `src/nighthawk/` is expected to match the spec.
- Roadmap (future only): `docs/roadmap.md`

## Safety model

This project assumes the Natural DSL source and any imported markdown are trusted, repository-managed assets.

Do not feed user-generated content (web forms, chat logs, CLI input, database text, external API responses) into template preprocessing or include helpers.

## Quick example (concept)

This is the intended style. See `docs/design.md` for the specification.

```py
import nighthawk as nh

@nh.fn
def calculate_average(numbers):
    """natural
    Consider the values of <numbers> and compute the semantic average as <:result>.
    """
    return result
```

Notes:

- A Natural block sentinel is strict: the string literal must begin with `natural\n` (no leading blank lines).
- `<:name>` controls which values are committed back into Python locals at Natural block boundaries.
