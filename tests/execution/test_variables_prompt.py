import builtins
import functools
import textwrap

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_executor import build_user_prompt


class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self):
        self.seen_prompts: list[str] = []

    def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
        from nighthawk.runtime.step_contract import PassStepOutcome

        self.seen_prompts.append(user_prompt)
        assert deps is not None
        _ = kwargs
        return _FakeRunResult(PassStepOutcome(kind="pass"))


class _CallableValue:
    def __call__(self, item):  # type: ignore[no-untyped-def]
        return item


class _BoundMethodCarrier:
    def transform(self, item):  # type: ignore[no-untyped-def]
        return item


class _SignatureUnavailableCallable:
    @property
    def __signature__(self):  # type: ignore[no-untyped-def]
        raise ValueError("signature unavailable")

    def __call__(self, *items):  # type: ignore[no-untyped-def]
        return items


G = 1
_DEFAULT_EXECUTOR_CONFIGURATION = nh.StepExecutorConfiguration()


def _global_helper(required, optional=10, *, keyword="x"):  # type: ignore[no-untyped-def]
    _ = keyword
    return required + optional


def _global_typed_helper(
    number: int,
    names: list[str],
    *,
    enabled: bool = True,
) -> dict[str, int]:
    _ = names
    _ = enabled
    return {"number": number}


def _build_step_context(*, python_globals: dict[str, object], python_locals: dict[str, object]) -> StepContext:
    return StepContext(
        step_id="test",
        step_globals=python_globals,
        step_locals=python_locals,
        binding_commit_targets=set(),
    )


def _build_user_prompt_text(*, processed_natural_program: str, step_context: StepContext) -> str:
    return build_user_prompt(
        processed_natural_program=processed_natural_program,
        step_context=step_context,
        configuration=_DEFAULT_EXECUTOR_CONFIGURATION,
    )


def _locals_section(prompt: str) -> str:
    return prompt.split("<<<NH:LOCALS>>>\n", 1)[1].split("\n<<<NH:END_LOCALS>>>", 1)[0]


def _globals_section(prompt: str) -> str:
    return prompt.split("<<<NH:GLOBALS>>>\n", 1)[1].split("\n<<<NH:END_GLOBALS>>>", 1)[0]


def test_user_prompt_renders_globals_and_locals_for_references(tmp_path):
    _ = tmp_path
    agent = _FakeAgent()
    with nh.run(nh.AgentStepExecutor.from_agent(agent=agent)):
        a = 1.0

        @nh.natural_function
        def f() -> None:
            x = 10
            """natural
            Say hi.
            """

            y = "hello"
            """natural
            <a><G>
            """

            _ = x
            _ = y

        f()

        _ = a

    assert agent.seen_prompts[0] == textwrap.dedent(
        """\
        <<<NH:PROGRAM>>>
        Say hi.

        <<<NH:END_PROGRAM>>>

        <<<NH:LOCALS>>>
        a: float = 1.0
        x: int = 10
        <<<NH:END_LOCALS>>>

        <<<NH:GLOBALS>>>

        <<<NH:END_GLOBALS>>>
        """
    )
    assert agent.seen_prompts[1] == textwrap.dedent(
        """\
        <<<NH:PROGRAM>>>
        <a><G>

        <<<NH:END_PROGRAM>>>

        <<<NH:LOCALS>>>
        a: float = 1.0
        x: int = 10
        y: str = "hello"
        <<<NH:END_LOCALS>>>

        <<<NH:GLOBALS>>>
        G: int = 1
        <<<NH:END_GLOBALS>>>
        """
    )


def test_locals_section_renders_plain_python_function_signature() -> None:
    def local_function(required, optional=10, *, keyword="x"):  # type: ignore[no-untyped-def]
        _ = keyword
        return required + optional

    step_context = _build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"local_function": local_function},
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_section = _locals_section(prompt)
    assert "local_function: (required, optional=10, *, keyword='x')" in locals_section
    assert "# intent:" not in locals_section
    assert "<function>" not in locals_section


def test_locals_section_renders_lambda_builtin_and_bound_method_values() -> None:
    lambda_value = lambda number: number  # noqa: E731
    bound_method = _BoundMethodCarrier().transform

    step_context = _build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "lambda_value": lambda_value,
            "builtin_length": len,
            "bound_method": bound_method,
        },
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_section = _locals_section(prompt)
    assert "lambda_value: (number)" in locals_section
    assert "builtin_length: (obj, /)  # intent:" in locals_section
    assert "bound_method: (item)" in locals_section
    assert "<function>" not in locals_section
    assert "builtin_function_or_method" not in locals_section
    assert "method = " not in locals_section


def test_locals_section_renders_callable_instance_and_partial_function() -> None:
    callable_instance = _CallableValue()
    partially_applied_helper = functools.partial(_global_helper, 1)

    step_context = _build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "callable_instance": callable_instance,
            "partially_applied_helper": partially_applied_helper,
        },
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_section = _locals_section(prompt)
    assert "callable_instance: (item)" in locals_section
    assert "partially_applied_helper: (optional=10, *, keyword='x')" in locals_section
    assert "partial = " not in locals_section
    assert "# intent:" not in locals_section
    assert "<function>" not in locals_section
    assert "(*args, **kwargs)" not in locals_section


def test_globals_section_renders_referenced_global_function_signature() -> None:
    step_context = _build_step_context(
        python_globals={
            "__builtins__": builtins,
            "global_helper": _global_helper,
        },
        python_locals={},
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Use <global_helper> in this block.",
        step_context=step_context,
    )

    globals_section = _globals_section(prompt)
    assert "global_helper: (required, optional=10, *, keyword='x')" in globals_section
    assert "# intent:" not in globals_section
    assert "<function>" not in globals_section


def test_locals_section_renders_typed_function_argument_and_return_annotations() -> None:
    def local_typed_function(
        number: int,
        names: list[str],
        *,
        enabled: bool = True,
    ) -> dict[str, int]:
        _ = names
        _ = enabled
        return {"number": number}

    step_context = _build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"local_typed_function": local_typed_function},
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_section = _locals_section(prompt)
    assert "local_typed_function: (number: int, names: list[str], *, enabled: bool = True) -> dict[str, int]" in locals_section
    assert "# intent:" not in locals_section
    assert "<function>" not in locals_section


def test_globals_section_renders_referenced_typed_function_argument_and_return_annotations() -> None:
    step_context = _build_step_context(
        python_globals={
            "__builtins__": builtins,
            "global_typed_helper": _global_typed_helper,
        },
        python_locals={},
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Use <global_typed_helper> in this block.",
        step_context=step_context,
    )

    globals_section = _globals_section(prompt)
    assert "global_typed_helper: (number: int, names: list[str], *, enabled: bool = True) -> dict[str, int]" in globals_section
    assert "# intent:" not in globals_section
    assert "<function>" not in globals_section


def test_signature_unavailable_callable_renders_non_invocable_marker() -> None:
    step_context = _build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"opaque_callable": _SignatureUnavailableCallable()},
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_section = _locals_section(prompt)
    assert "opaque_callable: <callable; signature-unavailable>" in locals_section
    assert "# intent:" not in locals_section
    assert "(*args, **kwargs)" not in locals_section


def test_callable_signature_collision_renders_disambiguation_hint_for_each_callable() -> None:
    def open_customer(customer_id):  # type: ignore[no-untyped-def]
        return customer_id

    def open_order(customer_id):  # type: ignore[no-untyped-def]
        return customer_id

    step_context = _build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "open_customer": open_customer,
            "open_order": open_order,
        },
    )

    prompt = _build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_section = _locals_section(prompt)
    assert "open_customer: (customer_id)  # disambiguation: use open_customer" in locals_section
    assert "open_order: (customer_id)  # disambiguation: use open_order" in locals_section
