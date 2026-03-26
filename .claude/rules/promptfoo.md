---
paths:
  - "evals/promptfoo/**"
---

# Prompt evaluation (promptfoo)

## Directory roles

| Directory | Purpose | Determinism | Cost |
|---|---|---|---|
| `evals/promptfoo/` | Prompt experimentation: system prompt variants, tool descriptions, suffix fragments, backend comparison | Non-deterministic; use `--repeat N` to measure stability. | API calls x N x providers. |
| `evals/promptfoo/outputs/` | Transient raw output (gitignored). Working files for in-progress analysis. | N/A | N/A |
| `evals/promptfoo/evidence/` | Committed eval evidence. Decision rationale for adopted/rejected variants. | N/A | N/A |

## Prompt changes workflow

1. Edit eval-layer prompts or provider config in `evals/promptfoo/`.
2. Run eval: `eval $PFOO eval --filter-providers "<provider>" --no-cache`.
3. Compare against previous eval in the promptfoo DB or JSON output.
4. Once validated, port the change to the corresponding production code (see mapping below).
5. Run `uv run pytest -q` to confirm no regressions.

**`--filter-providers` caveat**: The flag takes a regex pattern, not an exact label. A pattern like `"gpt-5.4-mini"` matches every provider whose label contains that substring (e.g. both `openai-responses` and `codex` labels). Use an anchored or label-specific pattern (e.g. `"^gpt-5.4-mini"`, `"codex:"`) to target a single provider.

## Prompt variant cleanup (deletion timing and criteria)

- Delete rejected prompt variants immediately when any of the following is true:
  - Deterministic regression (`N/N` failures across repeats, e.g. `--repeat 3`)
  - Clear degradation versus baseline on primary metrics
  - Temporary/scratch variants created for quick checks
- Keep a variant only if it has clear re-test value (e.g. flaky `1/N` behavior or meaningful metric trade-offs).
- Retention for kept variants is limited to `min(7 days, 2 experiment cycles)`.
  - If not re-evaluated within this window, delete it.
- Run cleanup at three checkpoints:
  1. Right after each experiment run
  2. Before opening a PR
  3. Before merge (final sweep)
- Before deleting, record a short rejection reason in the corresponding `evals/promptfoo/evidence/` file; do not keep dead prompt files just for history.

## Backend prerequisites

| Backend | Requirement |
|---|---|
| `openai-responses` | `OPENAI_API_KEY` environment variable. |
| `codex` | Pre-authenticated `codex` CLI (`codex login`) or `CODEX_API_KEY` environment variable. |
| `claude-code-cli` | Pre-authenticated `claude` CLI (`claude login`) or `ANTHROPIC_API_KEY` environment variable. |
| `claude-code-sdk` | `ANTHROPIC_API_KEY` environment variable. |

Evals that include a backend without its prerequisite will run but produce errors for that backend's test cases. Use `--filter-providers` to exclude unavailable backends.

## Eval-to-production mapping

Eval prompts are experimental copies of production code. Keep them in sync; divergence means eval results do not predict production behavior.

| Eval file | Production counterpart |
|---|---|
| `evals/promptfoo/prompts/eval_default.txt` | `configuration.py:DEFAULT_STEP_SYSTEM_PROMPT_TEMPLATE` |
| `evals/promptfoo/prompts/eval_coding_agent.txt` | No single counterpart; coding agent backends receive this prompt via `system_prompt_file` config. |
| Suffix variants in `evals/promptfoo/provider.py` (`_build_suffix_*`) | `step_contract.py:build_step_system_prompt_suffix_fragment` |
| Tool presets in `evals/promptfoo/provider.py` (`_build_tool_preset`) | `tools/registry.py` + `tools/assignment.py` |

## Eval evidence

### When to save evidence

| Save | Do not save |
|---|---|
| Eval that decided adoption or rejection of a prompt/suffix/tool variant | In-progress trial runs during development |
| Regression baseline update | Single-test filter runs |
| Eval backing a change merged via PR | Transient output already in `outputs/` |

### File path convention

```
evals/promptfoo/evidence/{YYYY-MM-DD}-{experiment-slug}.md
```

Examples: `2026-03-20-suffix-ab.md`, `2026-03-25-regression-v2.md`.

### File format

```markdown
---
eval_id: <promptfoo eval ID>
config: <YAML config file used>
date: YYYY-MM-DD
decision: <one-line summary of what was adopted/rejected>
---

## Providers tested
- <provider label> (<variants if A/B>)

## Results summary
| Variant | Pass | Fail | Error | Score | Latency |
|---------|------|------|-------|-------|---------|
| ...     |      |      |       |       |         |

## Decision rationale
<Why the chosen variant was adopted.>

## Rejected variants
- <variant>: <short rejection reason>
```

### Rules

- Only Markdown files (`*.md`) are committed under `evidence/`. Raw JSON stays in `outputs/` (gitignored).
- One file per experiment decision. If a follow-up eval revises a prior decision, create a new file; do not overwrite.
- Reference the `eval_id` so the full raw result can be retrieved from the promptfoo local DB (`promptfoo view`) or `-o` export if needed.

## Eval interpretation

- **Exit code 100**: promptfoo returns exit code 100 when any test case fails. This is not a system error; it signals "some assertions did not pass". CI scripts and background runners should treat exit 100 as "check results" rather than "eval crashed".
- **OpenAI 500 errors**: Transient; ignore unless persistent across runs.
- **Codex CLI errors**: Codex backend is unstable; isolate with `--filter-providers`.
- **Codex binding-not-returned**: Codex may return `None` for write bindings that `openai-responses` handles correctly. This is a distinct failure mode from flaky LLM non-determinism; it typically indicates the coding-agent backend did not invoke the assignment tool.
- **Mutation vs filter ambiguity**: Natural language instructions like "remove negative numbers" can be interpreted as either in-place mutation or filter-to-new-list. LLMs inconsistently choose between these, producing flaky failures where the binding value is the original unmodified collection. This pattern recurs across providers and suffix variants.
- **1/N failures (flaky)**: Inherent LLM non-determinism. Use `--repeat 3` to distinguish from deterministic regressions.
- **Deterministic failures** (N/N across repeats): Require prompt or code fix before merging.

## Baseline workflow

Run a full baseline when: (a) setting up the eval environment for the first time, (b) after a major production code change, or (c) when prior baselines are stale (> 2 weeks or across model version changes).

1. Verify prerequisites for each backend (see Backend prerequisites above).
2. Run all applicable configs in parallel with `--no-cache` and `-o outputs/baseline-{slug}.json`:
   - `promptfooconfig.yaml` — regression across all available backends.
   - `promptfooconfig-prompt-ab.yaml` — prompt/tool variant comparison.
   - `promptfooconfig-suffix-ab.yaml` — suffix fragment comparison.
   - `promptfooconfig-agents.yaml` — coding-agent backends (requires `codex` and `claude` CLIs).
3. For backends without prerequisites, either skip the config or use `--filter-providers` to exclude unavailable providers.
4. Create one evidence file per config under `evals/promptfoo/evidence/` with the `baseline-` slug prefix.

## Commands

See `CONTRIBUTING.md` "Prompt evaluation with promptfoo" for eval commands, config files, directory layout, and flags.
