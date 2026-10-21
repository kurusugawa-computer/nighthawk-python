# Contributing to Nighthawk

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
# Clone the repository and install all dependencies
uv sync --all-extras --all-groups
```

`uv` manages the project virtual environment in `.venv`; run project commands through `uv run`.
The generated `.venv` directory is ignored by Git and recreated by `uv sync` or `uv run` when missing.

Declare project dependencies in `pyproject.toml` through `uv add`.

If you see a `uv` warning about hardlinking (common in containers or cross-filesystem workspaces), suppress it with:

```bash
export UV_LINK_MODE=copy
```

## Development commands

```bash
# Run Python
uv run python

# Format code
uv run ruff format .

# Lint (auto-fix)
uv run ruff check --fix .

# Type check
uv run pyright

# Build documentation, treating warnings as failures
uv run mkdocs build --strict

# Run tests
uv run pytest -q       # quiet output
```

Test ownership and helper conventions are documented in [tests/README.md](tests/README.md).

### Design and application evidence

Use [Philosophy](docs/philosophy.md) for design principles and [Specification](docs/specification.md) for supported runtime contracts.
[Design drivers](docs/design-drivers.md) defines which application information belongs in this workspace and how to distinguish conceptual scenarios from verified behavior.
When proposing Core changes, show the application composition that exposes the general execution need.
Nighthawk-based development checks are an application of the library; adopting a local check does not make it a public feature or a requirement for library users.

### Integration tests

The full setup command installs the OpenAI provider development dependency.
Integration tests are opt-in; loading credentials alone does not enable them.
Run OpenAI integration tests with your `.env` loaded:

```bash
NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q tests/integration/
```

Add `NIGHTHAWK_OPENAI_MULTIMODAL_INTEGRATION_TESTS=1` to include the image cases.
Coding agent tests use their respective flags: `NIGHTHAWK_CODEX_INTEGRATION_TESTS=1`, `NIGHTHAWK_CLAUDE_SDK_INTEGRATION_TESTS=1`, or `NIGHTHAWK_CLAUDE_CLI_INTEGRATION_TESTS=1`.
Their executables and authentication must be available in the test environment.

### Trace inspection with otel-tui

Nighthawk emits OpenTelemetry traces for step execution.
You can inspect them locally using [otel-tui](https://github.com/ymtdzzz/otel-tui):

```bash
docker run --rm -it -p 4318:4318 --name otel-tui ymtdzzz/otel-tui:latest
```

Then run integration tests with the collector endpoint:

```bash
NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
  uv run --env-file .env pytest -q tests/integration/
```

Traces appear in the otel-tui terminal UI in real time.

### Prompt evaluation with promptfoo

System prompt, tool descriptions, and backend behavior are evaluated with [promptfoo](https://www.promptfoo.dev/). Requires Node.js (for `npx`). API keys must be loaded from `.env` first.

```bash
set -a; source .env; set +a
PFOO="cd evals/promptfoo && PROMPTFOO_PYTHON=\"$(uv python find)\" npx promptfoo@latest"
```

| Command | Purpose |
|---|---|
| `eval $PFOO eval` | Full regression (all backends, all tests) |
| `eval $PFOO eval -c promptfooconfig-prompt-ab.yaml` | Prompt A/B test (gpt-5.6-luna only) |
| `eval $PFOO eval -c promptfooconfig-luna-sanity.yaml --no-cache` | Default-model sanity check (gpt-5.6-luna only) |
| `eval $PFOO eval -c promptfooconfig-agents.yaml` | Coding agent backends (reduced test set) |
| `eval $PFOO eval -c promptfooconfig.yaml --filter-pattern "P-BIND-001"` | Single test |
| `eval $PFOO eval -c promptfooconfig.yaml --filter-providers "claude-code-cli"` | Single backend |
| `eval $PFOO view` | Open results in browser |

#### Config files (`evals/promptfoo/`)

- `promptfooconfig.yaml` — Regression: winner prompt combo across openai-responses, claude-code-cli, and codex backends. All test cases.
- `promptfooconfig-prompt-ab.yaml` — A/B testing: 4 prompt/tool variants on gpt-5.6-luna. All test cases.
- `promptfooconfig-luna-sanity.yaml` — Default-model sanity check on openai-responses:gpt-5.6-luna. All test cases.
- `promptfooconfig-agents.yaml` — Coding agent only: claude-code-cli and codex with reduced test set.

#### Directory layout

- `provider.py` -- Executes trusted fixtures through `StepDefinition` and `execute_step`, including validation, writeback, terminal records, and usage metering. Experimental tool and suffix variants are restored after each serialized call. Return reports contain resolved `return_value`; `step_locals` reports the explicit namespace after writes and in-place effects.
- `prompts/` — System prompt variants (`eval_default.txt`, `eval_sequenced.txt`, `eval_mutation_aware.txt`, `eval_coding_agent.txt`, etc.).
- `test_cases/` -- YAML suites for bindings, tool selection, outcomes, loops, multi-step execution, and edge cases.
- `assertions/` — Custom Python assertions: `binding_value.py`, `outcome_kind.py`, `raise_message.py`.

#### Adding tests

Each test case YAML entry needs: `description` (with `P-SLUG-NNN` Id), `vars` (natural_program, input_bindings, output_binding_names), and `assert` list. Callable bindings use `"__callable:<key>"` resolved from `_FIXTURE_NAME_TO_CALLABLE` in `provider.py`.

#### Useful flags

- `--no-cache` — Skip cache (required after changing provider.py or prompts).
- `--filter-pattern "<regex>"` — Run only matching test descriptions.
- `--filter-providers "<regex>"` — Run only matching provider labels.
- `-o <path>.json` — Write structured results to file for analysis.

### Environment variables

- `OPENAI_API_KEY`: Required for OpenAI integration tests (also requires `pydantic-ai-slim[openai]`).
- `CODEX_API_KEY`: Required for Codex integration tests.

## Docstring Guide

Document the public API rendered by `docs/api.md` using Google-style docstrings.
Follow [.claude/rules/python.md](.claude/rules/python.md) for comments and internal docstrings.

Start with a concise summary of the operation, using imperative mood for functions.
Describe the contract and any non-obvious consequences; do not repeat annotations or narrate the implementation.
Keep examples in `docs/`, where they can be executed and checked against the runtime.

| Section | Content |
| --- | --- |
| `Args` | Parameter meaning and constraints beyond its type. |
| `Returns` / `Yields` | The returned value's meaning when not clear from the signature. |
| `Raises` | Exceptions callers are expected to handle. |
| `Attributes` | Public model fields and their semantics. |

Put class documentation on the class body.
MkDocstrings merges constructor parameters through `merge_init_into_class: true`.
Use semantic line breaks and keep internal docstrings only for facts not conveyed by the code.
