---
paths:
  - "evals/**"
  - "tests/integration/**"
  - "tests/support/model_evaluation.py"
  - "tests/test_model_evaluation.py"
---

# Model evaluations

Use pytest and ordinary Python fixtures to assess production execution and actual Natural function composition.
Keep live calls explicitly selected and outside the default deterministic suite.
Commands, settings, artifacts, and usage limitations are owned by `CONTRIBUTING.md` under "Model evaluations with pytest".

## Cases and assertions

- Check requested values, object identity where relevant, actual calls, effects, and control results with direct Python assertions.
- Use trusted repository-managed programs by default and supply task data through Python bindings. Generating or revising program assets during execution, then executing them, follows the experiment policy and scope defined in `CONTRIBUTING.md` "Programs used in development".
- Generate mutable inputs and effect-recording callables afresh for each trial.
- Specify only ordering and call counts required by the task; permit equivalent model expressions.
- Keep deterministic runtime mechanics with their test owners. Use live evaluations for model behavior and meaningful compositions.
- Preserve failed/interrupted trial evidence and already observed usage. Do not retry until a case passes or reuse cached results as new trials.

## Experiments and decisions

Run relevant cases when changing model-facing behavior and the complete shared suite when establishing a baseline or changing the evaluation boundary.
Use repeated trials for reliability assessments; report individual failures, sample sizes, and variation across cases.
Compare prompt experiments with an unchanged production control and record settings, tools, cases, and assertion differences.
Use public configuration and Python composition; avoid private monkeypatches or copied production prompts in regression cases.
Do not add Core abstractions solely to support an evaluation experiment.

Record a decision under `evals/evidence/` when model-evaluation evidence supports a model-facing change or a documented claim.
Include the source revision or fingerprint, models/settings, versions, case/repeat counts, results, usage, timing, and limitations specific to the run; the record must stand without local artifacts.
Report live regression checks for changes without model-facing effects in the commit message.
Records describe the runs that produced them; do not rewrite them to describe a different runtime.
Delete a record, with links to it, once no decision in effect or documented claim depends on it; Git history retains it.
Synthetic success does not establish application-level judgment quality, security, or superiority to ordinary Python.
Retain experiment implementations only for a current investigation; remove completed treatments, runners, and their dedicated tests after recording the decision and source provenance.

Comments and docstrings follow `.claude/rules/python.md`.
