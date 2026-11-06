# Embedding Sessions

Read [Agent Parts and Harness](index.md) for run configuration and [runtime obligations](runtime.md) for application-owned services.
`nighthawk.session` retains Python bindings and execution evidence in the caller's interpreter and event loop.
The [optional terminal](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md) is one application of this library.

## Embedding and lifecycle

Use `create_session(execution_model=...)` for lazy provider configuration, or construct `Runner` and `SessionManager` with your own executor, allocation factory, grant, perimeter policy, and optional conversation builder.
The standard builder returns an ordinary `AsyncScaffoldComposition[ConversationInput, ConversationResult]`; a replacement may narrow its supplied grant and policy but cannot widen them.
`ConversationInput` carries the submitted read/write binding names. A custom composition returns actual Python values in `ConversationResult.binding_name_to_value`; Session validates that complete write set against the same declarations and types before the run ends.
The standard composition uses the core executor directly for declared bindings. Accepted values also appear in the completed run result so publication evidence retains their ledger snapshots.

<!-- runnable-example:session_embedding -->
```py
import asyncio

from nighthawk.session import CellInput, create_session


async def main() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        execution = session.submit(CellInput("import asyncio\nawait asyncio.sleep(0)\nanswer = 42", mode="python"))
        outcome = await execution.wait_async()
        assert outcome.status == "success"
        assert await execution.wait_async() is outcome
        assert session.binding_name_to_value["answer"] == 42
        print("answer=42; repeated_wait_preserves_outcome=True")
    finally:
        assert session.close()


asyncio.run(main())
```
<!-- /runnable-example:session_embedding -->

`submit` queues work; `wait` or `wait_async` owns its execution.
Only one driver and one unfinished cell are allowed per Session.
Cooperative awaits permit application tasks and other Sessions to progress on the caller's loop; synchronous Python can still block it.
The library owns no scheduler, stream proxy, or signal handler, and arbitrary tasks/threads must be joined by the code that creates them.

`execution.request_interrupt()` requests cancellation.
Queued cancellation runs no code and creates no run.
A running cell records completion only after its managed work ceases; prior Python effects remain and subsequent cell work does not resume after cancellation.
When an observed branch cannot be stopped, the outcome remains absent, new submissions remain blocked, and `close()` returns False.
After body unwind, a later wait may reattach to cleanup without replaying any statement or publication.
Pending asynchronous branches require their owning loop.
Repeat `close()` after completion to dispose source/history/evidence; completed CellExecution snapshots remain readable.
Closing an idle or queued Session returns True and repeated close is idempotent.

Output is recorded before notification.
An ordinary Exception from `on_output` disables that callback for the current wait and records a delivery diagnostic; execution and publication continue.
Use `read_output()` to recover missed output.
External waiter cancellation and other BaseException interruptions propagate after cleanup.

For operator exit, use `session.exit()` rather than the embedding cleanup primitive `close()`.
It checks explicitly registered background owners and unresolved run cleanup before changing lifecycle state.
A refused result identifies blockers, leaves the Session usable, and creates no pending exit.
An accepted call inside an operator cell returns without self-wait and finalizes after that cell unwinds.
The library never exits the embedding application.

A trusted background adapter implements `ManagedBackgroundWork.read()` and `request_interrupt()` with bounded host callbacks.
Its `BackgroundObservation` certifies `stopped` or `failed` only after actual cessation, including joining owned threads/processes or finishing an owned asynchronous task.
Register the adapter with `work_id = session.background.register(owner)`; registration starts no job and scans no tasks.
Use `session.background.interrupt(work_id)` to ask that owner to stop, and inspect its observation until cessation is confirmed.
A callback failure is `unknown` and still blocks exit.
Background completion alone does not close the Session: request `session.exit()` again explicitly.
`close()` remains a distinct embedding cleanup request that may cancel managed work and returns False until it has ceased.

## Session failures

`SessionError` inherits `NighthawkError` and groups the embedding API's lifecycle and lookup failures:

- `SessionBusyError`: an unfinished cell or wait driver already owns the Session.
- `SessionClosedError`: the Session is disposed or closing admission.
- `SessionLookupError`: a requested name or run is unavailable; also a Python `LookupError`.
- `SessionWaitContextError`: the chosen wait driver cannot run on the current event loop.

These errors describe API use; a submitted cell's ordinary execution failure is reported in its `CellOutcome` and output records.
Inspect `outcome.status` after waiting, and propagate caller cancellation after cleanup.
See [Error handling](../specification.md#13-error-handling) for the common exception contract.

## Input and persistent bindings

Use `CellInput(source, mode="python")` for operator Python or `CellInput(text, mode="natural")` for a Natural cell.
Implicit mode recognizes the `> ` Python prefix; explicit mode avoids interpreting that prefix.
Only submit Python or Natural program text authorized by the host; do not turn untrusted text into operator approval.

Assignments, imports, and definitions retain ordinary Python bindings.
Natural cells use the core binding grammar: `<name>` reads a Python binding and `<:name>` declares a Session write binding.
As in Natural blocks, markers take ASCII Python identifiers; dotted paths are expressions, not binding names.
Plain prose such as "save as total" does not declare a write binding. Only markers in the submitted input declare bindings; history and tool results cannot add them.
There is no `_`, numbered result binding, hidden cache, or automatic persistence.
Source export requires a granted writer and audit storage; see [Retained source and explicit export](#retained-source-and-explicit-export).
Python annotations govern writes; absent an annotation, the core infers the type from the existing non-None initial value, otherwise it uses `object`.
An annotation without an initial value is supported, as are forward annotations resolved in the Session namespace.
All proposed bindings are validated and coerced before any Session assignment. An invalid or undeclared target prevents the complete write set from being published.
Unassigned write bindings remain unchanged. Atomicity covers the write set, not mutations of referenced objects or effects of function calls.
Rendered prose and proposed source never execute automatically.

Public approved functions are available to later runs by default.
Operator lambda expressions such as `f = lambda a: a + 10` are observed and discoverable just like ordinary function definitions; aliases preserve the same source identity.
An operator definition submission approves its exact source for this Session; successful materialization and later invocation are separate facts.
Private names are excluded from automatic discovery. `<_name>` explicitly reads an eligible private binding for that run; `session.expose("_name")` includes it in later discovery.
Explicit bindings cannot confer callable approval or expose Session operator controls. An operator may directly call an approved private Natural function from Python.
Imported functions require a trusted `RatifiedFunctionSource` registration in the Manager; name matching and writable function metadata confer no approval.
Natural functions declare their completed result with a return annotation, or a trusted `function_to_result_type` Manager registration.
Missing or unresolved declarations fail before the outer body and provider access.

```python
import nighthawk as nh


@nh.function
async def describe(value: int) -> str:
    result: str = ""
    """execute
    ---
    deny: [raise, return, break, continue]
    ---
    Describe <value> and write the description to <:result>.
    """
    return result
```

Submit the definition with `CellInput(source, mode="python")`, then submit `description = await describe(total)` as another Python cell.
Each outer Natural call has a fresh run; nested calls share its grant and allocation.
Nested Natural steps use their own Python arguments and locals, with normal global and lexical lookup; parent conversation inputs and temporary evaluator bindings are not implicitly copied into the child prompt.
Creating or displaying a coroutine does not implicitly await it or admit a run.
Future flags, function globals, aliases, redefinition, and original-cell tracebacks retain normal Python semantics.

## Shared operator operations

Typed operations under `session` perform no model request:

```python
from nighthawk.session import HistoryFilter, LocalQuery

session.help()
session.operations.list("rename")
session.locals.read(LocalQuery(name="samples", exposure="candidate"))
session.locals.inspect("samples")
session.locals.rename("samples", "measurements")
session.locals.delete("measurements")
session.execution.read()
session.execution.interrupt()
session.background.list()
session.history.search("samples", filters=HistoryFilter(outcome="success"))
```

Local previews include observation time, revision, exposure description, continuation, and incompleteness.
They use bounded built-in projections without user `repr` or dynamic properties.
Source candidates with unsupported proposal metadata receive an unavailable preview rather than executing that metadata.
Automatic previews recognize basic types by identity, independently of user-defined equality.
Explicit operator expressions such as `repr(value)`, `str(value)`, and comparisons retain their ordinary Python side effects, as does displaying a cell's result.
Automatic observation is a bounded presentation facility, not a general restriction on those expressions.
A future-run candidate is not a claim that the value belongs to a current grant.
Controller objects and bound operator methods remain operator-only even when retained through aliases.

A rename or delete from outside the owning cell while the namespace is busy returns `busy` immediately without queuing.
Explicit operator Python in the owning cell may apply the same operation at its own statement boundary.
Rename refuses an existing destination and preserves the original object's identity.
Controller/interpreter names are unavailable through binding organization; ordinary Python assignment retains its normal meaning.

## Context, history, and evidence

Default data previews accept exact basic built-ins and selected portions of lists, tuples, and string-keyed dictionaries.
Each query returns at most 20 top-level items and 6000 characters of projected content, visits at most 200 entries, and descends at most four levels.
Cycles, unsupported objects, and omitted content are marked incomplete without invoking arbitrary object repr.
Paging top-level names cannot recover missing nested elements of one large value.
Use an approved operator function or explicitly admitted reference for whole-data calculations.

`query_session_data`, `query_session_functions`, `resolve_session_function`, and `query_session_history` are typed capabilities bound to the run's final admitted selection.
They also respect nested grant attenuation, including previously obtained capability wrappers.
History queries use the captured run-entry horizon; a model cannot query future records or another Session's history.
Historical text is context, not operational state or evidence that an effect completed.

```python
page = session.history.read(maximum_item_count=5, maximum_character_count=2000)
matches = session.history.search("samples")
if matches.next_starting_index is not None:
    more = session.history.search(
        "samples",
        starting_index=matches.next_starting_index,
        horizon=matches.horizon,
    )
```

History keeps immutable text records of at most 6000 characters each.
Read/search pages default to at most 20 records and 6000 excerpt characters; supplied limits may lower those ceilings.
Search is literal and case-sensitive and may scan all retained records; neither storage nor computation has a constant bound.
Conversation pages carry immutable `cell_outcome_tuple` metadata for their returned cells only after authoritative completion within the page's horizon.
Outcome filters use that metadata even when the terminal status record is absent from the displayed page; older horizons cannot disclose a later completion.
Excerpt truncation and further-record continuation are distinct.
Core context/tool-result rendering may further shorten these structured pages; Session provides no separate tokenizer or model renderer.
`session.history.text(record_index)` returns retained semantic text in a `PlainTextObservation` with explicit availability and incompleteness.
Read Python continuation fields explicitly when a model preview omits them.

Live run identities appear in `execution.run_id_tuple` immediately upon admission, before output or terminal outcome.
Use `session.read_run_evidence(run_id, EvidenceQuery(...), bounds=EvidenceBounds(...))` for bounded projections of the actual retained ledger.
Unknown or other-Session run identities and reads after disposal fail with typed errors.
Block citations do not create ledger events, and publication observations refer to the accepted run result without appending to an ended ledger.

## Explicit model selection

`nighthawk.adapters.pydantic_ai.harness.model_runner(model_configuration_tuple=..., default_execution_model=..., default_decision_model=..., perimeter_policy=..., resource_allocation_factory=...)` identifies each configuration by its execution and decision model names, copies it at construction, and lazily builds one delegate per selected pair.
`create_session(execution_model=..., decision_model=...)` uses the same integration with exactly one permitted pair.
There is no fallback, automatic routing, or mutable Session model registry.

Natural functions carry no model configuration; they use the models selected for their caller.
Select models lexically **inside an admitted Natural function or composition**, for example in trusted operator Python:

```python
import nighthawk as nh


@nh.function
async def child(value: int) -> int:
    result: int = 0
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Add one to <value> and assign it to <:result>.
    """
    return result


@nh.function
async def parent(value: int) -> int:
    with nh.scope(execution_model="openai-responses:gpt-6.1-sol"):
        return await child(value)
```

`nh.scope(execution_model=...)` and `nh.scope(decision_model=...)` each replace one model of the current pair, and the resulting pair must be permitted.
A core scope outside a run is unavailable; select the outer pair when constructing the Runner or Session.
Returning, raising, and cancellation restore the enclosing pair.
All selections share the original run allocation and grant; consumption on one model can prevent the next model's request.
A full `step_executor_configuration` selection must equal a Runner-permitted configuration; other setting overrides fail before provider access.
The host wrapper remains installed throughout.
`NaturalExecutionOutcomeEvent.block_kind` distinguishes `execute` from `decide`; `response_model_name_tuple` records actual response identities of either kind in observation order, with `None` when unavailable; `executor_usage` belongs only to that Natural execution.
These fields are also available through the bounded run-evidence projection:

```python
from nighthawk.parts.evidence import EvidenceQuery

page = session.read_run_evidence(execution.run_id_tuple[0], EvidenceQuery())
```

## Declared Session functions

Natural can declare a function directly into a Session write binding:

```python
execution = session.submit(CellInput("Declare a Fibonacci function <:fib> with an integer argument and result.", mode="natural"))
outcome = await execution.wait_async()
if outcome.status == "success":
    invocation = session.submit(CellInput("fib(3)", mode="python"))
    result = await invocation.wait_async()
```

The binding contains a `SessionFunction`, not a `SourceCandidate` or an approved operator Python function.
The default conversation first selects a `generation.FunctionRequest(description, export_name)`.
The independently replaceable `generation.propose_function` judgment produces its inert source proposal.
`FunctionDeclaration(proposal, export_name)` is a typed conversation action accepting a definition-only `CodeProposal` and a declared callable export.
It validates and materializes the definition without invoking its body, returning a callable that the next Natural step assigns with ordinary write-binding syntax and type validation.
The Session adapter wraps the same `generated.RetainedFunction` used by batch applications and records its originating cell separately.
Its `definition` carries exact source, dependencies, and original generated identity; `originating_cell_id` belongs to the Session adapter.
Only a successfully accepted write set changes Session locals; failed declaration or write validation leaves the prior bindings intact.

Each operator Python-cell call admits a new run and uses ordinary generated dispatch without regenerating the source.
An interior call uses its current run; repeated calls to the same reference within that run share a materialized definition.
Dependencies resolve from the current effective grant on every call, including after nested attenuation.
Asynchronous definitions return a coroutine: use `await fib(3)` in Python and Natural cells.
Public Session functions are discoverable by later Natural cells, and explicit read bindings also work.
`previous = fib` preserves the old definition when a later declaration rebinds `fib`.
Closure and default-argument state follow [retained-function semantics](index.md#reuse-generated-functions-across-runs).
Closing the owning Session prevents further calls, including through escaped aliases.
Applications can explicitly select `function.retained_function` for use in another host-established run, including after Session closure.
This preserves generated source rather than the old Session's grant, approval evidence, or materialized execution.
Generator and other lazy results retain the generated-execution lifetime rules; publishing a callable does not extend the run of its returned objects.

The default Session declares and invokes generated functions in its own interpreter under the [in-process regime](runtime.md#in-process-execution-and-optional-confinement).
Explicitly configured confinement must confirm declaration and every invocation; a refusal fails without publishing inert source as a function.
Allocation, grants, declared dependencies, provenance, and atomic write-binding acceptance remain in effect for each call.
Publication never ratifies generated source; submitting reviewed source explicitly as operator Python remains a separate operation.

To replace only a judgment, supply a partially configured `build_conversation_composition` as the manager's builder:

```python
from functools import partial
from nighthawk.session import SessionManager, build_conversation_composition

manager = SessionManager(
    runner=runner,
    conversation_composition_builder=partial(build_conversation_composition, generate_function=my_generate_function),
)
```

`my_generate_function(request, context)` is an ordinary async function returning a definition-only `CodeProposal`.
The builder also accepts `choose_step` for next-step selection.
The shared `conversation` module exposes these parts without Session ownership or implicit namespace access.

## Retained source and explicit export

An explicit `SourceRetention(binding_name, proposal)` in `ConversationResult.source_retention_tuple` publishes a `SourceCandidate` at a declared `<:binding_name>` target after typed result acceptance.
Session supplies its `originating_run_id` and `originating_cell_id`; the stored `CodeProposal` remains inert ordinary Python data.
Data previews include bounded source, exports, dependencies, and origin identities, marking omitted content incomplete.
No candidate is retained without a request, and closing the Session writes no files.
Old generated callable references stay expired even after source reuse, export, or later operator approval.

The standard conversation can choose `SourceInvocation(candidate_name, export_name, argument_tuple, keyword_argument_name_to_value)` as a typed intermediate action.
Deterministic Python resolves the selected candidate from the current grant, rematerializes through `dispatch`, and invokes the fresh sync/async reference.
Dependencies resolve against the new run's grant and declared types.
For a combined definition-and-invocation proposal, omit export name and arguments to use its declared entry exactly once.
The action's actual result becomes the next Natural block's `action_result`, which can supply an explicitly requested value retention.
An unavailable action produces a failure block immediately.
The default configuration supports source reuse through the same in-process dispatch as generated function calls.

`SourceExport(candidate_name, file_path)` writes the exact source to a new `.py` file and useful dependencies/origin metadata to a `.py.json` sidecar.
`DataExport(reference_name, file_path)` writes only the selected JSON-compatible data to a new `.json` file.
Unsupported values, non-string dictionary keys, and nonfinite numbers are rejected; there is no pickle or generic object serializer.
Both actions use the granted `apply_file_patch` capability and produce perimeter attempt/completion evidence.
The default writer refuses existing destination files; export is not a multi-file transaction, and attempted effects are never rolled back.
Export and retention never approve source.
An operator can separately submit the exact candidate definition as a Python cell, which creates new Session approval/materialization evidence while leaving the old generated lifetime unchanged.
This submission route requires a deliberate approval decision about that exact source; an application must not automatically relabel model output or historical text as operator input.
Approved repository functions may be integrated through the trusted imported-function registration described above.

Configure export capabilities through `SessionManager(runner=...)` with the application's writer, perimeter policy, and durable audit store.
See the [terminal factory example](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md#source-export-factory) for one application configuration.
