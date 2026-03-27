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

- **[Quickstart](quickstart.md)** — Setup and first example. Start here.
- **[Tutorial](tutorial.md)** — Learn bindings, functions, control flow, and composition from first principles.
- **[Practices](practices.md)** — Writing guidelines, binding function design, testing, observability, and resilience patterns.
- **[Providers](providers.md)** — Choose and configure an LLM provider (Pydantic AI, coding agent, or custom).
- **[Coding agent backends](coding-agent-backends.md)** — Claude Code and Codex backend configuration, skills, and MCP tool exposure.
- **[Philosophy](philosophy.md)** — Design rationale: workflow styles, comparison with workflow engines, tool exposure tradeoffs.
- **[Design](design.md)** — Canonical specification (target behavior for implementation).
- **[Roadmap](roadmap.md)** — Future directions and open questions.
- **[API Reference](api.md)** — Auto-generated API documentation from source docstrings.
- **[For coding agents](for-coding-agents.md)** — Condensed development reference for coding agents (LLMs) working on Nighthawk projects.

## References

- Nightjar (upstream concept): <https://github.com/psg-mit/nightjarpy>
