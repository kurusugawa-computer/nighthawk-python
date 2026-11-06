# Persistent Python/Natural terminal and Sessions

This is an optional interactive application of the [Agent extension principles](../../docs/agent/principles.md).
It combines reusable Session infrastructure, Agent Parts in a replaceable conversation composition, and terminal presentation over the same Harness used by direct Python callers.
The terminal demonstrates the Application responsibility; Examples do not form a separate library execution layer.
Neither the conversation strategy nor the terminal defines a required Agent loop.
The [architecture](../../docs/agent/architecture.md#responsibility-boundaries-and-current-packaging) describes the logical component boundaries and their current packaging.

Run from this checkout with Python 3.13+ and the development dependencies:

```sh
uv sync --all-extras --all-groups
uv run python -m examples.repl --help
uv run python -m examples.repl
```

The terminal uses one persistent prompt-toolkit application and a separate execution process that retains the live Session.
It uses the terminal's normal screen and scrollback, with a compact input editor and exactly one Status Line below it.
It does not enter the alternate screen or capture mouse events.
Input, history inspection, and elapsed-time updates remain available during synchronous Python.
The process boundary supplies scheduling independence; it does not establish perimeter confinement.
Neither is a Session/runtime dependency or an installed command-line entry point.
Python-only cells require no credentials and construct no provider.
For model-backed conversation, explicitly load your own environment file:

```sh
uv run --env-file .env python -m examples.repl --execution-model openai-responses:gpt-6-luna --decision-model openai-decisions:gpt-6-luna
```

Without `--execution-model`, the terminal selects `openai-responses:gpt-6-luna`.
Without `--decision-model`, `decide` blocks fail.
Default runs allow at most 24 model requests and 60000 observed tokens, with a 4000-token response cap; exhaustion is checked before the next request.
These bounds belong to each new run, not to the retained Session history.

## Input and persistent bindings

Enter inserts a newline; Escape-Enter submits the entire cell.
The initial mode is Natural language, displayed by the `#> ` prompt; Python mode uses `!> `.
In an empty Natural draft, type `!` followed by Space to switch to Python; these two keystrokes are consumed and are not submitted.
Backspace on an already empty draft returns to Natural language.
Deleting the last character only edits the draft; one additional Backspace switches modes.
The entire draft must be empty, not merely the current line.
`# ` remains ordinary content, including Python comments and Markdown headings.
Bracketed paste never selects a mode, even when the pasted text starts with `! `, `# `, or `> `.
The selected mode persists after submission and is restored with recalled history.
Escape-N submits one literal Natural cell without changing the selected editor mode.
Ctrl+C clears an idle draft without changing its mode or creating a history record.
Ctrl+D on an empty draft requests exit; managed background blockers cause refusal with no cancellation or pending exit.
Ctrl+C inside a palette, search, or inspector returns first; in the main view it interrupts the active cell while preserving the next draft.
A submission during unfinished work is refused immediately and never queued.
The single Status Line shows actual execution state, elapsed time during active execution, and a nonzero managed-background count as width permits.
An idle Session without background work shows only its state, such as `Idle (success)`, with no ticking observation age or empty fields.
Queued, running, and interruption-requested states come from the execution owner; a sent request awaiting its reply is labelled separately.
Keyboard, palette, and Python interruption use the same observed states, and delayed terminal requests remain tied to their original cell Id.
Keyboard instructions, directory, model, and input mode are available through F1.
In Python mode, submit `session.help()` with Escape-Enter for readable names, signatures, descriptions, and availability from typed Session operation metadata.
Help still returns ordinary typed values; its tuple/list display does not dump internal metadata fields.
Execution observation timestamps and full identities remain available through `session.execution.read()` and the inspectors.
Use F1 or the execution/background palette entries for details when a narrow screen abbreviates them.
Narrow screens use shorter state labels and omit whole optional fields.
Very narrow screens may show only the state; `Interrupt pending` and `Pending` do not certify cessation.
Errors, busy rejections, and exit refusal reasons are retained in console output rather than replacing the Status Line with instructions.

| Binding | Action |
| --- | --- |
| Enter | Insert a newline |
| `!` then Space in an empty Natural draft | Switch to Python mode |
| Backspace on an already empty draft | Return to Natural language mode |
| Escape-Enter / Escape-N | Submit selected mode / one literal Natural cell |
| Tab | Complete static binding names and canonical operation names |
| Ctrl+R | Search editor input history; Enter recalls editable input, Escape restores the exact draft and cursor |
| Up / Down | Move through display rows; beyond the top/bottom, recall the previous/next input |
| Ctrl+P | Search operations; Enter opens parameters, then a separate Enter explicitly activates |
| Ctrl+T | Search retained conversation history |
| F2 | Navigate from a selected cell to its runs and retained evidence |
| F3 | Inspect captured stdout/stderr, with native/background attribution labelled separately |
| F4 | Set conversation kind, cell Id, run Id, and confirmed outcome filters |
| F5 | Request the next local or evidence page |
| F6 | Request interruption of the selected managed background owner |
| F10 | Close retained failure diagnostics after the worker process has exited; return a failure exit code |
| PageUp / PageDown | Open and scroll retained output for keyboard browsing |
| Ctrl+L | Return from retained output to input and follow latest output |
| F1 | Read help without a model request |

Editing history stays in memory and is separate from semantic Session history.
Recalled Python and literal Natural cells retain explicit modes.
Navigation and Ctrl+R copy submitted input into the editor; neither changes a historical cell nor executes it.
Up enters the previous input's last display row; Down enters the next input's first display row, retaining the preferred screen column where possible.
Wrapped rows are traversed before changing input, and navigation stops at the oldest input and the latest draft without wrapping around.
The current draft and edits to recalled inputs survive navigation until submission or clearing the draft.
Ctrl+R searches the original submitted text, joins the same navigation session on selection, and restores the current draft on cancellation.
Submitting an edited copy appends a new input history entry.
Bracketed paste preserves multiline indentation, Japanese text, combining characters, and whitespace; ordinary multiline input also remains a draft until explicit submission.
`--no-animation` disables the animated activity mark while elapsed time continues updating.
State and failure labels do not depend on color.

Mouse dragging, wheel scrolling, and clipboard commands belong to the terminal.
In VS Code on macOS, drag to select, use Command-C to copy, and Command-V to paste; running Python inside a Docker container does not move the clipboard into that container.
See [VS Code terminal basics](https://code.visualstudio.com/docs/terminal/basics) for terminal selection and clipboard behavior.
Wheel scrolling browses the native terminal history, including output above the current screen; returning to the live end reveals the editor and Status Line again.
Paste preserves meaningful whitespace and remains a draft until explicitly submitted.
No application clipboard transport or special mouse-selection modifier is required.

To exit using Python, switch to Python mode, type `session.exit()`, and press Escape-Enter.
Enter alone inserts a newline; `session.exit` without parentheses only refers to the method.
An accepted exit releases the terminal after the execution owner is reaped; pending background work instead produces a refusal that identifies its blockers.

Redirected standard input is one complete cell, read through end-of-file and rendered as ordinary text without full-screen controls:

```sh
printf '> samples = [1, 2, 3]\nsum(samples)\n' | uv run python -m examples.repl
```

Redirected input and implicit-mode library `CellInput` retain the `> ` Python prefix; interactive drafts use their explicitly selected mode instead.

If exit is refused after end-of-file, the terminal reports refusal, disconnects, and supervises the execution owner until managed work independently ceases, then returns exit code 1.
It does not retry exit or interrupt background owners; a job that never certifies cessation keeps its owner supervised.
Use the interactive terminal when background work requires further operator input to stop.

The terminal's process/signal path and real controlling-terminal tests require POSIX.
Other platforms can use the embeddable Session and deterministic presentation services; equivalent native interruption has not been established there.

Submit these as separate cells, switching mode as indicated by the displayed prompt.
The prompts shown here are not part of the input text:

```text
!> samples = [1, 2, 3]
!> samples
#> Explain samples and assign its sum to <:total>.
!> total
!> def add_one(value: int) -> int:
    return value + 1
#> Call add_one with total and assign the result to <:incremented>.
!> incremented
```

The intended results are total 6 and incremented 7.
Assignments, imports, and definitions retain ordinary Python bindings.
Natural cells use the core binding grammar: `<name>` reads a Python binding and `<:name>` declares a Session write binding.
As in Natural blocks, markers currently take ASCII Python identifiers; dotted paths are expressions, not binding names.
Plain prose such as "save as total" does not declare a write binding. Only markers in the submitted input declare bindings; history and tool results cannot add them.
There is no `_`, numbered result binding, hidden cache, or automatic persistence.
Source reuse and export require the configuration described below.
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

Submit that definition as Python, then `description = await describe(total)` in the same mode.
Each outer Natural call has a fresh run; nested calls share its grant and allocation.
Nested Natural steps use their own Python arguments and locals, with normal global and lexical lookup; parent conversation inputs and temporary evaluator bindings are not implicitly copied into the child prompt.
Creating or displaying a coroutine does not implicitly await it or admit a run.
Future flags, function globals, aliases, redefinition, and original-cell tracebacks retain normal Python semantics.

## Shared operator operations

Python and the palette share typed operations under the existing `session` object.
They perform no model request, and palette text is never evaluated as Python:

```python
from nighthawk.agent.session import HistoryFilter, LocalQuery

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
Local and background query results open searchable lists; Enter opens one retained observation and Escape restores the list position.
List scrolling follows the selected item's actual screen position, including wrapped and multiline records, while search keeps keyboard focus.
Source candidates with unsupported proposal metadata receive an unavailable preview rather than executing that metadata.
Automatic previews recognize basic types by identity, independently of user-defined equality.
Explicit operator expressions such as `repr(value)`, `str(value)`, and comparisons retain their ordinary Python side effects, as does displaying a cell's result.
Automatic observation is a bounded presentation facility, not a general restriction on those expressions.
A future-run candidate is not a claim that the value belongs to a current grant.
Controller objects and bound operator methods remain operator-only even when retained through aliases.
Rebinding the Python name `session` is visible as shadowing; canonical palette operations still address the owner.

A palette rename or delete while the namespace is busy returns `busy` immediately without queuing.
Explicit operator Python in the owning cell may apply the same operation at its own statement boundary.
Rename refuses an existing destination and preserves the original object's identity.
Controller/interpreter names are unavailable through binding organization; ordinary Python assignment retains its normal meaning.

During synchronous work, inspection uses received immutable snapshots with their original times.
A query requiring fresh owner state remains visibly pending; browsing retained data stays available.
Missing revisions and data never observed are labelled unavailable.
No timer, animation, or transport receipt is evidence of provider activity or completed cancellation.

## Embedding and lifecycle

Use `create_session()` for lazy default configuration, or construct `Runner` and `SessionManager` with your own executor, allocation factory, grant, perimeter policy, and optional conversation builder.
The standard builder returns an ordinary `AsyncScaffoldComposition[ConversationInput, ConversationResult]`; a replacement may narrow its supplied grant and policy but cannot widen them.
`ConversationInput` carries the submitted read/write binding names. A custom composition returns actual Python values in `ConversationResult.binding_name_to_value`; Session validates that complete write set against the same declarations and types before the run ends.
The standard composition uses the core executor directly for declared bindings. Accepted values also appear in the completed run result so publication evidence retains their ledger snapshots.
Import `Runner` and its service contracts from `nighthawk.agent.runtime`; direct compositions use `runner.run(...)` over the same host machinery as Session.
See [runtime composition](../../docs/agent/index.md#runtime-composition) for direct invocation and [runtime obligations](../../docs/agent/runtime.md) for application-supplied services.

```python
from nighthawk.agent.session import CellInput, create_session

session = create_session()
execution = session.submit(
    CellInput(
        "import asyncio\nawait asyncio.sleep(0)\nanswer = 42",
        mode="python",
    )
)
outcome = await execution.wait_async()
assert outcome.status == "success"
assert await execution.wait_async() is outcome
assert session.close()
```

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

The terminal starts exactly one dedicated Session subprocess using an explicit module entry point and inherited communication descriptors.
The UI and Session are the only REPL-owned processes; no multiprocessing resource tracker or generated-execution worker is started.
The Session interpreter owns live Python/Natural/generated objects, grants, ledger, cwd, project import lookup, operator `__main__`, stdout/stderr capture, and execution-time signals.
The UI exchanges input and immutable observations; it does not mirror locals or decide execution completion.
These process and stream responsibilities belong solely to the terminal example; the embedded library uses its caller's interpreter and event loop.
The terminal sends interruption for one observed cell identity; a late request cannot interrupt a later cell.
Uninterruptible native code or an active bridge worker keeps cessation unconfirmed while the screen remains usable.
Captured stdout/stderr records are presentation observations, not run-ledger events.
Normal interactive semantic output uses compact kind labels such as `[execution]`, `[answer]`, and `[status]`.
Interleaved cells and multiple runs within a cell receive local display numbers where disambiguation is needed, such as `[answer cell 1 run 2]`.
These numbers are display labels, not Session identifiers: history and run inspectors show their correspondence to complete Id values.
Semantic text, retained record identities, evidence references, and incomplete markers remain intact.
Only output from the cell's own execution task receives its cell Id; other tasks, callbacks, threads, and native descriptors have unavailable cell attribution.
Sharing the execution owner's thread or running while another cell is active does not establish that relationship.
Presentation transport is bounded; explicit gap and incomplete markers disclose discarded display records without deleting Session history or ledger evidence.
A disconnected transport or crashed worker means failure with unknown cessation, not successful cancellation.
The parent watches process death independently of the control connection and reaps the worker through one owner.
Unexpected worker exit loses the live Session; no automatic restart, source replay, or object restoration occurs.
The screen retains received history, output, and the draft for inspection; F10 restores terminal settings and exits with a failure code.
This closes the diagnostic screen without claiming successful Session shutdown or completion of external work.
A lost connection while the worker remains alive keeps owner supervision active; F10 cannot abandon that owner.
After transport loss a surviving worker retains the event loop and its registered background owners until they certify cessation.
The terminal reaps the worker after confirmed shutdown and restores terminal modes on failure.

For operator exit, use `session.exit()` rather than the embedding cleanup primitive `close()`.
It checks explicitly registered background owners and unresolved run cleanup before changing lifecycle state.
A refused result identifies blockers, leaves the Session usable, and creates no pending exit.
An accepted call inside an operator cell returns without self-wait and finalizes after that cell unwinds.
The library never exits the embedding application.

A trusted background adapter implements `ManagedBackgroundWork.read()` and `request_interrupt()` with bounded host callbacks.
Its `BackgroundObservation` certifies `stopped` or `failed` only after actual cessation, including joining owned threads/processes or finishing an owned asynchronous task.
Register the adapter with `work_id = session.background.register(owner)`; registration starts no job and scans no tasks.
The palette's registration form selects an existing adapter binding by name.
Use `session.background.interrupt(work_id)` to ask that owner to stop, and inspect its observation until cessation is confirmed.
A callback failure is `unknown` and still blocks exit.
Background completion alone does not close the Session: request `session.exit()` again explicitly.
`close()` remains a distinct embedding cleanup request that may cancel managed work and returns False until it has ceased.

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

Ctrl+T searches conversation records; Ctrl+R searches editor input; F3 searches captured streams; F2 opens authoritative run-evidence projections.
These collections retain their separate meanings, identities, and truncation markers.
History filters use a confirmed cell-outcome index, so unfinished cells are not classified as success.
Selected historical records remain anchored when output arrives or older pages load, and Escape restores the prior search and position after evidence navigation.

Enter on a history record, captured stream, or evidence excerpt opens plain retained text without gutters or visual-wrap newlines.
Opening an incomplete conversation page excerpt requests the fuller retained record; page truncation alone is not proof that the original retained text is unavailable.
`session.history.text(record_index)` returns the same semantic text in a `PlainTextObservation` with explicit availability and incompleteness.
Interactive views escape terminal control bytes to keep output from overwriting fixed screen regions; semantic text queries retain the original characters.
No clipboard action or export file is created by inspection.

History keeps immutable text records of at most 6000 characters each.
Read/search pages default to at most 20 records and 6000 excerpt characters; supplied limits may lower those ceilings.
Search is literal and case-sensitive and may scan all retained records; neither storage nor computation has a constant bound.
The conversation inspector shares the Python history matching rules and displays how many records it has received within the latest observed horizon, including incomplete excerpts.
Conversation pages carry immutable `cell_outcome_tuple` metadata for their returned cells only after authoritative completion within the page's horizon.
Outcome filters use that metadata even when the terminal status record is absent from the displayed page; older horizons cannot disclose a later completion.
Excerpt truncation and further-record continuation are distinct.
Core context/tool-result rendering may further shorten these structured pages; Session provides no separate tokenizer or model renderer.
Read Python continuation fields explicitly when a model preview omits them.

Live run identities appear in `execution.run_id_tuple` immediately upon admission, before output or terminal outcome.
Use `session.read_run_evidence(run_id, HistoryQuery(...), bounds=HistoryBounds(...))` for bounded projections of the actual retained ledger.
Unknown or other-Session run identities and reads after disposal fail with typed errors.
Block citations do not create ledger events, and publication observations refer to the accepted run result without appending to an ended ledger.

## Project directory and Manager factory

```sh
uv run python -m examples.repl --directory /path/to/project --session-manager-factory project_session:build_session_manager
```

The terminal prepares the directory before importing the factory.
The function receives `model` by keyword and returns one `SessionManager`; the terminal calls `create_session()` once.
Factories honor or reject the selected model and may resolve resources relative to their own module.
Ordinary `import analysis`, module globals, and explicit `importlib.reload(analysis)` retain Python behavior.
Use separate terminal processes for separate projects with same-named modules; Sessions inside an existing application share its normal import cache.

## Explicit model selection

The tested multi-model factory returns a Manager without creating a Session or provider:

```sh
uv run --env-file .env python -m examples.repl --session-manager-factory examples.repl.session_factory:build_session_manager --execution-model openai-responses:gpt-6-luna
```

It permits `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol` for `execute` blocks, each with no decision model or `openai-decisions:gpt-6-luna` for `decide` blocks, and rejects other choices before provider access.
Adapt the fixed tuples in your own project factory to models available to your account.
`Runner.from_models(model_configuration_tuple=..., default_execution_model=..., default_decision_model=..., perimeter_policy=..., resource_allocation_factory=...)` identifies each configuration by its execution and decision model names, copies it at construction, and lazily builds one delegate per selected pair.
`create_session(execution_model=..., decision_model=...)` uses the same integration with exactly one permitted pair.
There is no fallback, automatic routing, or mutable Session model registry.

Natural functions carry no model configuration; they use the models selected for their caller.
Select models lexically **inside an admitted Natural function or composition**, for example in trusted operator Python:

```python
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
A core scope outside a run is unavailable; select the outer pair with the factory.
Returning, raising, and cancellation restore the enclosing pair.
All selections share the original run allocation and grant; consumption on one model can prevent the next model's request.
A full `step_executor_configuration` selection must equal a Runner-permitted configuration; other setting overrides fail before provider access.
The host wrapper remains installed throughout.
`NaturalExecutionOutcomeEvent.block_kind` distinguishes `execute` from `decide`; `response_model_name_tuple` records actual response identities of either kind in observation order, with `None` when unavailable; `executor_usage` belongs only to that Natural execution.
These fields are also available through the existing bounded run-evidence projection:

```python
from nighthawk.agent.history import HistoryQuery

page = session.read_run_evidence(execution.run_id_tuple[0], HistoryQuery())
```

## Declared Session functions

With generated-execution services configured, Natural can declare a function directly into a Session write binding:

```text
#> Declare a Fibonacci function <:fib> with an integer argument and result.
[publication] defined fib
[status] success
!> fib(3)
2
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
Hidden closure and mutable default-argument state are not persistent across runs; use explicit parameters, results, or declared shared references for persistent state.
Closing the owning Session prevents further calls, including through escaped aliases.
Applications can explicitly select `function.retained_function` for use in another host-established run, including after Session closure.
This preserves generated source rather than the old Session's grant, approval evidence, or materialized execution.
Generator and other lazy results retain the existing generated-execution lifetime rules; publishing a callable does not extend the run of its returned objects.

The default Session declares and invokes generated functions in its own interpreter, with limited CPython audit checks and no external confinement claim.
Observed direct file writes, network operations, and process creation inside runs are rejected outside admitted host gateways.
This is not a sandbox: native code, unaudited descriptors, shared memory, and work without propagated context retain Python's structural limitations.
The dispatch ledger records `confinement_identity=None`.
Explicitly configured confinement must confirm declaration and every invocation; a refusal fails without publishing inert source as a function.
Allocation, grants, declared dependencies, provenance, and atomic write-binding acceptance remain in effect for each call.
Publication never ratifies generated source; submitting reviewed source explicitly as operator Python remains a separate operation.

To replace only a judgment, supply a partially configured `build_conversation_composition` as the manager's builder:

```python
from functools import partial
from nighthawk.agent.session import SessionManager, build_conversation_composition

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
Both actions use the existing granted `apply_file_patch` capability and produce perimeter attempt/completion evidence.
The default writer refuses existing destination files; export is not a multi-file transaction, and attempted effects are never rolled back.
Export and retention never approve source.
An operator can separately submit the exact candidate definition as a Python cell, which creates new Session approval/materialization evidence while leaving the old generated lifetime unchanged.
This submission route requires a deliberate approval decision about that exact source; an application must not automatically relabel model output or historical text as operator input.
The principles also permit an independent automated approval workflow under human-established conditions.
Such a workflow belongs to external application governance, and this terminal supplies no automatic approval action or pipeline.
Approved repository functions may be integrated through the existing trusted imported-function registration described above.

A project factory can call `examples.repl.session_factory.build_source_session_manager` with `execution_model`, optional `decision_model`, a selected `directory`, a supplied `perimeter_audit_store`, and optional `perimeter_confinement` and `termination_mechanism` services.
It composes the existing read-only workspace inspection capabilities and fixes write authority to that directory, then constructs `SessionManager(runner=...)` before any run.
Your terminal-facing `build_session_manager(*, execution_model=None, decision_model=None)` binds those deployment services and returns that Manager.
If supplied, the confinement service must prevent direct perimeter effects in the actual execution environment; the audit store must persist attempt/completion records before returning.
The repository does not provide a deployment confinement implementation.
The confirming confinement and in-memory audit doubles in tests prove integration only and must not be presented as deployment guarantees.
For a local durable audit implementation, pass `SqlitePerimeterAuditStore` from [the audit example](../durable_audit.py) to the source-session factory; its database path and filesystem guarantees remain application choices.
