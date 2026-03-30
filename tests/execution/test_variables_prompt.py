import builtins
import functools
import textwrap
from typing import Literal

import nighthawk as nh
from nighthawk.resilience import fallback
from tests.execution.prompt_test_helpers import FakeAgent, build_step_context, build_user_prompt_text, globals_section, locals_section


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


def test_user_prompt_renders_globals_and_locals_for_references(tmp_path):
    _ = tmp_path
    agent = FakeAgent()
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

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"local_function": local_function},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "local_function: (required, optional=10, *, keyword='x')" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_locals_section_renders_lambda_builtin_and_bound_method_values() -> None:
    lambda_value = lambda number: number  # noqa: E731
    bound_method = _BoundMethodCarrier().transform

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "lambda_value": lambda_value,
            "builtin_length": len,
            "bound_method": bound_method,
        },
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "lambda_value: (number)" in locals_text
    assert "builtin_length: (obj, /)  # " in locals_text
    assert "bound_method: (item)" in locals_text
    assert "<function>" not in locals_text
    assert "builtin_function_or_method" not in locals_text
    assert "method = " not in locals_text


def test_locals_section_renders_callable_instance_and_partial_function() -> None:
    callable_instance = _CallableValue()
    partially_applied_helper = functools.partial(_global_helper, 1)

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "callable_instance": callable_instance,
            "partially_applied_helper": partially_applied_helper,
        },
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "callable_instance: (item)" in locals_text
    assert "partially_applied_helper: (optional=10, *, keyword='x')" in locals_text
    assert "partial = " not in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text
    assert "(*args, **kwargs)" not in locals_text


def test_globals_section_renders_referenced_global_function_signature() -> None:
    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "global_helper": _global_helper,
        },
        python_locals={},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Use <global_helper> in this block.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "global_helper: (required, optional=10, *, keyword='x')" in globals_text
    assert "# " not in globals_text
    assert "<function>" not in globals_text


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

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"local_typed_function": local_typed_function},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "local_typed_function: (number: int, names: list[str], *, enabled: bool = True) -> dict[str, int]" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_globals_section_renders_referenced_typed_function_argument_and_return_annotations() -> None:
    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "global_typed_helper": _global_typed_helper,
        },
        python_locals={},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Use <global_typed_helper> in this block.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "global_typed_helper: (number: int, names: list[str], *, enabled: bool = True) -> dict[str, int]" in globals_text
    assert "# " not in globals_text
    assert "<function>" not in globals_text


def test_signature_unavailable_callable_renders_non_invocable_marker() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"opaque_callable": _SignatureUnavailableCallable()},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "opaque_callable: <callable; signature-unavailable>" in locals_text
    assert "# " not in locals_text
    assert "(*args, **kwargs)" not in locals_text


def test_callable_signature_collision_renders_disambiguation_hint_for_each_callable() -> None:
    def open_customer(customer_id):  # type: ignore[no-untyped-def]
        return customer_id

    def open_order(customer_id):  # type: ignore[no-untyped-def]
        return customer_id

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "open_customer": open_customer,
            "open_order": open_order,
        },
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "open_customer: (customer_id)  # disambiguation: use open_customer" in locals_text
    assert "open_order: (customer_id)  # disambiguation: use open_order" in locals_text


def test_locals_section_renders_pep695_type_alias_in_function_signature() -> None:
    type T = Literal["A", "B", "C"]  # pyright: ignore[reportGeneralTypeIssues]

    def f(t: T) -> None:
        _ = t

    def g(literal_value: Literal["A", "B", "C"]) -> None:
        _ = literal_value

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"T": T, "f": f, "g": g},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "T: type = typing.Literal['A', 'B', 'C']" in locals_text
    assert "f: (t: T) -> None" in locals_text
    assert "g: (literal_value: Literal['A', 'B', 'C']) -> None" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_globals_section_includes_type_alias_from_local_callable_return_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check": check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check the text.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text

    locals_text = locals_section(prompt)
    assert "check: (text: str) -> Labels" in locals_text


def test_globals_section_includes_type_alias_from_local_callable_parameter_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def process(labels: Labels) -> str:
        _ = labels
        return ""

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"process": process},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Process.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text


def test_globals_section_includes_type_alias_from_generic_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def batch_check(texts: list[str]) -> list[Labels]:
        _ = texts
        return []

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"batch_check": batch_check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Batch check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text


def test_globals_section_includes_type_alias_from_nested_generic_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def deep_check(data: list[dict[str, Labels]]) -> None:
        _ = data

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"deep_check": deep_check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Deep check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text


def test_globals_section_includes_type_alias_from_global_callable_signature() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def classify(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels, "classify": classify},
        python_locals={},
        input_binding_names=["classify"],
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Classify the email by <classify> function.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text
    assert "classify: (text: str) -> Labels" in globals_text


def test_globals_section_excludes_builtin_types_from_callable_signature() -> None:
    def plain(text: str, count: int) -> list[str]:
        _ = text, count
        return []

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"plain": plain},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Run plain.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert globals_text.strip() == ""


def test_type_alias_in_locals_is_not_duplicated_in_globals_section() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check": check, "Labels": Labels},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels" not in globals_text

    locals_text = locals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in locals_text


def test_globals_section_deduplicates_type_alias_from_multiple_callables() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check_a(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    def check_b(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check_a": check_a, "check_b": check_b},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check both.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert globals_text.count("Labels:") == 1


def test_type_alias_in_globals_unreferenced_by_signature_is_not_discovered() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> str:
        _ = text
        return ""

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check": check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels" not in globals_text


def test_type_alias_not_in_globals_is_not_discovered() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"check": check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels" not in globals_text


def test_locals_section_renders_fallback_composed_function_signature() -> None:
    def primary(x: str) -> str:
        return f"primary:{x}"

    def backup(x: str) -> str:
        return f"backup:{x}"

    composed = fallback(primary, backup)

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"composed": composed},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "composed: (x: str) -> str" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_locals_section_renders_fallback_merged_return_type_signature() -> None:
    def primary(x: str) -> str:
        return f"primary:{x}"

    def backup(x: str) -> int:
        return len(f"backup:{x}")

    composed = fallback(primary, backup)

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"composed": composed},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "composed: (x: str) -> str | int" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text
