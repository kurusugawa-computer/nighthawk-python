# Ideal State implementation audit

Date: 2026-09-11.

This audit maps the required contracts in [Ideal State](../../../docs/ideal-state.md) to the implementation and verification evidence.
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
