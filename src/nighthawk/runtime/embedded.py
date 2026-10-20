"""Execute trusted Natural programs with host-owned bindings and writeback."""

from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from inspect import isawaitable

from .async_bridge import run_coroutine_synchronously
from .runner import Runner
from .scoping import get_step_executor
from .step_definition import StepDefinition
from .step_result import StepResult

type BindingWriter = Callable[[str, object], Awaitable[None] | None]


async def _await_write(result: Awaitable[None]) -> None:
    await result


def execute_step(
    definition: StepDefinition,
    *,
    name_to_value: Mapping[str, object],
    write_binding: BindingWriter,
) -> StepResult:
    """Execute and save one step under the active run and scope.

    The trusted program is a body without the ``execute`` sentinel; deny frontmatter is supported.
    Declarations are authoritative metadata supplied by the host, without source interpolation or include expansion.
    The explicit namespace is shallow-copied during preparation; contained objects retain identity.
    Declared reads must exist there, even when a builtin or scoped task reference has the same name.
    Missing output annotations use initial-value inference; explicit annotations, including None, are preserved.
    String and nested forward annotations resolve in the explicit environment.

    ``write_binding(name, value)`` saves a validated, approved output.
    It may return an awaitable and must complete the assignment before normal return.
    Core calls it in declaration order, records each successful return, and stops on failure or interruption.
    The namespace itself is not automatically updated.
    Writes that complete before a later failure remain recorded; no rollback or retry is performed.
    A writer that applies an effect and then raises cannot report that effect as a successful assignment.

    Terminal notification finishes before the control result is returned.
    Return, Break, and Continue are values for the host to dispatch; a final Raise propagates its exception after notification.
    Synchronous executor and writer bodies run on the caller thread; awaitables use the usual synchronous bridge.
    """
    runner = Runner(get_step_executor())
    with runner.execution(source_location=definition.source_location):
        if not isinstance(name_to_value, Mapping):
            raise TypeError("name_to_value must be an explicit mapping")
        if not callable(write_binding):
            raise TypeError("write_binding must be callable")
        envelope = runner.run_step(
            definition.natural_program,
            list(definition.input_binding_names),
            list(definition.output_binding_names),
            dict(definition.binding_name_to_type),
            definition.return_annotation,
            definition.is_in_loop,
            name_to_value=name_to_value,
            block_kind=definition.block_kind,
        )
        for name in definition.output_binding_names:
            if name not in envelope["bindings"]:
                continue
            value = envelope["bindings"][name]
            result = write_binding(name, value)
            if isawaitable(result):
                run_coroutine_synchronously(partial(_await_write, result))
            runner.record_assignment(name, value)
    return envelope["outcome"]


async def execute_step_async(
    definition: StepDefinition,
    *,
    name_to_value: Mapping[str, object],
    write_binding: BindingWriter,
) -> StepResult:
    """Asynchronous counterpart of :func:`execute_step` with the same environment and writeback contract.

    Each writer is awaited before recording that assignment or starting the next write.
    Cancellation propagates through the writer and its cleanup before terminal notification.
    Host writers own any effects performed before raising or before cancellation.
    """
    runner = Runner(get_step_executor())
    async with runner.execution_async(source_location=definition.source_location):
        if not isinstance(name_to_value, Mapping):
            raise TypeError("name_to_value must be an explicit mapping")
        if not callable(write_binding):
            raise TypeError("write_binding must be callable")
        envelope = await runner.run_step_async(
            definition.natural_program,
            list(definition.input_binding_names),
            list(definition.output_binding_names),
            dict(definition.binding_name_to_type),
            definition.return_annotation,
            definition.is_in_loop,
            name_to_value=name_to_value,
            block_kind=definition.block_kind,
        )
        for name in definition.output_binding_names:
            if name not in envelope["bindings"]:
                continue
            value = envelope["bindings"][name]
            result = write_binding(name, value)
            if isawaitable(result):
                await result
            runner.record_assignment(name, value)
    return envelope["outcome"]
