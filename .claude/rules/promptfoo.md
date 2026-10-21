---
paths:
  - "evals/promptfoo/**"
---

# Prompt evaluations

## Execution boundary

Regression evaluations use the production `StepExecutorConfiguration`, embedded-step execution, prompts, and tools.
Do not copy production prompts into regression assets or replace internal tool/suffix modules.
Keep model/backend comparisons on the shared fixture list in `promptfooconfig.yaml`.

For an active prompt experiment, override the public configuration explicitly and compare it with an unchanged production control.
Record exactly which prompt, settings, tools, fixture contracts, and assertions differ.
Do not add a Core extension solely to host an evaluation experiment.

Natural programs and fixture callables are trusted repository assets.
Keep input values separate from program instructions.
Use the existing `__callable:<name>` fixtures for requested Python operations and check their recorded invocations as well as their outputs.

## Workflow

1. Validate fixture declarations, expected values/effects, and assertion behavior with deterministic tests.
2. Run the relevant model cases without cached results. Use repeated runs when assessing reliability.
3. Inspect wrong values, missing effects, unexpected control results, and infrastructure errors separately.
4. Compare with the control before adopting a production prompt change.
5. Run the deterministic suite after porting an adopted change to production.
6. Save the decision and its limitations in a dated evidence record, then remove expired experiment code.

Run the full shared suite when establishing a baseline or changing the evaluation adapter or production execution behavior.
Select only backends with available credentials and executables.
Provider filters are regular expressions; use labels such as `^openai-responses:` or `^codex:` to make the selection explicit.
Contributor commands and backend prerequisites are maintained in `CONTRIBUTING.md` and the corresponding backend guides.

## Assertions and evidence

Assess the requested result and effects, not merely the output shape or one acceptable control tag.
Distinguish missing bindings from null values, committed outputs from namespace mutations, and returned values from expressions.
A correct fabricated result does not establish that a requested Python function was called.
Avoid prescribing a tool-call sequence unless the task requires that order or those effects.
Retain diagnostic effects and completed-agent usage when execution fails; unknown or unobserved usage is not evidence of free execution.

`outputs/` holds ignored raw JSON, reports, and transient analysis.
`evidence/` holds committed Markdown assessments.
Do not rewrite earlier dated records to describe a different runtime.

A record includes:

- Evaluation Id, date, source revision or artifact fingerprint, configuration, model, and tool versions.
- Case/repeat counts, results, observed token usage, and latency.
- The adoption/removal decision, relevant failure cases, and limitations.
- Differences that prevent a direct comparison with earlier results.

Separate local adapter failures, provider errors, and task failures.
Promptfoo exit code 100 means cases failed or errored; inspect the report.
Repeated failures need investigation; a single successful rerun does not establish reliability.
Do not generalize synthetic fixture compliance into application-level judgment quality or comparative advantage.

## Experiment retention

Delete rejected or temporary variants after recording the reason.
Keep a candidate only while it has a concrete active comparison and a reason to retest.
Reevaluate retained candidates within seven days or two experiment cycles, whichever comes first; otherwise remove them.
Review remaining variants after experiments and before committing.
Past evidence belongs in dated records, not dormant execution paths.
