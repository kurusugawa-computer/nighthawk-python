# Ideal State implementation audit

Date: 2026-09-11.

This audit maps the required contracts in [Natural execution specification](../../../docs/specification.md) to the implementation and verification evidence.
It covers the stated ordinary Python and in-process execution boundaries.
The [inspection assessment](20260911-inspection-assessment.md) selects the existing two-tool surface with Python inspection helpers and reports probabilistic failures separately from runtime correctness.

## Contract coverage

| Contract area | Implementation | Principal verification |
| --- | --- | --- |
| Functions, `run`/`scope`, task injection, composition vocabulary | `composition.py`, `runtime/scoping.py`, `runtime/step_request.py`, `runtime/context.py` | `tests/public/test_composition.py`, `tests/public/test_public_api.py`, `tests/execution/test_context_builder.py` |
| One executor request, resolved live candidates, mandatory finalization | `runtime/step_request.py`, `runtime/runner.py`, `runtime/model_candidate.py` | `tests/execution/test_executor_contract.py`, `tests/governance/test_step_results.py` |
| Lexical precedence, unbound names, live globals/cells, explicit global/nonlocal outputs | `natural/decorator.py`, `natural/transform.py`, `runtime/environment.py` | `tests/natural/test_lexical_contract.py`, `tests/natural/test_decorator_globals.py`, `tests/execution/test_environment.py`, `tests/execution/test_output_contracts.py` |
| Within-operation lambda/generator lookup, parent snapshots, shared values, joined children | `runtime/environment.py`, `runtime/expression.py`, `runtime/step_context.py` | `tests/execution/test_environment.py`, `tests/execution/test_runtime.py`, `tests/integration/test_natural_composition.py` |
| Source helper hygiene and application `inspect` preservation | `natural/_names.py`, `natural/decorator.py`, `runtime/environment.py` | `tests/natural/test_runtime_name_hygiene.py`, `tests/execution/test_discovery.py` |
| Annotation selection, missing versus explicit contracts, initialized output validation | `runtime/annotations.py`, `natural/transform.py`, `runtime/runner.py` | `tests/execution/test_output_contracts.py`, `tests/natural/test_variadic_contracts.py`, `tests/natural/test_deferred_annotations.py` |
| Assignment grammar, admission before effects, right-hand side once, live target traversal | `runtime/assignment.py`, `identifier_path.py` | `tests/tools/test_assignment_contract.py`, `tests/tools/test_assignment_async.py` |
| Ordinary Pydantic fields, assignment validators, constraints, frozen restrictions, partial effects | `runtime/assignment.py` | `tests/tools/test_assignment_contract.py` |
| Replaceable context, budgeted priorities, provenance, collisions, multimodal content | `runtime/context.py`, `runtime/prompt.py`, `runtime/description.py` | `tests/execution/test_context_builder.py`, `tests/execution/test_variables_prompt.py`, `tests/execution/test_discovery.py`, `tests/integration/test_llm_integration.py` |
| Shared non-executing metadata, opaque descriptions, progressing discovery and composed continuations | `runtime/description.py`, `runtime/discovery.py`, `runtime/inspection.py`, `json_renderer.py` | `tests/execution/test_description.py`, `tests/execution/test_discovery.py`, `tests/execution/test_discovery_composition.py` |
| Common operation identities, awaited inspection, per-step ordering, sticky failures | `runtime/operations.py`, `tools/_admission.py`, `tools/_ordering.py`, `runtime/step_executor.py` | `tests/governance/test_operations.py`, `tests/governance/test_native_admission.py`, `tests/governance/test_model_tool_order.py`, `tests/governance/test_model_operation_failures.py` |
| Exception origin, child failure propagation, Pydantic AI retry/approval/deferral meaning | `runtime/expression.py`, `runtime/operations.py`, `tools/execution.py`, `backends/tool_bridge.py` | `tests/tools/test_assignment_contract.py`, `tests/governance/test_native_admission.py`, `tests/governance/test_operations.py`, `tests/backends/` |
| Return once, independent output/return validation, complete rewrites, discarded Raise outputs | `runtime/model_candidate.py`, `runtime/runner.py`, `oversight.py` | `tests/execution/test_executor_contract.py`, `tests/governance/test_governance.py`, `tests/governance/test_step_completion.py` |
| Correlated started/operation/terminal facts, actual writes, distinct delivery failures | `lifecycle.py`, `runtime/runner.py`, `runtime/notifications.py` | `tests/governance/test_step_ledger.py`, `tests/governance/test_async_lifecycle.py`, `tests/governance/test_step_completion.py` |
| Bounded interruption cleanup, synchronous bridge context, backend callback loop ownership | `runtime/async_bridge.py`, `runtime/notifications.py`, `backends/tool_transport.py` | `tests/governance/test_async_lifecycle.py`, `tests/backends/test_tool_transport.py` |
| Actual Natural judgments, model-selected children, ten inherited counter steps | `tests/integration/test_natural_composition.py` | Six provider integration cases and [composition evidence](20260910-natural-composition.md) |
| Controlled interface comparison and selection | `evals/model_tools/inspection_study.py` | 57 deterministic study cases, 804 model trials, separate guidance and backend comparisons, [assessment and artifacts](20260911-inspection-assessment.md) |
| Current documentation and architecture | `docs/specification.md`, `docs/natural-blocks.md`, `docs/patterns.md`, `docs/coding-agent-backends.md`, `docs/roadmap.md`, `CHANGELOG.md` | Executable document examples, architecture checks, strict MkDocs build |

## Issues found and resolved in the final audit

Variadic argument annotations previously constrained the entire local tuple/dictionary as though it were one element.
They now select collection contracts, retain `Annotated` validators, and support unpacked tuples and typed dictionaries.
Eight regressions exercise assignment and finalization through both synchronous and asynchronous functions.

Interrupted notifications previously had a bounded shielded wait but could wait again without a bound during `asyncio.run` shutdown.
The synchronous bridge now shares the notification deadline with its private loop cleanup.
Four regressions cover operation and terminal notifications, both outside and inside a running caller loop, while preserving the original cancellation.
Closing that private loop does not claim completion of a cancellation-resistant handler.

The first Python 3.14 suite exposed 17 metadata-display failures.
Deferred callable annotations now use bounded direct source-file acquisition without calling the annotation evaluator or an application source loader.
Explicitly stored annotation dictionaries remain usable; source spelling and unavailable or modified deferred metadata have stated limitations.
Natural decoration retains deferred evaluators rather than eagerly evaluating return annotations.
Global-output preparation obtains deferred module annotation contracts, while unrelated presentation does not invoke the module evaluator.
Four Python 3.14-specific regressions cover those boundaries.

An additional ordered-batch audit showed that Pydantic AI 2.37 ran the second native argument validator before the first body, including before the first call's rejection.
The Nighthawk compatibility dispatch now places each validation at the corresponding execution barrier.
It delegates schemas, validators, retries, deferrals, and output settlement to Pydantic AI.
The supported dependency range is constrained to 2.37 and requires a compatibility review before widening.
Tests cover all three completion strategies, terminal validator failure after an earlier effect, exhaustive settlement after invalid output JSON, and unchanged ordinary Pydantic AI execution outside the Nighthawk context.
The exhaustive transport's early call events expose `args_valid=None`; operation records contain the actual admission result.

## Verification record

The final deterministic suites passed on both interpreters:

- CPython 3.13: 991 passed, 35 skipped, one upstream deprecation warning.
- CPython 3.14.7: 995 passed, 31 skipped.

The 31 provider tests are opt-in; the additional four skips on 3.13 are the deferred-annotation cases specific to 3.14.
Python 3.14 and its locked dependencies were installed in independent `/tmp` directories, leaving the project's existing `.venv` intact.
The lock change only narrows the Pydantic AI requirement; package versions did not change.
Ruff and Pyright passed, and the documentation built with strict MkDocs validation.

The complete provider-enabled 3.13 run passed 1,010 tests and failed one Codex arithmetic-skill case.
That failure was a nested automatic-approval rejection rather than an arithmetic or runtime exception: the coding agent returned an approval question.
The synthetic test prompt was clarified to carry the user's existing authorization for `calc(1, 2)` and to state the function's pure arithmetic body.
The unchanged result oracle then passed on the targeted rerun.
The other 30 provider integration cases passed in the complete run, including all six actual Natural composition cases.
Earlier separate Codex, Claude CLI, and Claude SDK checks also passed all 12 coding-backend integration cases.

After the Python 3.14 annotation and presentation fixes, all 13 OpenAI contract integration tests passed on Python 3.14 in 84.10 seconds.
After the native-ordering changes, all 13 OpenAI contract integration tests passed again on Python 3.13 in 91.77 seconds.
These are recorded checks across identified revisions, not a claim that every provider suite was rerun after every subsequent documentation or compatibility edit.

The [inspection protocol](20260911-inspection-study-protocol.md) and [assessment](20260911-inspection-assessment.md) report probabilistic studies separately, including failures and source snapshots.
The semantic failures observed in those studies are retained; successful runtime validation does not establish model reliability.

## Deliberate boundaries

The implementation does not add arbitrary caller-frame capture, deferred generated lambda/generator lifetime guarantees, children that outlive their parent, rollback, durable suspension/replay, or an automatic background scheduler.
Untrusted Natural source is not a sandboxed input format.
Attribute assignment remains restricted to the documented Pydantic route; arbitrary mutation belongs in ordinary helpers.
Tiny discovery budgets may fit only a minimal recovery result, and unsupported metadata does not promise a continuation.
Host storage remains an attempted delivery, with deduplication and retries owned by the host.
An asynchronous host owns the later shutdown of its own event loop and any cancellation-resistant tasks it retains.

Dedicated-tool retention and helper/continuation exposure are settled by the recorded inspection selection.
Statement/iteration extensions and completion redesign remain unratified optional work rather than missing acceptance requirements.

## Ratified decision traceability

### Documentation consolidation verification

On 2026-09-12, the target contracts were integrated into the existing specification, philosophy, usage guides, composition patterns, verification guidance, and roadmap.
The retired target page was removed from navigation and its inbound links were redirected to the owning contracts.
The decision index below retains approval traceability without retaining a second normative document.
No library implementation changed in this consolidation.

Validation on CPython 3.13:

- `uv run --no-sync pytest -q -x`: 991 passed, 35 skipped, one upstream event-loop deprecation warning.
- `uv run --no-sync pytest -q tests/docs`: 27 passed.
- OpenAI-enabled `tests/integration/test_natural_composition.py`: all six cases passed across `gpt-5.6-luna` and `gpt-5.6-sol` in 266.65 seconds, using synthetic test inputs.
- The three composition examples were extracted from the edited Markdown into a source-available Python module and executed with a custom executor through actual Natural calls, common operation entry points, and mandatory finalization.
  Assertions verified Python iteration results, inherited review policy, and all ten parent counter values and child results.
- Strict MkDocs build passed; the rendered contract anchors and removal of the retired page from navigation were checked.
- All 204 local Markdown links across the documentation and model-tool evidence resolved, including section anchors.
- Ruff check and formatting checks passed for the changed documentation architecture test; `git diff --check` passed.

The documentation test suite now guards the retired filename alongside earlier removed pages.
The provider tests measure the stated tasks; these successful runs do not erase probabilistic failures reported by the separate inspection study.

### Decision index

The target document was integrated into the canonical documentation on 2026-09-12.
This historical index retains the ratified decision Ids and routes them to their current contract owners; it is not a second specification.
Historical section numbers have been replaced with links to the integrated contracts.
Inspection exposure is settled by the assessment above; statement/iteration extensions and completion redesign remain optional, unratified work.

| Decision Id | Direction and current contract owner |
| --- | --- |
| P-SCOPE-001A | Distinct environments and lexical priority: [Specification](../../../docs/specification.md#81-state-layers-python-locals-and-step-locals). |
| P-CONTEXT-001A | Accessibility versus presentation: [Specification](../../../docs/specification.md#82-prompt-context). |
| P-WRITE-001 / Q-WRITE-001 | Static output declarations: [Specification](../../../docs/specification.md#7-natural-dsl-bindings). |
| P-HOST-001 | Host-owned operations and ledger: [Specification](../../../docs/specification.md#104-terminal-delivery-and-host-ledgers). |
| P-CHECK-001A | Explicit annotations before initial-value inference: [Specification](../../../docs/specification.md#7-natural-dsl-bindings). |
| P-RUNTIME-001A | Shared operation engine and direct Pydantic integration: [Specification](../../../docs/specification.md#14-step-executor) and [Operation pipeline](../../../docs/specification.md#103-operation-inspection). |
| Q-TRUST-001 / Q-RESUME-001 | Trusted execution; in-process approval without durable suspension: [Specification](../../../docs/specification.md#2-non-goals). |
| P-DESIGN-001 / Q-SIMPLICITY-001 | Target-first changes without compatibility requirements: [Specification](../../../docs/specification.md#0-document-scope-and-alignment-policy). |
| P-TOOLS-001 / P-TOOLS-002 / P-TOOLS-003 / P-TOOLS-004 | Comparison baseline, per-reference selection, progressing retrieval, and feedback: [Specification](../../../docs/specification.md#825-value-inspection-and-name-discovery). |
| P-TOOLS-EVAL-001 | Controlled comparison and semantic oracles: [Specification](../../../docs/specification.md#825-value-inspection-and-name-discovery). |
| P-REVIEW-001A / P-BOUNDARY-001 | Practical judgment example, inheritance motivation, and nested proof: [Composition patterns](../../../docs/patterns.md#natural-selects-follow-up-assessments). |
| P-REVIEW-002 | Ordinary functions first; optional host integration: [Specification](../../../docs/specification.md#14-step-executor). |
| P-REVIEW-003 / P-DOCUMENT-001 / P-AUDIT-006 | Single contract ownership and separate historical evidence: the canonical ownership rules in the documentation governance file. |
| P-REVIEW-004 | Replacement/Raise semantics and discovery lifecycle: [Specification](../../../docs/specification.md#103-operation-inspection) and [Host contracts](../../../docs/specification.md#104-terminal-delivery-and-host-ledgers). |
| P-REVIEW-005 / P-TOOLS-005 | Upfront collisions and usable references: [Specification](../../../docs/specification.md#82-prompt-context). |
| P-INSPECT-001 / P-INSPECT-002 | Optional dedicated inspection tool and controlled helper comparison: [Specification](../../../docs/specification.md#825-value-inspection-and-name-discovery). |
| P-INSPECT-003 / P-INSPECT-004 | Value inspection separate from name discovery; preserve application names: [Specification](../../../docs/specification.md#825-value-inspection-and-name-discovery). |
| P-FOCUS-001 / Q-FOCUS-001 | General-purpose Natural execution: [Specification](../../../docs/specification.md#1-goals). |
| P-DISCOVERY-001 | Required discovery, best-effort descriptions: [Specification](../../../docs/specification.md#825-value-inspection-and-name-discovery). |
| P-ORDER-001 | Per-step serialization: [Specification](../../../docs/specification.md#103-operation-inspection). |
| P-ASSIGN-001 / P-AUDIT-001 / P-IDEAL-001 | Assignment order and validation boundaries: [Specification](../../../docs/specification.md#7-natural-dsl-bindings); attribute scope narrowed by P-VERIFY-001. |
| P-REWRITE-001 | Return replacement through the outcome: [Host contracts](../../../docs/specification.md#102-host-commit-boundary). |
| P-AUDIT-002 | Execution and writeback boundaries with contextual inputs: [Specification](../../../docs/specification.md#1-goals). |
| P-AUDIT-003 / P-IDEAL-002 | Immediate evaluation required; deferred generated values excluded: [Specification](../../../docs/specification.md#813-parent-lifetime-and-expression-lifetime). |
| P-AUDIT-004 / P-IDEAL-004 | Required semantics and optional host detail: [Specification](../../../docs/specification.md#825-value-inspection-and-name-discovery) and [Host contracts](../../../docs/specification.md#104-terminal-delivery-and-host-ledgers). |
| P-AUDIT-005 | Shared custom-executor operation entry points: [Specification](../../../docs/specification.md#144-nested-execution-acceptance-example). |
| P-AUDIT-007 | Undeclared-output rejection and initialized-output validation: [Specification](../../../docs/specification.md#7-natural-dsl-bindings); roadmap alignment: [Specification](../../../docs/specification.md#0-document-scope-and-alignment-policy). |
| P-IDEAL-003 | Trusted sources and external input as data: [Specification](../../../docs/specification.md#2-non-goals). |
| P-IDEAL-005 / P-VERIFY-006 | Compress repeated contracts and management detail; retain acceptance examples and approval traceability. |
| P-IDEAL-006 | Semantic reliability before efficiency; simplicity breaks unresolved ties: [Evaluation criteria](../../../docs/verification.md#interface-evaluation-criteria). |
| P-VERIFY-001 | Ordinary Pydantic fields with assignment validation enabled; typed local outputs remain validated: [Specification](../../../docs/specification.md#7-natural-dsl-bindings). |
| P-VERIFY-002 | Explicit lexical support matrix and authorized preliminary investigation: [Specification](../../../docs/specification.md#812-supported-python-source-forms). |
| P-VERIFY-003 | Parent inheritance limited to joined child calls: [Specification](../../../docs/specification.md#813-parent-lifetime-and-expression-lifetime). |
| P-VERIFY-004 | Distinguish operation-delivery and terminal-delivery failures: [Host contracts](../../../docs/specification.md#1041-delivery-failure-cases). |
| P-VERIFY-005 | Practical motivation and cost of inherited task policy: [Composition patterns](../../../docs/patterns.md#natural-selects-follow-up-assessments). |
| P-STATE-001 / Q-STATE-001 | Bounded annotation acquisition, separate from lexical access: [Specification](../../../docs/specification.md#7-natural-dsl-bindings). |
| P-STATE-001A / P-STATE-006 | Shared presentation metadata, named contracts, and distinct annotation failure rules: [Specification](../../../docs/specification.md#7-natural-dsl-bindings) and [Inspection and discovery](../../../docs/specification.md#825-value-inspection-and-name-discovery). |
| P-STATE-002 / Q-STATE-002 / P-STATE-002A | Ordinary helper and child exceptions propagate; domain observations and explicit Pydantic AI control signals retain their roles: [Specification](../../../docs/specification.md#103-operation-inspection). |
| P-STATE-003 | Executable target example of judgment-selected children inheriting review policy: [Composition patterns](../../../docs/patterns.md#natural-selects-follow-up-assessments). |
| P-STATE-004 | Centralize historical evidence and comparison status; preserve acceptance and host failure tables. |
| P-STATE-005 | Explain contextual input dependencies alongside explicit execution and writeback boundaries in [Philosophy](../../../docs/philosophy.md#nighthawk). |
