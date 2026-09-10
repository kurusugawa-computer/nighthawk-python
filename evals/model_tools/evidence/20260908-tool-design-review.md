# Initial model-facing tool review, 2026-09-08

Historical design review, consolidated from the Ideal State on 2026-09-10.
References to "current" tools below describe the code inspected at review time.
This record preserves the initial rationale, not the current target contract.
P-TOOLS-001 through P-TOOLS-004 and P-TOOLS-EVAL-001 were ratified on 2026-09-09.
The later inspection-helper decisions make dedicated tool exposure provisional;
see the [Ideal State](../../../docs/ideal-state.md).

## Evidence and limits

This review inspected the current tool schemas, system prompt, outcome guidance,
target environment specification, and recorded promptfoo evaluations. Local
in-memory probes checked existing expression and assignment behavior. The
target environment selectors and `nh_inspect` were not implemented at review
time, so the initial review included no live model comparison. "Decision burden"
here describes observable choices and failure opportunities, not a measured
psychological quantity or an inferred model reasoning trace.

The current schemas have one required argument for `nh_eval` (`expression`) and
two for `nh_assign` (`target_path`, `expression`). A local probe confirmed that
`nh_assign("result", "search_documents(question)")` computes and stores the
result in one runtime operation with one callable invocation. Another confirmed
that `nh_eval("items.sort()")` returns `None` while successfully mutating the
list. Tool-return values alone are therefore not a complete state-change report.

The recorded April prompt evaluation in
`evals/promptfoo/evidence/2026-04-27-runtime-prompt-optimization.md`
reports 49/52 passes for an initial small-model prompt, 51/52 after a mutation
hint, and 52/52 after also adding loop-control examples. It also records a
regression when shortening outcome guidance that appeared to duplicate schemas.
These are historical runs, not a new controlled experiment or proof that three
tools outperform two. They support retaining short behavioral distinctions and
testing reductions in guidance rather than equating brevity with usability.

Anthropic's tool-design guidance recommends distinct purposes, useful responses,
and evaluation of errors, redundant calls, tokens, and latency alongside task
success. Its examples also emphasize discoverability and meaningful retrieval.
Those principles support this review method but do not establish an optimal
Nighthawk tool count. [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

## Findings at the initial review

| Finding Id | Finding | Evidence and consequence |
| --- | --- | --- |
| F-TOOLS-001 | Three tool names are not themselves an excessive interface. | Computing/acting, assigning a retained value, and discovering state are recognizable purposes. Merging them can move complexity into optional arguments rather than remove it. This is a design judgment, not a measured performance result. |
| F-TOOLS-002 | A whole-expression environment selector burdens composition. | Under the specified task-only semantics, `search_documents(question)` cannot read a Python-local `question`, even when it correctly selects an injected `search_documents`. A local semantic witness reproduced that omission using separate mappings; it did not exercise an implemented target selector. Effective lookup works without selection when names do not collide. |
| F-TOOLS-003 | Inspection lacks a complete retrieval contract. | A bounded `nh_inspect(name=None, environment=...)` has no specified continuation, search, or widening behavior. Repeating it is not guaranteed to expose previously omitted names or fields. This is a specification gap, not an observed runtime failure. |
| F-TOOLS-004 | Assignment documentation understates its supported work. | Current tool descriptions say "write binding", but the target also needs temporary parent state such as `i`, and supports dotted mutation. Without explicit guidance a model can use an unnecessary eval-then-copy sequence or fail to materialize the parent's counter. |
| F-TOOLS-005 | Model-visible state categories need simplification. | Lexical values, writable output slots, temporary names, inherited names, and shared mutable objects have different update rules. Showing every internal environment detail does not teach the model which action is valid. |
| F-TOOLS-006 | Completion is part of the interface under review. | After successful assignment the model must still choose pass versus return/continue/break/raise. Historical failures include completing a mutation but choosing the wrong outcome. An internally resolved candidate alone does not specify the model's completion protocol. |
| F-TOOLS-007 | Host records should not become routine tool payloads. | Execution Ids, revision counters, failure stages, and commit internals help host tracing but usually do not help the model's next action. Operation effects and concrete correction guidance do. |

The initial review identified exposure of runtime implementation distinctions
as a substantial design concern. The approved separation of environments is
useful internally; it does not require the model to choose among five
environments for every expression or to navigate `StepContext` in ordinary tasks.
The later [GPT-5.6 assessment](20260909-gpt56-assessment.md) revises the empirical
priorities. Current target contracts and approved follow-ups are maintained in
the [Ideal State](../../../docs/ideal-state.md).
