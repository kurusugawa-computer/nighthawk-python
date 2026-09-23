"""Execute trusted Natural programs with host-owned bindings and writeback."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from inspect import isawaitable
from keyword import iskeyword

from .async_bridge import run_coroutine_synchronously
from .runner import Runner
from .scoping import get_step_executor
from .step_result import StepResult

type BindingWriter = Callable[[str, object], Awaitable[None] | None]


def _validate_declarations(input_binding_names: Sequence[str], output_binding_names: Sequence[str]) -> None:
    for names in (input_binding_names, output_binding_names):
        if isinstance(names, str):
            raise TypeError("Binding declarations must be a sequence of names, not a string")
        if any(not isinstance(name, str) or not name.isidentifier() or iskeyword(name) for name in names):
            raise ValueError("Binding names must be Python identifiers, excluding keywords")
        if len(set(names)) != len(names):
            raise ValueError("Binding declarations must not contain duplicate names")


async def _await_write(result: Awaitable[None]) -> None:
    await result


def execute_step(
    natural_program: str,
    *,
    name_to_value: Mapping[str, object],
    input_binding_names: Sequence[str] = (),
    output_binding_names: Sequence[str] = (),
    binding_name_to_type: Mapping[str, object] | None = None,
    write_binding: BindingWriter,
    return_annotation: object = object,
    is_in_loop: bool = False,
    source_location: str = "<embedded>:0",
) -> StepResult:
    """Execute and save one step under the active run and scope.

    The trusted program is a body without the ``natural`` sentinel; deny frontmatter is supported.
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
    with runner.execution(source_location=source_location):
        _validate_declarations(input_binding_names, output_binding_names)
        input_binding_names = tuple(input_binding_names)
        output_binding_names = tuple(output_binding_names)
        if not isinstance(name_to_value, Mapping):
            raise TypeError("name_to_value must be an explicit mapping")
        if not callable(write_binding):
            raise TypeError("write_binding must be callable")
        envelope = runner.run_step(
            natural_program,
            list(input_binding_names),
            list(output_binding_names),
            dict(binding_name_to_type or {}),
            return_annotation,
            is_in_loop,
            name_to_value=name_to_value,
        )
        for name in output_binding_names:
            if name not in envelope["bindings"]:
                continue
            value = envelope["bindings"][name]
            result = write_binding(name, value)
            if isawaitable(result):
                run_coroutine_synchronously(partial(_await_write, result))
            runner.record_assignment(name, value)
    return envelope["outcome"]


async def execute_step_async(
    natural_program: str,
    *,
    name_to_value: Mapping[str, object],
    input_binding_names: Sequence[str] = (),
    output_binding_names: Sequence[str] = (),
    binding_name_to_type: Mapping[str, object] | None = None,
    write_binding: BindingWriter,
    return_annotation: object = object,
    is_in_loop: bool = False,
    source_location: str = "<embedded>:0",
) -> StepResult:
    """Asynchronous counterpart of :func:`execute_step` with the same environment and writeback contract.

    Each writer is awaited before recording that assignment or starting the next write.
    Cancellation propagates through the writer and its cleanup before terminal notification.
    Host writers own any effects performed before raising or before cancellation.
    """
    runner = Runner(get_step_executor())
    async with runner.execution_async(source_location=source_location):
        _validate_declarations(input_binding_names, output_binding_names)
        input_binding_names = tuple(input_binding_names)
        output_binding_names = tuple(output_binding_names)
        if not isinstance(name_to_value, Mapping):
            raise TypeError("name_to_value must be an explicit mapping")
        if not callable(write_binding):
            raise TypeError("write_binding must be callable")
        envelope = await runner.run_step_async(
            natural_program,
            list(input_binding_names),
            list(output_binding_names),
            dict(binding_name_to_type or {}),
            return_annotation,
            is_in_loop,
            name_to_value=name_to_value,
        )
        for name in output_binding_names:
            if name not in envelope["bindings"]:
                continue
            value = envelope["bindings"][name]
            result = write_binding(name, value)
            if isawaitable(result):
                await result
            runner.record_assignment(name, value)
    return envelope["outcome"]
