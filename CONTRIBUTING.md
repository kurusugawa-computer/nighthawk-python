# Contributing to Nighthawk

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
# Clone the repository and install all dependencies
uv sync --all-extras --all-groups
```

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

# Run tests
uv run pytest -q       # quiet output
```

### Integration tests

Integration tests require provider packages and API keys. Install the provider you need:

```bash
uv pip install pydantic-ai-slim[openai]
```

Then run with your `.env` loaded:

```bash
set -a; source .env; set +a; uv run pytest -q
```

### Trace inspection with otel-tui

Nighthawk emits OpenTelemetry traces for step execution. You can inspect them
locally using [otel-tui](https://github.com/ymtdzzz/otel-tui):

```bash
docker run --rm -it -p 4318:4318 --name otel-tui ymtdzzz/otel-tui:latest
```

Then run integration tests with the collector endpoint:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run pytest -q tests/integration/
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
| `eval $PFOO eval -c promptfooconfig-prompt-ab.yaml` | Prompt A/B test (gpt-5.4-mini only) |
| `eval $PFOO eval -c promptfooconfig-agents.yaml` | Coding agent backends (reduced test set) |
| `eval $PFOO eval -c promptfooconfig.yaml --filter-pattern "P-BIND-001"` | Single test |
| `eval $PFOO eval -c promptfooconfig.yaml --filter-providers "claude-code-cli"` | Single backend |
| `eval $PFOO view` | Open results in browser |

#### Config files (`evals/promptfoo/`)

- `promptfooconfig.yaml` — Regression: winner prompt combo across openai-responses, claude-code-cli, and codex backends. All test cases.
- `promptfooconfig-prompt-ab.yaml` — A/B testing: 4 prompt/tool variants on gpt-5.4-mini. All test cases.
- `promptfooconfig-agents.yaml` — Coding agent only: claude-code-cli and codex with reduced test set.

#### Directory layout

- `provider.py` — Custom provider wrapping `AgentStepExecutor`. Handles tool preset installation, backend-specific model settings, and callable fixture resolution.
- `prompts/` — System prompt variants (`eval_default.txt`, `eval_sequenced.txt`, `eval_mutation_aware.txt`, `eval_coding_agent.txt`, etc.).
- `test_cases/` — YAML test suites: `binding_operations`, `tool_selection`, `outcome_kinds`, `edge_cases`, `loop_outcomes`, `multi_step`, `null_handling`, `tool_selection_core`.
- `assertions/` — Custom Python assertions: `binding_value.py`, `outcome_kind.py`, `raise_message.py`.

#### Adding tests

Each test case YAML entry needs: `description` (with `P-SLUG-NNN` Id), `vars` (natural_program, input_bindings, output_binding_names), and `assert` list. Callable bindings use `"__callable:<key>"` resolved from `_CALLABLE_FIXTURE_REGISTRY` in `provider.py`.

#### Useful flags

- `--no-cache` — Skip cache (required after changing provider.py or prompts).
- `--filter-pattern "<regex>"` — Run only matching test descriptions.
- `--filter-providers "<regex>"` — Run only matching provider labels.
- `-o <path>.json` — Write structured results to file for analysis.

### Environment variables

- `OPENAI_API_KEY`: Required for OpenAI integration tests (also requires `pydantic-ai-slim[openai]`).
- `CODEX_API_KEY`: Required for Codex integration tests.

## Docstring Guide

This guide defines the docstring conventions for the Nighthawk codebase.

### Style

Use [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings) docstrings. This is the default style parsed by mkdocstrings.

### Scope

Write docstrings only for public API members exported via `__all__` in `src/nighthawk/__init__.py`, plus public classes and functions in these modules:

- `nighthawk.errors`
- `nighthawk.configuration`
- `nighthawk.backends.claude_code_sdk`
- `nighthawk.backends.claude_code_cli`
- `nighthawk.backends.codex`

Do not write docstrings for names prefixed with `_` (module-private).

### Structure

#### Summary line

Required. Use imperative mood ("Return ...", "Start ...", "Register ..."). Keep it on one line.

#### Sections

| Section | When to include |
|---|---|
| `Args` | One or more parameters exist. Do not repeat type information already present in annotations. |
| `Returns` / `Yields` | Return value is not `None`, or carries meaning that is not obvious from the type alone. |
| `Raises` | The function raises exceptions that callers are expected to catch. |
| `Example` | Required for decorators (`natural_function`, `tool`) and context managers (`run`, `scope`). Optional for everything else. |

#### Classes

Write the docstring on the class body, not on `__init__`. mkdocstrings merges `__init__` parameters automatically (`merge_init_into_class: true`).

For Pydantic models, describe fields in an `Attributes` section:

```python
class StepExecutorConfiguration(BaseModel):
    """Configuration for a step executor.

    Attributes:
        model: Model identifier in "provider:model" format (e.g. "openai:gpt-4o").
        prompts: Prompt templates for step execution.
        context_limits: Token and item limits for context rendering.
    """
```

### What not to write

- Do not duplicate type annotations in `Args` descriptions.
- Do not add docstrings just to satisfy a linter; omit them when the signature alone is self-explanatory.
- Do not document internal implementation details in public docstrings.

### Full example

```python
def run(
    step_executor: StepExecutor,
    *,
    run_id: str | None = None,
) -> Iterator[None]:
    """Start an execution run with the given step executor.

    Establishes a run-scoped context that makes the step executor
    available to all Natural blocks executed within this scope.

    Args:
        step_executor: The step executor to use for Natural block execution.
        run_id: Optional identifier for the run. If not provided, a ULID is
            generated automatically.

    Yields:
        None

    Raises:
        RuntimeError: If called while another run is already active.

    Example:
        ```python
        executor = AgentStepExecutor.from_configuration(
            configuration=StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini"),
        )
        with nighthawk.run(executor):
            result = my_natural_function()
        ```
    """
```
