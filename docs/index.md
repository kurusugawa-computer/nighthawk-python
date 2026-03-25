# Nighthawk

<div align="center">
<img src="assets/nighthawk_logo-128x128.png" alt="logo" width="128px">
</div>

Nighthawk is an experimental Python library exploring a clear separation:

- Use **hard control** (Python code) for strict procedure, verification, and deterministic flow.
- Use **soft reasoning** (an LLM or coding agent) for semantic interpretation inside small embedded "Natural blocks".

Python controls all flow; the LLM or coding agent is constrained to small Natural blocks with explicit input/output boundaries. The same mechanism handles lightweight LLM judgments ("classify this sentiment") and autonomous agent executions ("refactor this module and write tests"). See **[Philosophy](philosophy.md)** for the full design rationale.

This repository is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Documentation

- **[Quickstart](quickstart.md)** — Setup and first example.
- **[Tutorial](tutorial.md)** — Learn from first principles.
- **[Practices](practices.md)** — Guidelines, patterns, and testing.
- **[Providers](providers.md)** — LLM providers and configuration.
- **[Coding agent backends](coding-agent-backends.md)** — Claude Code and Codex integration.
- **[Philosophy](philosophy.md)** — Design rationale and positioning.
- **[Design](design.md)** — Canonical specification.
- **[API Reference](api.md)** — Auto-generated API documentation.
- **[Roadmap](roadmap.md)** — Future directions.
- **[For coding agents](for-coding-agents.md)** — LLM development reference.

## References

- Nightjar (upstream concept): <https://github.com/psg-mit/nightjarpy>
