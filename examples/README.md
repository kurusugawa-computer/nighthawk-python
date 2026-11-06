# Composing Agent Parts

These examples are verified reference applications demonstrating the [principles](../docs/agent/principles.md) by combining reusable Agent Parts through ordinary Python over the shared Harness.
They combine discovery, concurrent work, evaluation, explicit learning, and retained state without requiring one universal Agent loop.
Examples are a form of application and verification, not an additional library execution responsibility or installed package.
Run them with Python 3.13+ and a caller-supplied `nighthawk.agent.runtime.Runner` using the in-process execution and host service contracts in [runtime obligations](../docs/agent/runtime.md).
External confinement and forced termination are optional; configured services must establish the guarantees they report.
The batch and continuation entry points are asynchronous and run on the caller's event loop.
Await them from asynchronous code; a synchronous application can own its outer event loop with `asyncio.run(...)`.

## What the examples demonstrate

| Example | Composition and observable result |
| --- | --- |
| `procedure_discovery.py` | Select a retained procedure, resolve it deterministically, and invoke it within a narrowed grant. |
| `async_object_workflow.py` | Compose typed asynchronous branches while retaining successful findings alongside ordinary failures. |
| `learning_loop.py` | Pass observed failures into an explicit learning judgment and use its output in a subsequent proposal. |
| `retained_text_workflow.py` | Use the conversation's source-generation part without a Session, then evaluate and reuse one retained definition in fresh batch runs. |
| `continued_object_workflow.py` | Save selected state and evidence, then continue pending work in a fresh run. |
| `evaluate_object_workflow.py` | Measure correctness, evidence coverage, unresolved work, and available resource observations on a fixed task set. |
| `durable_audit.py` | Persist perimeter attempts and completions in application-selected SQLite storage and inspect indeterminate attempts after restart. |
| `repl/` | Embed the library in a persistent interactive application with explicit bindings and inspectable execution. |

These examples establish composition and integration behavior.
Claims about improved judgment, lower cost, or reusable capability quality require application evaluation; a passing scripted example is not that evidence.

## Retain values and read bounded portions

```python
from nighthawk.agent.values import read_text_window, search_text, select_sequence_window

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
`build_default_web_fetch_client(retain_full_text=True, maximum_response_character_count=100000)` enables capped incremental full-text retention.
Responses exceeding the configured cap have `full_text=None` and `truncated=True`; an empty complete response can retain `full_text=""`.
No retention helper implicitly saves data.

## Discover procedures before exposing actions

`nighthawk.agent.procedures` provides `select_procedure`, `selected_procedure`, and `materialize_procedure` for use from installed applications.
`procedure_discovery.py` combines these parts for its numeric domain and queries a caller-owned `ProcedureCatalog` under a scope containing only its query capability.
The bounded `ProcedureSelection` supplies an identity and reason; deterministic Python resolves the retained procedure and narrows the actual enclosing grant to its capabilities and named dependencies.
Resolution never executes source or grants authority.
Selection is the only Natural judgment in this example.
Python constructs the fixed typed wrapper and explicitly declares its dependency on the selected callable; this known transformation needs no model call.
The wrapper enters the same generated-code admission path regardless of who constructed its source.
Other applications may pass selected callables as read bindings so Core renders their signatures and usage hints when a later judgment needs them.
Generated identities remain distinct from names, and a generated reference cannot be invoked after its originating run.
`materialize_procedure` stores a shared `RetainedFunction` in the caller's catalog.
Selecting it in a later run materializes its exact source under the new grant and yields a reference local to that run.
Neither the catalog description nor source retention carries authority or approval.

```python
from examples.procedure_discovery import DiscoveryInput, run_procedure_discovery
from nighthawk.agent.catalog import ProcedureCatalog, ProcedureDescriptor, build_procedure_discovery


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

Choose model configurations using evidence from the intended task and its cost and latency constraints.
Each run retains its host mediation executor; permitted model configurations and allocation limits are caller-side choices.

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
This verifies source reuse and explicit assessment; the deterministic normalization task does not establish a quality or cost advantage from generating its implementation.

## Compose cooperative asynchronous work

```python
from examples.async_object_workflow import SampleBatch, WorkflowInput, run_async_object_workflow
from nighthawk.agent import Grant
from nighthawk.agent.catalog import ProcedureCatalog, ProcedureSelection

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

`async_object_workflow.py` uses actual asynchronous Natural execution, async generated references, and `asyncio.TaskGroup`.
Branches receive their `SampleBatch` and deliberately shared `AnalysisConfiguration` references without serialization.
Generated procedures return `(original_index, value)` pairs below the strict upper-bound criterion.
Batch identities are unique. Findings cite a successful invocation only when its validated inputs, definition identity, and recorded result identify one unambiguous observation.
Concurrent identical invocations that cannot be distinguished remain explicitly uncited.
An ordinary arithmetic or value error from analysis becomes `BatchFailure`; independent findings survive in input order.
Host control errors, exhaustion, cancellation, and unexpected errors propagate.
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

`learning_loop.py` starts with a controlled faulty procedure, records observed and expected values through a typed capability, explicitly calls `derive_learning`, and passes its lessons to the next proposal judgment.
The learning call is visible in an ordinary Python loop.
There is no implicit history, automatic learning hook, mandatory state root, or extra model call guaranteed after allocation exhaustion.
Callers can use their own state types and adapt optional `Lesson`, `ImprovementCandidate`, and `LearningResult` values.
Bootstrap outputs have finite collection, narrative, citation, and candidate-source bounds.
Citation checks establish membership in supplied evidence or current lessons, not the truth of the conclusion.
Observed correction demonstrates the data path, not that the lesson was the sole cause of improvement.
Learning results and successful trials do not approve generated source for future trusted use.
Applications may arrange human review or independent automated approval under human-established conditions; these examples provide no automatic ratification pipeline.

`analyze_run(handle.ledger)` reports recorded ancestry, own/subtree usage, failures, active nodes, and indeterminate unmatched perimeter attempts.
`project_history(ledger.entries, query, run_id=ledger.run_id, bounds=bounds)` captures a finite evidence page with a snapshot horizon and advancing continuation.
Its excerpts omit host usage and allocation fields and may omit content permanently.
A live completion does not certify that the durable audit store retained it.

## Save selected state and start a fresh run

`continued_object_workflow.py` supplies `run_finite_batch`, a versioned `WorkflowHandoff`, its JSON encoder/decoder, and `continue_workflow`.
Await `run_finite_batch` to complete a finite number of batches, join their branches, and select completed findings, pending inputs, ordinary failures, lessons, and supporting evidence for saving.
The version 2 handoff also retains the strict comparison criterion and configuration label.
`await continue_workflow(handoff, runner=runner, run_entry_grant=grant)` uses that retained criterion; it cannot silently change the task while carrying earlier findings forward.

```python
from examples.continued_object_workflow import decode_workflow_handoff, encode_workflow_handoff
from nighthawk.agent.tools.artifacts import DirectoryArtifactStore, build_artifact_storage

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
A failed attempt audit prevents a durable write; a failed completion audit retains the host's existing successful return/live-observation semantics where applicable.
Oversized complete loads fail visibly instead of silently truncating a handoff.

Decoding constructs declared domain values and verifies version, task identity, and disjoint scheduling identities.
It never restores generated authority, allocations, active coroutines, implementation classes named by data, or arbitrary alias graphs.
Continuation starts a new run with explicit grants and allocation, schedules pending batches only, and carries prior successes and failures forward.
Retrying an ordinary failed batch requires explicit caller rescheduling.
Abrupt termination can recover only previously completed saves; no uncertain external effect is automatically replayed.
`format_candidate_review` creates inert review text with exact source, dependencies, effects, selected evidence, and observed checks; it can be saved using the same storage capability without ratifying or executing it.

## Verify and evaluate

Run offline acceptance with `uv run pytest -q -m "not integration"`.
With `OPENAI_API_KEY` explicitly available, run `NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 uv run pytest -q -s tests/integration/agent/test_composition.py` for the finite discovery, learning, and combined live scenarios.
The tests print actual model names, observation mode, configured limits, request counts, and observed tokens, without credentials.
Focused runs permit at most 24 requests and 60000 observed tokens; the larger combined scenario permits 48 requests and 120000 observed tokens per run, with provider output capped at 4000 tokens per response.
The test platform uses confinement and termination doubles; passing it does not establish a deployment's isolation guarantees.
Skipped live tests do not satisfy live acceptance.

`evaluate_object_workflow.py` compares every expected input and every reported result, rejecting duplicate input identities and exposing missing, incorrect, or duplicate findings.
`evaluate_workflow(run_input, report, ledger_tuple=(first_run.ledger, later_run.ledger))` distinguishes citation coverage from evidence coverage supported by exact invocation inputs and committed output.
Unknown and unrelated citations remain visible; a result's numeric correctness does not imply evidence coverage, execution success, or observed execution completion.
The evaluator reports completion, success, and completeness of live effect observations separately. Live observations do not certify durable audit persistence.
`resolve_evidence(ledger, reference)` is the shared library lookup for exact event fields and recorded value paths; existence alone does not establish relevance or truth.
`await evaluate_fixed_tasks(runner=runner)` compares the same fixed inputs with the ordinary `python_workflow` baseline and reports elapsed seconds, observed requests/tokens, usage basis, and evidence coverage.
The explicit numeric rule already has a complete Python implementation, so this example establishes composition behavior rather than a reason to generate code for numeric filtering.
Unavailable measurements remain `None`; scripted success or accounting is not evidence of model quality or cost improvement.

## Supply durable perimeter auditing

Applications permitting perimeter effects explicitly configure `Runner.perimeter_audit_store`.
The absence of a store refuses such a run before admission; selecting `InMemoryPerimeterAuditStore` explicitly is suitable only for tests and experiments without durable-audit guarantees.
The application-owned `SqlitePerimeterAuditStore` supplies a usable local reference implementation:

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

Each record commits with SQLite `synchronous=FULL` before returning.
The example assumes a supported local filesystem with working storage synchronization, not remote replication or tamper resistance.
The Harness records an attempt before entering a perimeter capability. A storage failure refuses the effect; an attempt without durable completion remains indeterminate.
This audit backend is separate from domain artifact storage and does not approve retained source.

## Sessions and the optional terminal

`uv run python -m examples.repl` starts the repository-local terminal.
The [terminal and embedding guide](repl/README.md) covers Python-only cells, approved Natural calls, concurrent Sessions, explicit retention, interruption, and bounded history/evidence queries.
The other examples remain callable workflows over the same Harness without a terminal or conversation dependency.
`examples.repl.session_factory:build_session_manager` is a lazy factory permitting fixed execution/decision model pairs and honoring terminal `--execution-model` and `--decision-model` before any run.
Its source-service variant composes the existing writer with a supplied audit store and optional confinement/termination services for selected artifact export.
The default Session already supports generated function declaration and source reuse in its own interpreter.
There is no background scheduler, automatic candidate cache, or source approval through storage.
