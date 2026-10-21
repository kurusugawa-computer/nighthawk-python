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

The shared suite evaluates production Natural execution, requested Python calls, and their results and effects.
The [Python provider](https://www.promptfoo.dev/docs/providers/python/) passes configuration directly to `StepExecutorConfiguration` and executes each trusted fixture through `execute_step_async`.
Production prompts and built-in tools are used without private replacements.

Node.js and the project development environment are required.
From the repository root, define this shell helper:

```bash
promptfoo() (
  cd evals/promptfoo || exit
  uv run --env-file ../../.env env \
    PROMPTFOO_PYTHON="$(uv python find)" \
    PYDANTIC_AI_NO_BANNER=1 \
    npx --yes promptfoo@0.124.1 "$@"
)
```

`uv` loads credentials from `.env`; the helper leaves the caller's directory unchanged.
The CLI version is pinned to the version used for the recorded evaluation.
`promptfooconfig.yaml` contains all providers and one shared test list.
Select providers explicitly for the credentials and executables available:

| Command | Purpose |
| --- | --- |
| `promptfoo eval --filter-providers "^openai-responses:"` | Full OpenAI regression suite. |
| `promptfoo eval --filter-providers "^openai-responses:" --repeat 3 -o outputs/production.json` | Repeated evaluation with raw results. |
| `promptfoo eval --filter-providers "^codex:"` | Same suite through the configured Codex executable. |
| `promptfoo eval --filter-providers "^claude-code-cli:"` | Same suite through the configured Claude Code executable. |
| `promptfoo eval --filter-providers "^openai-responses:" --filter-pattern "P-BIND-001"` | One fixture. |
| `promptfoo view` | Inspect local evaluation results. |

The configuration disables result caching because runtime, adapter, and assertion changes must be executed.
Provider filters are regular expressions matched against labels.
See the [CLI reference](https://www.promptfoo.dev/docs/usage/command-line/) for additional options.
OpenAI requires `OPENAI_API_KEY`; coding-agent executables and authentication follow the [backend guide](docs/coding-agent-backends.md).

#### Fixtures and reports

`test_cases/` contains trusted programs with stable `P-SLUG-NNN` descriptions, JSON-encoded input bindings, ordered output names, and assertions.
Optional `global_bindings` exposes named builtins through `"__builtin__"`.
Optional `allowed_outcome_kinds` selects the fixture's loop context and restricts outcomes while retaining its frontmatter denials.

Callable values use `"__callable:<name>"`, resolved by `_FIXTURE_NAME_TO_CALLABLE` in `provider.py`.
Calls are recorded with arguments bound to their parameter names, so equivalent positional and keyword calls compare equally.
The record describes an attempted invocation; it is not a transaction or a success guarantee.

Reports contain committed `bindings`, the explicit namespace after writes and mutations in `step_locals`, resolved `return_value`, DSL raise details, and `function_calls`.
Runtime failures remain errors and retain already observed effects.
`tokenUsage` is the completed-agent-run aggregate, including when later value validation fails; it may not expose usage from an interrupted or failed provider run.

The [Python assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/python/) check values, outcomes, raise details, and requested function calls.
Binding assertions require committed values by default.
Set `check_step_locals: true` when a fixture intentionally checks an in-place mutation.
Return fixtures specify `expected_return_value` as well as the control result.

Run adapter and assertion tests with `uv run pytest -q tests/evals/`.
Keep raw results in ignored `outputs/` and record decisions and limitations in dated Markdown under `evidence/`.
For active experiments, add explicit public configuration overrides beside the production control and follow [.claude/rules/promptfoo.md](.claude/rules/promptfoo.md).

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
