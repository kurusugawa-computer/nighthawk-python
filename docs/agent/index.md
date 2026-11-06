# Agent Parts and Harness

Agent Parts are reusable judgments and operations; the Harness supplies host-owned execution mechanisms.
AI agents combine Natural judgment with Python computation and actions to construct capabilities, carry out work, assess results and effects, and improve later attempts.
Python defines execution structure even when an agent authors that structure during execution.

The library supports multiple execution strategies, including recursive code generation, procedure discovery, explicit learning, and application-defined compositions.
No single agent loop or conversation framework is required.
Reusable Sessions support embedding, and an optional repository-local terminal demonstrates an interactive application.

Read [Quickstart](../quickstart.md) for setup. The [Agent principles](principles.md) apply the [Nighthawk philosophy](../philosophy.md) to Harness and Agent Parts responsibilities.

## Responsibility boundaries

Core supplies Natural/Python execution semantics; Harness admits and records governed execution; Parts compose judgments and domain operations.
Their packages and dependency direction are defined in [Architecture](../architecture.md).
Parts range from an individual judgment to a complete strategy and combine through ordinary Python calls.

Applications supply goals, domain actions, policy, storage, runtime services, source approval, and capability improvement workflows.

## What it provides

- Grants: immutable name-to-reference mappings that bound what a Natural execution or generated execution may reach.
  Capabilities carry a stable name, a typed signature, an authority boundary, and a declared effect footprint.
- Host-owned dispatch: generated Python source enters execution only as a validated `CodeProposal` through `dispatch()` or the granted `execute_code` capability.
  Materialized definitions get stable identities and are re-entered through host mediation on every call.
- A run ledger: an append-only typed record of run entry, `execute` and `decide` Natural execution outcomes, dispatches and validated invocation inputs, definitions, capability requests, perimeter effects, and run end.
- A platform contract: opaque resource allocation and perimeter audit storage are supplied by the host; external confinement and forced branch termination are optional services whose guarantees the library never fabricates.
- An optional fixed-loop composition in `nighthawk.parts.exploration`, using the same Harness mechanisms as other strategies.
- Domain capabilities in `nighthawk.parts.commands`, `files`, `web`, `images`, `workspace`, and `artifacts`, composed explicitly into grants.

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
| Inspect causal observations or resolve an exact citation | `harness.evidence.analyze_run`, `parts.evidence.project_evidence`, `harness.evidence.resolve_evidence` |
| Derive explicit lessons and inert candidates | `learning.derive_learning`, `learning.format_candidate_review` |
| Admit and account for a Python composition | `harness.runner.Runner`, `harness.run_contract.ScaffoldComposition`, `harness.run_contract.AsyncScaffoldComposition` |

These parts are ordinary functions and values. Applications choose which to combine, where to retain results, and what evidence is sufficient for reuse.
When a Natural block constructs a capability's structured request, expose the request schema explicitly through ordinary Python.
For example, import `FilePatchRequest` from `nighthawk.parts.files` and instruct the block to call `<FilePatchRequest.model_json_schema>` before constructing the patch.
Static callable descriptions are best-effort and do not evaluate Pydantic's deferred constructor signatures; a type name alone does not describe its fields.
The [file-patch integration test](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/tests/integration/harness/test_file_patch.py) demonstrates this composition with a writable-root grant and recorded effects.

For example, this body materializes one generated function and invokes it through the same host mechanisms used by other strategies:

```python
from nighthawk.harness.proposals import CodeProposal
from nighthawk.parts.procedures.catalog import ProcedureCatalog
from nighthawk.parts.procedures import materialize_procedure, selected_procedure
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition


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
The [reference applications](composition.md) combine Natural selection, generation, concurrent work, explicit learning, evaluation, and continuation.
Their evaluators compare complete results with explicit Python baselines, resolve observed input/result evidence, and distinguish scripted accounting from provider observations.

## Reuse generated functions across runs

`retain_function(proposal, export_name=...)` validates and materializes a definition-only proposal in the active run without calling its body.
Its `RetainedFunction` keeps the exact proposal and observed definition identity.
Applications retain that ordinary Python value wherever they choose; no catalog, Session, storage backend, or approval workflow is required.
Calling it in a later run rematerializes its source under that run's current grant.
Each run shares one materialization per retained callable identity, while every invocation resolves dependencies and passes through normal dispatch admission again.
For an asynchronous function, materialization and invocation begin when its returned coroutine is awaited.

```python
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.generated import RetainedFunction, retain_function
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition

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
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.harness.grants import Grant
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.dispatch import execute_code_capability
from nighthawk.harness.runner import Runner
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.parts.exploration import exploration_composition, ExplorationTask

runner = Runner(
    step_executor=AgentStepExecutor(StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna")),
    perimeter_policy=PerimeterPolicy.NONE,
    resource_allocation_factory=lambda run_identity: bounded_resource_allocation(
        maximum_recursion_depth=3,
        maximum_executor_step_count=64,
    ),
    # The default executes in process; optional external confinement must confirm each dispatch.
)
handle = RunHandle()
result = runner.run(
    exploration_composition(run_entry_grant=Grant.of(execute_code_capability())),
    ExplorationTask(task="Summarize what a run ledger records."),
    handle=handle,
)
print(result.summary)
for entry in handle.ledger.entries:
    print(entry.sequence, entry.event.kind)
```

## Runtime composition

Configure the executor and host services on a Runner, then call `runner.run(composition, run_input, run_id=run_id, handle=handle)` for an application-defined composition.
The exploration composition and callable examples use the same Runner entry point.

`ScaffoldComposition` returns its completed result synchronously.
`AsyncScaffoldComposition` returns an unstarted coroutine from `runner.run(...)`; awaiting it admits the run and returns its completed typed result.
Callers may supply a `RunHandle` to inspect the ledger or request termination, without creating a Session.
Every run receives a fresh allocation.
The [runtime obligations](runtime.md) describe the guarantees required from host services, including the audit store that a run permitting perimeter effects requires.
The [SQLite audit example](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/durable_audit.py) demonstrates committed attempt/completion records surviving process restart.

## Foreground Sessions

`nighthawk.session.create_session(execution_model=...)` supplies lazy provider configuration for a persistent Python namespace.
Submit a `CellInput`, then drive it with `execution.wait()` or `await execution.wait_async()` on the caller's event loop.
Python-only cells construct no model provider.
Use a `SessionManager` when the application supplies its own Runner and conversation composition.

[Embedding Sessions](sessions.md) covers lifecycle, typed writes, source approval, generated functions, history, and model selection, with a runnable Python-only example.
The [terminal application](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/repl/README.md) adds an independent UI process and operator controls.

## Development

Follow the repository's [development and verification commands](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/CONTRIBUTING.md#development-commands).
Provider acceptance uses the explicit opt-in controls described in the [integration test instructions](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/CONTRIBUTING.md#integration-tests).
