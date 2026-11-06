# Composing Agent Parts

Read [Agent Parts and Harness](index.md) for Runner setup and [runtime obligations](runtime.md) for host services.
This guide combines reusable Parts through ordinary Python, with callable [reference applications](https://github.com/kurusugawa-computer/nighthawk-python/tree/main/examples) in the checkout.
Examples establish composition behavior; claims about judgment quality, cost, or improvement need application evaluations.

Supply a configured `runner` for the snippets below; `deep_runner` denotes a Runner with enough allocation for the learning workflow.
Imports from `examples` require the repository checkout and are not part of the installed distribution.
Batch and continuation entry points are asynchronous: await them on the caller's loop, or use `asyncio.run(...)` at a synchronous application's outer boundary.

## Retain values and read bounded portions

```python
from nighthawk.parts.values import read_text_window, search_text, select_sequence_window

samples = tuple(range(10000)) + (-7,)
first = select_sequence_window(samples)
last = select_sequence_window(samples, starting_index=10000)
assert last.item_tuple == (-7,)

text = "x" * 10000 + "marker"
preview = read_text_window(text)
hit = search_text(text, "marker", starting_character=9999)
assert hit.match_tuple[0].starting_character == 10000
```

Offsets are Unicode character offsets.
Search is case-sensitive and includes overlapping matches; its scan limit includes lookahead, and empty intermediate pages still advance.
Sequence pages retain references to members, including their shared mutable contents.
Item counts do not bound the rendered size of arbitrary members; Core's configured prompt limits remain the final rendering bounds.
Reference passing does not bound process memory or immutable ledger snapshot work.

Coding results retain optional `standard_output_text`, `standard_error_text`, and `body_text` alongside their previews.
Research fetches can retain `full_text`; `truncated` describes preview omission independently and is `None` when the fetch client does not report it.
Custom fetch and request backends implement the admission callbacks described in the [Web backend contracts](../api.md#nighthawk.parts.web.WebFetchClient).
No retention helper implicitly saves data.

## Discover procedures before exposing actions

`nighthawk.parts.procedures` provides `select_procedure`, `selected_procedure`, and `materialize_procedure` for use from installed applications.
[procedure_discovery.py](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/procedure_discovery.py) combines these parts for its numeric domain and queries a caller-owned `ProcedureCatalog` under a scope containing only its query capability.
The bounded `ProcedureSelection` supplies an identity and reason; deterministic Python resolves the retained procedure and narrows the actual enclosing grant to its capabilities and named dependencies.
Resolution never executes source or grants authority.
Other applications may pass selected callables as read bindings so Core renders their signatures and usage hints when a later judgment needs them.
Generated identities remain distinct from names, and a generated reference cannot be invoked after its originating run.
`materialize_procedure` stores a shared `RetainedFunction` in the caller's catalog.
Selecting it in a later run materializes its exact source under the new grant and yields a reference local to that run.
Neither the catalog description nor source retention carries authority or approval.

```python
from examples.procedure_discovery import DiscoveryInput, run_procedure_discovery
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureDescriptor, build_procedure_discovery


def square(value: int) -> int:
    return value * value


catalog = ProcedureCatalog()
catalog.register(
    ProcedureDescriptor(
        procedure_id="square",
        name="square",
        purpose="Square an integer",
        source_provenance_class="ratified",
    ),
    square,
)
# Supply a Runner configured with your application's fixed StepExecutor and services.
result = run_procedure_discovery(
    DiscoveryInput(objective="Square integers", value_tuple=(3, 7)),
    catalog=catalog,
    runner=runner,
    run_entry_grant=build_procedure_discovery(catalog=catalog),
)
assert result.result_tuple == (9, 49)
```

## Share generation between conversation and batch work

`retained_text_workflow.generate_normalizer(runner=runner)` calls `generation.propose_function`, the same source judgment used by standard conversation.
The application supplies exact text-normalization requirements and example data, then explicitly retains the returned definition-only proposal.
`run_normalization_trial(function, labels, runner=runner)` invokes it in a fresh run and compares the observed result with the application's Python baseline.
Repeated trials reuse the retained source without additional model calls.
Incorrect results remain unsuccessful evaluations even when their generated execution completed successfully.

```python
from examples.retained_text_workflow import generate_normalizer, run_normalization_trial

function = await generate_normalizer(runner=runner)
first = run_normalization_trial(function, (" Alpha ", "alpha", "BETA", ""), runner=runner)
second = run_normalization_trial(function, (" X ", "x", "Y"), runner=runner)
assert first.matches_criterion and second.matches_criterion
assert first.run_id != second.run_id
```

Pass an ordinary async `generate_function(request, context)` to replace only source generation.
The same function can be supplied to `conversation.converse` or the Session `build_conversation_composition` builder.
No common part base class or mandatory catalog is involved.

## Compose cooperative asynchronous work

```python
from examples.async_object_workflow import SampleBatch, WorkflowInput, run_async_object_workflow
from nighthawk.harness.grants import Grant
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureSelection

report = await run_async_object_workflow(
    WorkflowInput(
        batch_tuple=(
            SampleBatch(batch_id="first", sample_tuple=(-2, 0, 3)),
            SampleBatch(batch_id="second", sample_tuple=(4, -5)),
        )
    ),
    catalog=ProcedureCatalog(),
    selection=ProcedureSelection(procedure_id=None, reason="Propose a procedure"),
    runner=runner,
    run_entry_grant=Grant.EMPTY,
)
```

[async_object_workflow.py](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/async_object_workflow.py) uses actual asynchronous Natural execution, async generated references, and `asyncio.TaskGroup`.
Branches receive their `SampleBatch` and deliberately shared `AnalysisConfiguration` references without serialization.
Generated procedures return `(original_index, value)` pairs below the strict upper-bound criterion.
TaskGroup joins cooperative descendants, but cancellation does not prove forced cessation of uncooperative code.

## Call learning explicitly

```python
from examples.learning_loop import LearningLoopInput, build_learning_validation, run_learning_loop

result = run_learning_loop(
    LearningLoopInput(sample_tuple=(-2, 0, 3)),
    runner=deep_runner,
    run_entry_grant=build_learning_validation(),
)
assert result.trial_tuple[0].selected_value_tuple == (-2, 0)
assert result.trial_tuple[1].selected_value_tuple == (-2,)
```

[learning_loop.py](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/learning_loop.py) starts with a controlled faulty procedure, records observed and expected values through a typed capability, explicitly calls `derive_learning`, and passes its lessons to the next proposal judgment.
The learning call is visible in an ordinary Python loop.
There is no implicit history, automatic learning hook, mandatory state root, or extra model call guaranteed after allocation exhaustion.
Callers can use their own state types and adapt optional `Lesson`, `ImprovementCandidate`, and `LearningResult` values.
Bootstrap outputs have finite collection, narrative, citation, and candidate-source bounds.
Citation checks establish membership in supplied evidence or current lessons, not the truth of the conclusion.
Observed correction demonstrates the data path, not that the lesson was the sole cause of improvement.
Learning results and successful trials do not approve generated source for future trusted use.

`analyze_run(handle.ledger)` reports recorded ancestry, own/subtree usage, failures, active nodes, and indeterminate unmatched perimeter attempts.
`project_evidence(ledger.entries, query, run_id=ledger.run_id, bounds=bounds)` captures a finite evidence page with a snapshot horizon and advancing continuation.
Its excerpts omit host usage and allocation fields and may omit content permanently.

## Save selected state and start a fresh run

[continued_object_workflow.py](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/continued_object_workflow.py) supplies `run_finite_batch`, a versioned `WorkflowHandoff`, its JSON encoder/decoder, and `continue_workflow`.
Await `run_finite_batch` to complete a finite number of batches, join their branches, and select completed findings, pending inputs, ordinary failures, lessons, and supporting evidence for saving.
The handoff retains the strict comparison criterion and configuration label.
`await continue_workflow(handoff, runner=runner, run_entry_grant=grant)` uses that retained criterion; it cannot silently change the task while carrying earlier findings forward.

```python
from examples.continued_object_workflow import decode_workflow_handoff, encode_workflow_handoff
from nighthawk.adapters.artifacts import DirectoryArtifactStore
from nighthawk.parts.artifacts import build_artifact_storage

store = DirectoryArtifactStore(destination, maximum_character_count=1000000)
storage_grant = build_artifact_storage(
    store=store,
    maximum_payload_character_count=100000,
    allow_write=True,
)
# Compose storage_grant into the run-entry grant when the run itself saves.
# Caller-side explicit persistence can use the backend directly:
descriptor = store.put_text((encode_workflow_handoff(handoff),), media_type="application/json")
reopened = DirectoryArtifactStore(destination)
restored = decode_workflow_handoff(
    reopened.load_text(descriptor, maximum_character_count=100000),
    expected_task_id=handoff.task_id,
)
```

`InMemoryArtifactStore` offers the same saved-text interface without durability.
Descriptors locate saved data, not live references or authority.
Directory publication is atomic for one artifact and prior saves survive ordinary failures.
Completed files are readable after process restart on the same available filesystem; there is no power-loss durability promise or transaction joining data and audit commits.
Capacity admission is serialized within an adapter; callers coordinate independent writers to one directory.
A failed attempt audit prevents a durable write; a failed completion audit retains the host's successful return/live-observation semantics where applicable.
Oversized complete loads fail visibly instead of silently truncating a handoff.

Decoding constructs declared domain values and verifies version, task identity, and disjoint scheduling identities.
It never restores generated authority, allocations, active coroutines, implementation classes named by data, or arbitrary alias graphs.
Continuation starts a new run with explicit grants and allocation, schedules pending batches only, and carries prior successes and failures forward.
Retrying an ordinary failed batch requires explicit caller rescheduling.
Abrupt termination can recover only previously completed saves; no uncertain external effect is automatically replayed.
`format_candidate_review` creates inert review text with exact source, dependencies, effects, selected evidence, and observed checks; it can be saved using the same storage capability without ratifying or executing it.

## Verify and evaluate

Run the reference applications through their [acceptance tests](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/README.md#verification).
Provider acceptance and its limits are described in the [contributor guide](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/CONTRIBUTING.md#integration-tests).

[evaluate_object_workflow.py](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/evaluate_object_workflow.py) compares every expected input and every reported result, rejecting duplicate input identities and exposing missing, incorrect, or duplicate findings.
`evaluate_workflow(run_input, report, ledger_tuple=(first_run.ledger, later_run.ledger))` distinguishes citation coverage from evidence coverage supported by exact invocation inputs and committed output.
Unknown and unrelated citations remain visible; a result's numeric correctness does not imply evidence coverage, execution success, or observed execution completion.
The evaluator reports completion, success, and completeness of live effect observations separately. Live observations do not certify durable audit persistence.
`resolve_evidence(ledger, reference)` is the shared library lookup for exact event fields and recorded value paths; existence alone does not establish relevance or truth.
`await evaluate_fixed_tasks(runner=runner)` compares the same fixed inputs with the ordinary `python_workflow` baseline and reports elapsed seconds, observed requests/tokens, usage basis, and evidence coverage.
Unavailable measurements remain `None`; scripted success or accounting is not evidence of model quality or cost improvement.

## Supply durable perimeter auditing

Applications permitting perimeter effects configure `Runner.perimeter_audit_store` under the [durable audit obligations](runtime.md#durable-perimeter-audit).
The application-owned `SqlitePerimeterAuditStore` supplies a local reference implementation:

```python
from dataclasses import replace
from pathlib import Path
from examples.durable_audit import SqlitePerimeterAuditStore

store = SqlitePerimeterAuditStore(Path("perimeter-audit.sqlite"))
runner = replace(runner, perimeter_audit_store=store)
# The application also selects the run's grant and permitted effect classes.
# After a run or a process restart:
records = store.read(run_id)
indeterminate = store.unmatched_attempts(run_id)
```

This audit backend is separate from domain artifact storage.
