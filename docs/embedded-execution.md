# Embedded execution

Use `nighthawk.runtime.execute_step` or `execute_step_async` when a Python host selects trusted Natural text, binding names, and output types at invocation time.
For a fixed operation, prefer an ordinary `@nh.function` with meaningful parameters and a return annotation.
The embedded entry points serve hosts whose dynamic declarations must participate in Core validation and whose binding writes must complete inside the same step lifecycle.

Create a `StepDefinition` for the trusted program and its declarations, then call `execute_step(definition, *, name_to_value, write_binding)` or await `execute_step_async` with the same arguments.
The definition is immutable and reusable across invocations; it contains no execution state, namespace, or writer.
Each invocation resolves annotations and infers missing contracts in its own environment.

Both entry points use the active `nh.run()` and `nh.scope()` configuration, the ordinary executor, commit inspection, and terminal notifications.
They do not invoke `FunctionCallHandler`; `nh.function_call_scope(...)` alone does not admit or refuse embedded execution.
They return a resolved `Pass`, `Return`, `Break`, or `Continue` after writeback and terminal delivery.
The host interprets that control result; a final `Raise` instead propagates its constructed exception after terminal delivery.
Setting `is_in_loop=True` permits `Break` and `Continue`; deny frontmatter can further restrict the permitted results.

## Authorize the execution

Embedded programs, whether repository-managed or generated at runtime, follow the [trusted-program contract](specification.md#3-hard-constraints).
Apply the host's authorization policy to the program, its declarations, and its helpers at a host-controlled entry point before calling `execute_step` or `execute_step_async`; constructing a `StepDefinition` does not establish approval.
An existing host policy may authorize a class of revisions in advance.

## Explicit names and contracts

`name_to_value` is required, and `{}` is a valid empty environment.
Core snapshots its name-to-object associations during preparation; objects retain their identity and remain live.
Core does not automatically save outputs into that mapping.

The host supplies `StepDefinition` with the program body without the `execute` sentinel, separate `input_binding_names` and ordered `output_binding_names`, and an optional `binding_name_to_type` mapping.
Declarations must be ordered sequences of unique ASCII Python identifiers, excluding keywords.
A name may be declared for both reading and writing.
Definitions copy the declaration sequences and type mapping, preserving annotation object identity.
Malformed declarations and types for undeclared outputs raise `BindingDeclarationError` at construction, before any execution lifecycle starts.
These declarations are authoritative; the entry point does not extract declarations, expand includes, or interpolate Python expressions into source.
Hosts that compile trusted sources supply the resulting text and matching declarations.
Natural text and type expressions are trusted program structure; user requests and retrieved material belong in binding values.

Declared read names must exist in the explicit mapping, even if a builtin or scoped task reference has that name.
Missing reads fail preparation before executor admission.
Evaluation, discovery, prompt context, type references, and model-selected exception expressions use the explicit environment.
Ordinary lookup uses step working assignments, explicit Python names, builtins, then scoped task references.
Caller locals, globals, closure cells, and inherited parent working state are not implicit sources for an explicitly embedded step.
Scoped configuration, execution identity, oversight, capabilities, and lifecycle retain their usual inheritance rules.

Called Python functions retain their own closures and globals.
Nested Natural functions use their own Python frames and the active scope's explicit task references.
The explicit namespace and later parent temporaries do not become implicit inputs to those functions or their descendants.
Pass values as arguments, or use a Python helper to select references for `nh.scope(task_references=...)` before calling the child.
See the [synchronous and asynchronous sharing examples](runtime-configuration.md#selecting-shared-values-with-python).
This selects an execution environment without isolating Python objects or restricting the authority of called functions.

Omitting an output annotation selects the ordinary initial-value inference rule.
An explicit `object`, `Any`, or `None` remains an explicit contract.
Initial `None` without an annotation selects `object`; an annotation of `None` requires `None`.
String annotations and nested forward references resolve during preparation using the explicit environment, including builtins and scoped task references.
Contracts remain fixed throughout execution.

## Writeback and completion

The required `write_binding(name, value)` callback applies one validated, approved binding assignment.
It returns `None` or an awaitable completing that assignment.
Callbacks run sequentially in output declaration order; Core records a successful assignment only after its callback returns normally.
Both entry points accept synchronous or asynchronous writers; the synchronous entry point uses the ordinary bridge for awaitables.

When `StepCandidate.binding_name_to_value` is omitted or `nh.UNSET`, finalization selects initialized declared outputs.
Tracked working values take precedence over current Python values; uninitialized outputs and task-only references remain absent.
A supplied mapping selects exactly those outputs, including when it is empty; omitted names are neither supplemented nor validated for writeback.
Selected values must satisfy their output contracts before commit inspection.
Use `request.snapshot_output_bindings()` and ordinary Python dictionary composition to explicitly combine current output values with replacements.
An initial `Raise` selects no outputs and skips validation of pending working values.
Pairing `Raise` with an explicitly supplied nonempty output mapping is invalid for both `StepCandidate` and `Rewrite`.
A commit `Rewrite` with an explicitly supplied mapping replaces the selected outputs completely; rewriting to `{}` does not restore initial values.

These are separate facts:

1. An initial value exists.
2. A value was selected, validated, and approved for writeback.
3. The writer completed an assignment.

Only the third appears in `StepFinished.assigned_binding_name_to_value`.
Assigning the same value again still counts as an assignment.
If the second write fails, the first completed write remains recorded and effective.
An interrupted writer must finish its cancellation handling before control can reach terminal delivery.
If a writer mutates storage and then raises, Core cannot infer whether the assignment completed; that writer owns diagnosis and recovery for its partial effects.
This interface does not promise database transactions, rollback, retries, or durable storage.

This example uses the same executor with synchronous and asynchronous entry points to distinguish automatic, empty, explicit, and composed output selections.

<!-- runnable-example:output_selection -->
```py
import asyncio

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepDefinition, StepRequest, execute_step, execute_step_async


class Executor:
    def run_step(self, *, request: StepRequest) -> StepCandidate:
        selection = request.processed_natural_program
        if selection == "automatic":
            return StepCandidate(outcome=Pass())
        if selection == "empty":
            return StepCandidate(outcome=Pass(), binding_name_to_value={})
        if selection == "explicit":
            return StepCandidate(outcome=Pass(), binding_name_to_value={"first": 7})
        return StepCandidate(outcome=Pass(), binding_name_to_value={**request.snapshot_output_bindings(), "first": 7})


async def main() -> None:
    selection_to_binding_name_to_value = {
        "automatic": {"first": 1, "second": 2},
        "empty": {},
        "explicit": {"first": 7},
        "composed": {"first": 7, "second": 2},
    }
    with nh.run(Executor()):
        for asynchronous in (False, True):
            for selection, expected_binding_name_to_value in selection_to_binding_name_to_value.items():
                saved_name_to_value: dict[str, object] = {}
                definition = StepDefinition(selection, output_binding_names=("first", "second"))
                initial_name_to_value = {"first": 1, "second": 2}
                if asynchronous:
                    await execute_step_async(definition, name_to_value=initial_name_to_value, write_binding=saved_name_to_value.__setitem__)
                else:
                    execute_step(definition, name_to_value=initial_name_to_value, write_binding=saved_name_to_value.__setitem__)
                assert saved_name_to_value == expected_binding_name_to_value


asyncio.run(main())
print("automatic=2; empty=0; explicit=1; composed=2; sync_and_async=True")
```
<!-- /runnable-example:output_selection -->

Terminal delivery happens after writeback or failure, with the usual `StepCompleted`, `StepRaised`, `StepFailed`, or `StepInterrupted` record.
Notification failure propagates without replaying execution or notification and without undoing completed writes.
The optional `source_location` labels the trusted program for diagnostics; its default is `<embedded>:0`.
Each invocation receives a distinct step execution Id.
See the [terminal record contract](specification.md#104-terminal-delivery-and-host-ledgers) for the fields Core supplies and their limits.

## Record revision evidence

When an application needs revision provenance for assessment or reuse, retain the program source, declarations, relevant helper and dependency versions, and execution conditions in host records.
Connect those records to the authorization, step execution Ids, and evaluation results so an assessment can be traced to the revision that ran.
Choose enough detail to distinguish changes relevant to the assessment, including helper-only revisions that leave Natural text unchanged.
The execution Id and `source_location` support correlation and diagnostics; the terminal record does not capture a complete program revision.

## Synchronous example

This example runs without credentials or external services.
The host explicitly saves into its mapping, and terminal delivery observes the saved value.

<!-- runnable-example:embedded_sync -->
```py
import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFinished, StepLifecycle
from nighthawk.runtime import Pass, StepDefinition, execute_step
from nighthawk.testing import ScriptedExecutor, pass_response

name_to_value = {
    "observations": ["The trial passed."],
    "summary": "",
}
events: list[StepFinished] = []


def finished(event: StepFinished) -> None:
    assert name_to_value["summary"] == "The trial passed."
    events.append(event)


definition = StepDefinition(
    "Summarize <observations> in <:summary>.",
    input_binding_names=("observations",),
    output_binding_names=("summary",),
    binding_name_to_type={"summary": str},
    source_location="trusted_summary:1",
)
executor = ScriptedExecutor([pass_response(binding_name_to_value={"summary": "The trial passed."})])
with nh.run(executor), nh.scope(lifecycle=StepLifecycle(finished)):
    outcome = execute_step(definition, name_to_value=name_to_value, write_binding=name_to_value.__setitem__)

assert outcome == Pass()
assert len(events) == 1 and isinstance(events[0], StepCompleted)
assert events[0].assigned_binding_name_to_value == {"summary": "The trial passed."}
print("summary_saved=True; terminal_after_save=True")
```
<!-- /runnable-example:embedded_sync -->

## Asynchronous example

Here the same mechanism uses different binding names and an application-defined output type.
The host saves to a separate mapping; the input namespace is not a write destination.
The asynchronous writer is awaited before delivery.

<!-- runnable-example:embedded_async -->
```py
import asyncio

from pydantic import BaseModel

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFinished, StepLifecycle
from nighthawk.runtime import Pass, StepDefinition, execute_step_async
from nighthawk.testing import ScriptedExecutor, pass_response


class Selection(BaseModel):
    label: str


async def main() -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    async def write(name: str, value: object) -> None:
        await asyncio.sleep(0)
        saved_name_to_value[name] = value

    def finished(event: StepFinished) -> None:
        assert saved_name_to_value == {"selected": Selection(label="alpha")}
        events.append(event)

    definition = StepDefinition(
        "Choose a label from <candidates> and write <:selected>.",
        input_binding_names=("candidates",),
        output_binding_names=("selected",),
        binding_name_to_type={"selected": "Selection"},
        source_location="trusted_selection:1",
    )
    executor = ScriptedExecutor([pass_response(binding_name_to_value={"selected": {"label": "alpha"}})])
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(finished)):
        outcome = await execute_step_async(definition, name_to_value={"candidates": ["alpha", "beta"], "Selection": Selection}, write_binding=write)
    assert outcome == Pass()
    assert len(events) == 1 and isinstance(events[0], StepCompleted)
    assert events[0].assigned_binding_name_to_value == saved_name_to_value
    print("selected_type=Selection; terminal_after_save=True")


asyncio.run(main())
```
<!-- /runnable-example:embedded_async -->

Replace the test executor with a configured production executor to delegate judgment to a model.
The deterministic examples verify execution and storage mechanics, not the quality of a model's summary or selection.

## Reusing binding processing

`infer_binding_types(binding_name_to_type, initial_name_to_value)` returns a new mapping without changing its inputs.
Use the public `MISSING_ANNOTATION` marker for existing declarations that need inference.
It does not create declarations for extra names found in the initial-value mapping.
Container inference uses the runtime container class without inspecting contents.

`validate_output_bindings(binding_name_to_value, binding_name_to_type, output_binding_names)` validates only the supplied candidates and returns a new mapping.
It rejects undeclared output names before invoking any validator.
Missing contracts and `MISSING_ANNOTATION` skip individual validation; explicit `None` is validated.
Supply resolved Python annotations to this helper; environment-dependent type resolution belongs to step preparation.
Validation requires an ordered sequence of unique binding names and follows that order.
Malformed declarations, undeclared candidates, and annotations for undeclared outputs raise `BindingDeclarationError` before invoking validators.
Missing candidates are not supplemented, and neither input mappings nor storage destinations receive converted values.

`BindingError` derives from `NighthawkError` and is the base for `BindingDeclarationError` and `BindingValidationError`.
`BindingDeclarationError.binding_names` identifies offending names when available.
`BindingValidationError.binding_name` identifies a failed value, and `original_exception` and `__cause__` retain the original validator failure.
The ordinary step lifecycle continues to expose the original validator exception in `StepFailed.original_exception`, with binding-specific diagnostics in `ExecutionError`.
Validation calls retain no failure state between invocations.
Neither helper deep-copies objects or reverses validator effects.

```py
from nighthawk.runtime import MISSING_ANNOTATION, infer_binding_types, validate_output_bindings

binding_name_to_type = infer_binding_types({"count": MISSING_ANNOTATION}, {"count": 0})
candidate_name_to_value = {"count": "12"}
validated_name_to_value = validate_output_bindings(candidate_name_to_value, binding_name_to_type, ("count", "label"))
assert validated_name_to_value == {"count": 12}
assert candidate_name_to_value == {"count": "12"}
```
