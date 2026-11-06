# Agent Parts and Harness

Reusable Agent Parts and host-owned execution mechanisms for [Nighthawk](https://github.com/kurusugawa-computer/nighthawk-python), delivered as the `nighthawk.agent` library.
AI agents combine Natural judgment with Python computation and actions to construct capabilities, carry out work, assess results and effects, and improve later attempts.
Python defines execution structure even when an agent authors that structure during execution.

The library supports multiple execution strategies, including recursive code generation, procedure discovery, explicit learning, and application-defined compositions.
No single agent loop or conversation framework is required.
Reusable Sessions support embedding, and an optional repository-local terminal demonstrates an interactive application.
There is no installed CLI.

Read [Quickstart](../quickstart.md) for Core setup. The [Agent principles](principles.md) apply the [Nighthawk philosophy](../philosophy.md) to Harness and Agent Parts responsibilities.
They take precedence over architectural choices without implying that every desired workflow is already implemented.

## Install

Python 3.13 or newer is required.
Install this checkout into another project's virtual environment:

```sh
uv pip install /path/to/nighthawk-python
# Optional OpenAI provider dependencies:
uv pip install '/path/to/nighthawk-python[openai]'
```

The base installation supplies the library without terminal dependencies.
The repository-local terminal and development checks use `uv sync --all-extras --all-groups`.

## Responsibility boundaries

Core supplies Natural/Python execution semantics in the same `nighthawk-python` distribution.
The `nighthawk.agent` package combines Harness mechanisms with Agent Parts: the Harness admits and records governed execution, while parts decide and carry out how work proceeds within that authority.
Agent Parts are reusable Python functions and Natural contracts, ranging from an individual judgment to a complete execution strategy; composition combines them through ordinary Python.
Core, Harness, and Agent Parts are three logical library responsibilities; the [architecture](architecture.md#responsibility-boundaries-and-current-packaging) maps them to current modules without requiring separate packages or a common part base class.

Applications supply goals, domain actions, policy, storage, runtime services, and capability improvement workflows.
Application is the responsibility outside the library that determines what to achieve, permit, and accept.
Examples are verified reference applications demonstrating the combination, rather than a fourth library execution responsibility.
They may approve reusable source through human review or independent automated approval under human-established conditions.
The library consumes approved source and records execution evidence; it does not supply an automatic approval pipeline.

## What it provides

- Grants: immutable name-to-reference mappings that bound what a Natural execution or generated execution may reach.
  Capabilities carry a stable name, a typed signature, an authority boundary, and a declared effect footprint.
- Host-owned dispatch: generated Python source enters execution only as a validated `CodeProposal` through `dispatch()` or the granted `execute_code` capability.
  Materialized definitions get stable identities and are re-entered through host mediation on every call.
- A run ledger: an append-only typed record of run entry, `execute` and `decide` Natural execution outcomes, dispatches and validated invocation inputs, definitions, capability requests, perimeter effects, and run end.
- A platform contract: opaque resource allocation and perimeter audit storage are supplied by the host; external confinement and forced branch termination are optional services whose guarantees the library never fabricates.
- An optional fixed-loop composition in `nighthawk.agent.reference`, using the same Harness mechanisms as other strategies.
- Optional capability families in `nighthawk.agent.tools` (workspace inspection, web research, coding actions, and explicit artifact storage) that return grants.
- Optional `values`, `history`, `catalog`, `procedures`, and `learning` modules for bounded views over retained Python objects, causal evidence, progressive procedure discovery, and explicitly invoked learning.
- [Callable object-workflow examples](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/README.md) covering concurrent typed branches, ordinary partial failures, learning from observed results, and saved handoffs to fresh runs.
- `nighthawk.agent.runtime.Runner` for reusable sync/async run configuration, and `nighthawk.agent.session` for persistent Python namespaces, whole-function Natural mediation, bounded conversation history, and explicit result retention.
- [Persistent terminal and embedding guide](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md), launched with `python -m examples.repl` from this checkout.

## Compose reusable parts

| Need | Public surface |
| --- | --- |
| Inspect bounded retained values | `values.read_text_window`, `values.search_text`, `values.select_sequence_window` |
| Describe caller-owned procedures | `catalog.ProcedureCatalog`, `catalog.build_procedure_discovery` |
| Choose and invoke within current authority | `procedures.select_procedure`, `procedures.selected_procedure` |
| Materialize and explicitly retain generated procedures | `procedures.materialize_procedure` |
| Retain generated functions for fresh runs without a Session | `generated.retain_function`, `generated.RetainedFunction` |
| Propose source from explicit requirements and context | `generation.FunctionRequest`, `generation.propose_function` |
| Select one conversation step or compose an optional loop | `conversation.choose_conversation_step`, `conversation.converse` |
| Execute a selected action or export selected values | `conversation.perform_conversation_action`, `sources.invoke_source`, `sources.export_source`, `sources.export_data` |
| Inspect causal observations or resolve an exact citation | `history.analyze_run`, `history.project_history`, `history.resolve_evidence` |
| Derive explicit lessons and inert candidates | `learning.derive_learning`, `learning.format_candidate_review` |
| Admit and account for a Python composition | `runtime.Runner`, `runtime.ScaffoldComposition`, `runtime.AsyncScaffoldComposition` |

These parts are ordinary functions and values. Applications choose which to combine, where to retain results, and what evidence is sufficient for reuse.
For example, this body materializes one generated function and invokes it through the same host mechanisms used by other strategies:

```python
from nighthawk.agent import CodeProposal
from nighthawk.agent.catalog import ProcedureCatalog
from nighthawk.agent.procedures import materialize_procedure, selected_procedure
from nighthawk.agent.runtime import RunContract, ScaffoldComposition


def increment(value: int) -> int:
    catalog = ProcedureCatalog()
    selection = materialize_procedure(
        catalog,
        CodeProposal(source="def increment(value: int) -> int: return value + 1", export_name_tuple=("increment",)),
        export_name="increment",
        purpose="Increment an integer",
    )
    with selected_procedure(catalog, selection) as procedure:
        assert callable(procedure)
        return procedure(value)


composition = ScaffoldComposition(RunContract(int, int), increment)
# Supply your configured Runner:
# result = runner.run(composition, 6)
```

This illustrates mediation and composition; ordinary Python already suffices for incrementing a number.
The [reference applications](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/README.md) combine Natural selection, generation, concurrent work, explicit learning, evaluation, and continuation.
Their evaluators compare complete results with explicit Python baselines, resolve observed input/result evidence, and distinguish scripted accounting from provider observations.

## Reuse generated functions across runs

`retain_function(proposal, export_name=...)` validates and materializes a definition-only proposal in the active run without calling its body.
Its `RetainedFunction` keeps the exact proposal and observed definition identity.
Applications retain that ordinary Python value wherever they choose; no catalog, Session, storage backend, or approval workflow is required.
Calling it in a later run rematerializes its source under that run's current grant.
Each run shares one materialization per retained callable identity, while every invocation resolves dependencies and passes through normal dispatch admission again.
For an asynchronous function, materialization and invocation begin when its returned coroutine is awaited.

```python
from nighthawk.agent import CodeProposal
from nighthawk.agent.generated import RetainedFunction, retain_function
from nighthawk.agent.runtime import RunContract, ScaffoldComposition

proposal = CodeProposal(
    source="def increment(value: int) -> int: return value + 1",
    export_name_tuple=("increment",),
)
function = runner.run(
    ScaffoldComposition(RunContract(type(None), RetainedFunction), lambda value: retain_function(proposal, export_name="increment")),
    None,
)
assert runner.run(ScaffoldComposition(RunContract(int, object), function), 6) == 7
assert runner.run(ScaffoldComposition(RunContract(int, object), function), 9) == 10
```

Retention never approves source or retains the originating run's grant.
Materialized references obtained through `function.materialize()` still expire with their run.
Ledger materializations in later runs link the selected export to its original `retained_definition_id` and remain generated provenance.
Private closure state and mutable defaults are run-local; pass persistent state explicitly.
The [retained text workflow](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/retained_text_workflow.py) uses a generation judgment once, then evaluates the same source in multiple fresh batch runs.

## Compose conversation judgments independently

`choose_conversation_step(input, bindings, completed_action, action_result)` returns one typed `ConversationStep` and its selected writes.
It neither runs the selected action nor publishes bindings.
`ConversationInput(task, context=...)` can carry application-selected values without a Session; its bounded pages are optional integration context.
`ConversationBindings` supplies the explicit read/write contract and any values retained between judgments.

The optional `converse` loop combines that judgment with `generation.propose_function` and `perform_conversation_action`.
A normal answer needs one judgment.
A request for a new function produces a `FunctionRequest`; the separate generation judgment returns an inert `CodeProposal`, and deterministic retention returns the callable to the next conversation step.
Applications can replace `choose_step`, `generate_function`, or `perform_action` with ordinary async functions.
They may also call those parts directly without the loop.
`propose_function` receives explicit context and dependency descriptions under a narrowed grant with no domain capability tools; generation does not execute or approve its candidate.
`sources` operations are also independent of conversation and use the current grant for their governed effects.

Session uses these same parts and adds binding publication, callable entry from Python cells, and operator lifecycle.
`build_conversation_composition(choose_step=..., generate_function=...)` replaces individual judgments while preserving Session integration.
Custom compositions declare trusted Natural helpers in their `function_definition_tuple` at run entry; this is exact callable identity evidence, not an additional grant or automatic approval.

## Optional fixed-loop composition

```python
import nighthawk as nh
from nighthawk.agent import Grant, PerimeterPolicy, RunHandle, execute_code_capability
from nighthawk.agent.runtime import Runner, bounded_resource_allocation
from nighthawk.agent.reference import run_reference_loop

runner = Runner(
    step_executor=nh.AgentStepExecutor(nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna")),
    perimeter_policy=PerimeterPolicy.NONE,
    resource_allocation_factory=lambda run_identity: bounded_resource_allocation(
        maximum_recursion_depth=3,
        maximum_executor_step_count=64,
    ),
    # The default executes in process; optional external confinement must confirm each dispatch.
)
handle = RunHandle()
result = run_reference_loop(
    "Summarize what a run ledger records.",
    runner=runner,
    run_entry_grant=Grant.of(execute_code_capability()),
    handle=handle,
)
print(result.summary)
for entry in handle.ledger.entries:
    print(entry.sequence, entry.event.kind)
```

## Runtime composition

Import `Runner` and service protocols from `nighthawk.agent.runtime`.
Configure the executor and host services on a Runner, then call `runner.run(composition, run_input, run_id=run_id, handle=handle)` for an application-defined composition.
The reference loop and callable examples also take `runner=runner`.

`ScaffoldComposition` returns its completed result synchronously.
`AsyncScaffoldComposition` returns an unstarted coroutine from `runner.run(...)`; awaiting it admits the run and returns its completed typed result.
Both compositions and `RunContract`/`RunHandle` are available from `nighthawk.agent.runtime`.
Direct callers may keep supplying a `RunHandle` to inspect the ledger or request termination, without creating a Session.
Every run receives a fresh allocation.
A run permitting perimeter effects requires an explicitly supplied audit store.
The [SQLite audit example](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/durable_audit.py) demonstrates committed attempt/completion records surviving process restart; storage location and deployment guarantees remain application-owned.
`InMemoryPerimeterAuditStore` is an explicit experimental choice and does not satisfy durability.
The [runtime obligations](runtime.md) describe the guarantees required from host services.

## Foreground Sessions

```python
from nighthawk.agent.session import CellInput, create_session

session = create_session(execution_model="openai-responses:gpt-6-luna")
try:
    execution = session.submit(CellInput("> samples = [1, 2, 3]"))
    assert execution.outcome is None
    assert execution.wait().status == "success"
    assert session.binding_name_to_value["samples"] == [1, 2, 3]
finally:
    assert session.close()
```

Python-only startup constructs no provider.
In an existing event loop, use `await execution.wait_async()`; synchronous `wait()` rejects unfinished work on a running loop.
Independent Sessions can progress cooperatively in caller-created tasks, while each Session admits only one unfinished cell.
The example terminal runs exactly one Session subprocess alongside its UI process so synchronous cells leave editing, history inspection, and its Status Line responsive.
Python, Natural, and generated code share that Session interpreter and its live objects; no per-dispatch execution process is created.
Python and its searchable palette share typed local, execution, history, evidence, and managed-background operations.
Operator `session.exit()` refuses while background work remains; a fresh request after confirmed cessation closes the Session.
The library leaves cwd, import lookup, the application's `__main__`, streams, and signal handlers under application ownership.
Model access begins at the first Natural step and requires the selected provider's optional dependency and credentials.
`execution_model` serves `execute` blocks; an optional `decision_model` such as `openai-decisions:gpt-6-luna` serves typed `decide` blocks, which fail without one.
The default configuration supports conversation, approved function calls, and generated dispatch in the same interpreter.
Limited Python audit checks reject observed unmediated perimeter effects without claiming a sandbox; optional configured confinement must confirm every dispatch.

## Documents

- [Principles](principles.md): purpose, identity, responsibility boundaries, and criteria for revising the design.
- [Architecture](architecture.md): current architectural commitments and rationale.
- [Runtime obligations](runtime.md): services and guarantees supplied by the host application.
- [Composition examples](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/README.md): discovery, concurrent work, learning, retention, and evaluation.
- [Sessions and terminal](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md): embedding and the optional interactive application.

## Development

Follow the repository's [development and verification commands](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/CONTRIBUTING.md#development-commands).
Core and Agent are installed together by `uv sync --all-extras --all-groups`; no package links are required.
The distribution checks exercise both from one installed wheel outside the checkout, without terminal dependencies.
Provider acceptance uses the explicit opt-in controls described in the [integration test instructions](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/CONTRIBUTING.md#integration-tests).

## Sessions and source reuse

Sessions retain ordinary Python bindings and support Natural judgments, approved function calls, and generated function declaration through the Harness.
Declared generated functions can be discovered and invoked in later runs with fresh authority; publication does not ratify their source.
Explicit retention and export preserve selected source or data without implicitly approving it or retaining a live run.
See [declared Session functions](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md#declared-session-functions), [source export](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md#retained-source-and-explicit-export), and [model configuration](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md#explicit-model-selection) for supported workflows and their limits.
