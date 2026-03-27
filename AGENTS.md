# AI Agent instructions for nighthawk-python

## NON-NEGOTIABLE REQUIREMENTS

- Do not edit files until all questions in the current chat session are resolved and explicit user permission is granted (except ExecPlans, Ideal State documents).
- In chat sessions, communicate in Japanese; when writing to files, use English; internal reasoning may use any language.
- When listing questions, confirmations, or proposed decisions, assign a short stable Id to each item so the user can respond inline.
  - Format: `Q-SLUG-001` (questions), `C-SLUG-001` (confirmations), `P-SLUG-001` (proposals). Follow-ups: append suffix like `Q-SLUG-001A`.
  - Each item must be answerable on its own and must include its Id.
- When the user requests "radical" changes, prioritize extensive, global, disruptive, and thorough edits over minimal fixes.

## Naming and terminology

### Allowed abbreviations (Glossary)

`Id` (Identifier), `DSL` (Domain Specific Language), `LLM` (Large Language Model), `NH`/`nh` (Nighthawk), `max` (maximum), `min` (minimum), loop indices `i`/`j`/`k`, type parameters `P` (ParamSpec), `R` (Return type), `T` (Type variable).

### Rules

- Use full words in all identifiers unless listed above.
- Disallowed abbreviations: `ctx` -> `context`, `cfg` -> `configuration`, `repo` -> `repository`, `opts` -> `options`, `ref` -> `reference`.
- Map/Dict: `(adjective + "_")* + key + "_to_" + value`.
  - Do not pluralize `value`; collections use explicit suffix (`_list`, `_set`, `_tuple`).
  - Examples: `binding_name_to_type`, `binding_name_to_field_name_to_value`.
  - Counter-example: `binding_types_dict_expression` (dict-literal expression, not a lookup).
  - Fix violations opportunistically when touching the code.
- Ask the user before doing broad renames across a file or the codebase.
- ASCII punctuation only: `'` (U+0027) and `"` (U+0022). No smart quotes.

## Project orientation

A Python 3.13+ library embedding a "Natural" DSL inside Python functions, executed using an LLM. Provider dependencies are installed via extras. No CLI entry point.

Natural DSL sources and included markdown are trusted, repository-managed assets. Do not wire untrusted user input into Natural blocks or template preprocessing.

### Repository layout

- `src/nighthawk/`: Library package.
- `tests/`: Pytest suite.
- `docs/`: `quickstart.md` (first steps), `tutorial.md` (first principles), `design.md` (specification), `roadmap.md` (future items).
- `.agents/`: `execplans/` (on request only), `PLANS.md` (format spec).
- `.devcontainer/`: Devcontainer definition.
- `pyproject.toml`, `uv.lock`: Metadata and locked dependencies.

## Development workflow

Python 3.13+, `uv` for dependencies, `pytest` for tests. Prefer LSP-based tooling for code analysis.

| Command | Purpose |
|---|---|
| `uv run python` | Investigate interactively |
| `uv sync --all-extras --all-groups` | Install/sync dependencies |
| `uv run ruff format .` | Format |
| `uv run ruff check --fix .` | Auto-fix lint |
| `uv run pyright` | Type check |
| `uv run pytest -q` | Tests (quiet) |
| `NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 uv run pytest -q` | Integration tests (OpenAI) |
| `NIGHTHAWK_CODEX_INTEGRATION_TESTS=1 uv run pytest -q` | Integration tests (Codex) |
| `NIGHTHAWK_CLAUDE_SDK_INTEGRATION_TESTS=1 uv run pytest -q` | Integration tests (Claude Code SDK) |
| `NIGHTHAWK_CLAUDE_CLI_INTEGRATION_TESTS=1 uv run pytest -q` | Integration tests (Claude Code CLI) |

`uv` hardlinking warnings do not indicate failure. Suppress: `export UV_LINK_MODE=copy`.

Environment: `OPENAI_API_KEY` (OpenAI), `CODEX_API_KEY` (Codex).

Promptfoo evaluation details (commands, configs, directory layout, flags): see `CONTRIBUTING.md` "Prompt evaluation with promptfoo".
