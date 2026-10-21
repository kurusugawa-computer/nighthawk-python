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

### Model evaluations with pytest

Model evaluations use ordinary Python fixtures, production execution, and direct pytest assertions.
`tests/integration/test_model_behavior.py` checks embedded values, Python object identity, requested calls, mutations, exceptions, and control outcomes.
`tests/integration/test_natural_composition.py` checks actual Natural parent/child calls, inherited policy, and Python iteration around model judgments.
The synthetic inputs assess these tasks; their scores do not establish broad application reliability or a security boundary.

Select a model explicitly to enable these tests; loading credentials or enabling a provider's separate integration gate does not enable them.
From the repository root:

```bash
uv run --env-file .env pytest -q tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-5.6-luna \
  --evaluation-repeat 3
```

| Option | Purpose |
| --- | --- |
| `--evaluation-model MODEL` | Select a production model identifier. Repeat the option to compare models on the same cases. |
| `--evaluation-repeat N` | Run each selected case/model N times with fresh fixture state; defaults to one. |
| `--evaluation-settings JSON` | Supply `execution_model_settings` as a JSON object; defaults to production defaults. Applies to every selected model. |
| `--evaluation-output DIRECTORY` | Write to a new directory. Defaults to a timestamped directory under ignored `evals/outputs/`. Existing directories are rejected. |
| `-k EXPRESSION` | Select ordinary pytest test names or parameter Ids. |
| `--collect-only` | Inspect the selected matrix without execution or result files. |

For example, compare two models by supplying both `--evaluation-model openai-responses:gpt-5.6-luna` and `--evaluation-model openai-responses:gpt-5.6-sol`.
For a quick check, add `-k python_call_preserves_identity`.
For a provider-specific setting, add `--evaluation-settings '{"openai_reasoning_effort":"medium"}'`.
Run backends separately when their settings differ.
Coding-agent identifiers, executables, and authentication follow the [backend guide](docs/coding-agent-backends.md); their configuration is not replaced by evaluation-specific tools or prompts.

Execution is sequential and every requested repetition runs, including after a failed trial.
Pytest's `-x` and `--maxfail` can stop early; remaining trials are recorded as `not_run`.
There is no success-seeking retry or result cache.
Ordinary pytest failure reporting and exit status remain authoritative.

#### Evidence

Each run writes:

- `manifest.json`: selected tests, start time, Git revision, source-file hashes, and Python/package versions.
- `trial-*.json`: model/settings, case parameters, repetition, pytest phase results, observations, lifecycle results, available model transcripts, elapsed time, and completed-agent usage.
- `summary.json`: counts and per-case/model pass rates, elapsed time, and observed usage totals.

The summary distinguishes `passed`, assertion `failed`, execution/setup/teardown `error`, `interrupted`, `skipped`, and `not_run`.
Pass rates include failed, errored, and interrupted attempts; skipped and unattempted trials remain visible outside the denominator.
Inspect both counts and rates before comparing runs.
Completed-agent usage can omit interrupted or failed provider requests and is not a billing record.
Missing transcripts and zero observed usage do not establish that no provider work occurred.
Records are updated after pytest phases; abrupt process termination can leave a `running` record and no final summary.

The `model_evaluation` fixture supplies the run, production configuration, usage meter, and lifecycle collection.
Keep mutable inputs and callable effects local to each trial.
Store references to useful state in `model_evaluation.name_to_observation` before execution so failed attempts retain diagnostic effects.
Tests that replace lifecycle handlers forward terminal records to `model_evaluation.steps`.
Assertions inspect live Python values directly; serialization is only for evidence.
Opaque values become type markers and unsupported cyclic data records a serialization diagnostic.

Keep reviewed decisions and limitations in dated Markdown under `evals/evidence/`.
Keep raw artifacts ignored and preserve the recorded distinction between older experiments and current behavior.
Follow [.claude/rules/evals.md](.claude/rules/evals.md) when changing evaluation cases or model-facing behavior.

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
