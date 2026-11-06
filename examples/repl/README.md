# Persistent Python/Natural terminal

This is an optional interactive application of the [Agent principles](../../docs/agent/principles.md).
It combines reusable Session infrastructure, Agent Parts in a replaceable conversation composition, and terminal presentation over the same Harness used by direct Python callers.
Neither the conversation strategy nor the terminal defines a required Agent loop.
The [architecture](../../docs/agent/architecture.md#responsibility-boundaries) describes the logical component boundaries.

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
Terminal dependencies are development dependencies.
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
For binding declarations, approved functions, typed writes, and callable reuse, see [Embedding Sessions](../../docs/agent/sessions.md#input-and-persistent-bindings).

## Operator views

Python and the operation palette share the [typed Session operations](../../docs/agent/sessions.md#shared-operator-operations).
Palette text is never evaluated as Python.
Local and background queries open searchable lists; Enter opens one observation and Escape restores the list position.
List scrolling follows the selected item's screen position, including wrapped and multiline records.
The palette's background registration form selects an existing adapter binding by name.
Rebinding the Python name `session` is visible as shadowing; canonical palette operations still address the owner.

During synchronous work, inspection uses received immutable snapshots with their original times.
A query requiring fresh owner state remains visibly pending; browsing retained data stays available.
Missing revisions and data never observed are labelled unavailable.
No timer, animation, or transport receipt is evidence of provider activity or completed cancellation.

Ctrl+T searches conversation records; Ctrl+R searches editor input; F3 searches captured streams; F2 opens authoritative run-evidence projections.
These collections retain their separate meanings, identities, and truncation markers.
History filters use a confirmed cell-outcome index, so unfinished cells are not classified as success.
Selected historical records remain anchored when output arrives or older pages load, and Escape restores the prior search and position after evidence navigation.

Enter on a history record, captured stream, or evidence excerpt opens plain retained text without gutters or visual-wrap newlines.
Opening an incomplete conversation page excerpt requests the fuller retained record; page truncation alone is not proof that the original retained text is unavailable.
Interactive views escape terminal control bytes to keep output from overwriting fixed screen regions; semantic text queries retain the original characters.
No clipboard action or export file is created by inspection.

## Execution process and shutdown

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

The embedding library's [lifecycle contract](../../docs/agent/sessions.md#embedding-and-lifecycle) defines interruption, background owners, and the distinction between operator `exit()` and embedding `close()`.

## Project directory and Manager factory

```sh
uv run python -m examples.repl --directory /path/to/project --session-manager-factory project_session:build_session_manager
```

The terminal prepares the directory before importing the factory.
The function receives `execution_model` and `decision_model` by keyword and returns one `SessionManager`; the terminal calls `create_session()` once.
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
See [Session model selection](../../docs/agent/sessions.md#explicit-model-selection) for Runner configuration and lexical model scopes.

## Source export factory

A project factory can call `examples.repl.session_factory.build_source_session_manager` with `execution_model`, optional `decision_model`, a selected `directory`, a supplied `perimeter_audit_store`, and optional `perimeter_confinement` and `termination_mechanism` services.
It composes the read-only workspace inspection capabilities and fixes write authority to that directory, then constructs `SessionManager(runner=...)` before any run.
Your terminal-facing `build_session_manager(*, execution_model=None, decision_model=None)` binds those deployment services and returns that Manager.
If supplied, the confinement service must prevent direct perimeter effects in the actual execution environment; the audit store must persist attempt/completion records before returning.
The repository does not provide a deployment confinement implementation.
The confirming confinement and in-memory audit doubles in tests prove integration only and must not be presented as deployment guarantees.
For a local durable audit implementation, pass `SqlitePerimeterAuditStore` from [the audit example](../durable_audit.py) to the source-session factory; its database path and filesystem guarantees remain application choices.
