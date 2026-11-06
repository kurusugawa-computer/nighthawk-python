from __future__ import annotations

import pytest
from pydantic import ValidationError

from nighthawk.harness._source import SourceRejectedError, analyze_generated_source
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration


def _analyze(source: str, **overrides: object) -> None:
    arguments: dict[str, object] = {"source": source, "export_name_tuple": ("f",)}
    arguments.update(overrides)
    proposal = CodeProposal(**arguments)  # type: ignore[arg-type]
    analyze_generated_source(proposal, filename="<test>")


def _rejection_reason(source: str, **overrides: object) -> str:
    with pytest.raises(SourceRejectedError) as captured:
        _analyze(source, **overrides)
    return captured.value.reason


def test_valid_definition_only_source_passes() -> None:
    _analyze("def f(x: int) -> int:\n    return _helper(x) + 1\n\ndef _helper(x: int) -> int:\n    return x * 2\n")


def test_syntax_error_is_rejected() -> None:
    assert _rejection_reason("def f(:\n") == "source_syntax_error"


@pytest.mark.parametrize(
    "call",
    ["exec('x')", "eval('x')", "compile('x', 'f', 'exec')", "__import__('os')", "importlib.import_module('os')"],
)
def test_raw_dynamic_execution_is_rejected(call: str) -> None:
    source = f"import importlib\n\ndef f() -> object:\n    return {call}\n"
    assert _rejection_reason(source) == "raw_dynamic_execution"


@pytest.mark.parametrize(
    "source",
    [
        "print('side effect')\n\ndef f() -> int:\n    return 1\n",
        "if True:\n    pass\n\ndef f() -> int:\n    return 1\n",
        "value = compute()\n\ndef f() -> int:\n    return value\n",
        "class _Helper:\n    registry = build()\n\ndef f() -> int:\n    return 1\n",
        "for i in range(3):\n    pass\n\ndef f() -> int:\n    return 1\n",
    ],
)
def test_materialization_effects_are_rejected(source: str) -> None:
    assert _rejection_reason(source) == "materialization_effect"


def test_unsupported_decorators_are_rejected_but_nighthawk_function_is_allowed() -> None:
    assert _rejection_reason("import functools\n\n@functools.cache\ndef f() -> int:\n    return 1\n") == "decorator_not_allowed"
    assert _rejection_reason("from nighthawk import function\n\n@function\ndef f() -> int:\n    return 1\n") == "decorator_not_allowed"
    assert _rejection_reason("import nighthawk as nh\n\n@nh.function()\ndef f() -> int:\n    return 1\n") == "decorator_not_allowed"
    _analyze(
        'import nighthawk as nh\n\n@nh.function\ndef f(text: str) -> str:\n    label: str = ""\n    """execute\n    Set <:label>.\n    """\n    return label\n'
    )


def test_exports_must_be_defined_functions_or_values() -> None:
    assert _rejection_reason("def g() -> int:\n    return 1\n", export_name_tuple=("g", "f")) == "export_undefined"
    assert _rejection_reason("class f:\n    pass\n") == "export_kind_unsupported"
    _analyze("f: int = 3\n")
    _analyze("async def f() -> int:\n    return 1\n")


_NATURAL_BODY = '    label: str = ""\n    """execute\n    Set <:label>.\n    """\n    return label\n\n'


def test_export_kinds_distinguish_sync_and_async_functions_and_values() -> None:
    source = (
        "import nighthawk as nh\n\n"
        "def f() -> int:\n    return 1\n\n"
        "async def g() -> int:\n    return 2\n\n"
        "@nh.function\n"
        "def h(text: str) -> str:\n" + _NATURAL_BODY + "@nh.function\n"
        "async def k(text: str) -> str:\n" + _NATURAL_BODY + "limit: int = 3\n"
    )
    proposal = CodeProposal(source=source, export_name_tuple=("f", "g", "h", "k", "limit"))
    analysis = analyze_generated_source(proposal, filename="<test>")
    assert analysis.export_name_to_kind == {
        "f": "function",
        "g": "async_function",
        "h": "function",
        "k": "async_function",
        "limit": "value",
    }


def test_public_top_level_definitions_must_be_declared_exports() -> None:
    assert _rejection_reason("def f() -> int:\n    return g()\n\ndef g() -> int:\n    return 1\n") == "undeclared_definition"


def test_free_names_must_be_declared_dependencies_or_builtins() -> None:
    source = "def f(x: int) -> int:\n    return lookup(x) + len([x])\n"
    assert _rejection_reason(source) == "undeclared_dependency"
    _analyze(source, dependency_tuple=(DependencyDeclaration(name="lookup", kind="capability", declared_type="(key: str) -> str"),))


def test_global_writes_must_be_declared_in_the_footprint() -> None:
    source = "counter = 0\n\ndef f() -> int:\n    global counter\n    counter += 1\n    return counter\n"
    assert _rejection_reason(source, export_name_tuple=("f", "counter")) == "undeclared_namespace_write"
    _analyze(source, export_name_tuple=("f", "counter"), effect_footprint=EffectFootprint(namespace_write_name_set=frozenset({"counter"})))


def test_dependencies_may_not_be_shadowed_by_module_definitions() -> None:
    source = "lookup = 1\n\ndef f() -> int:\n    return lookup\n"
    reason = _rejection_reason(
        source, export_name_tuple=("f", "lookup"), dependency_tuple=(DependencyDeclaration(name="lookup", kind="reference", declared_type=int),)
    )
    assert reason == "dependency_shadowed"


def test_invocation_intent_requires_a_synchronous_function_entry() -> None:
    reason = _rejection_reason(
        "f: int = 1\n",
        execution_intent="definition_and_invocation",
        entry_export_name="f",
    )
    assert reason == "intent_mismatch"
    reason = _rejection_reason(
        "async def f() -> int:\n    return 1\n",
        execution_intent="definition_and_invocation",
        entry_export_name="f",
    )
    assert reason == "intent_mismatch"


def test_code_proposal_validators() -> None:
    with pytest.raises(ValidationError):
        CodeProposal(source="def f(): pass", export_name_tuple=("f",), entry_export_name="f")
    with pytest.raises(ValidationError):
        CodeProposal(source="def f(): pass", export_name_tuple=("f",), execution_intent="definition_and_invocation")
    with pytest.raises(ValidationError):
        CodeProposal(source="def f(): pass", export_name_tuple=("f",), execution_intent="definition_and_invocation", entry_export_name="g")
    with pytest.raises(ValidationError):
        CodeProposal(source="def _f(): pass", export_name_tuple=("_f",))
    with pytest.raises(ValidationError):
        CodeProposal(source="def f(): pass", export_name_tuple=("f", "f"))
    with pytest.raises(ValidationError):
        CodeProposal(source="def f(): pass", export_name_tuple=())
    proposal = CodeProposal(source="def f(): pass", export_name_tuple=("f",))
    assert len(proposal.defining_source_identity) == 64
    assert proposal.dependency_name_tuple == ()


@pytest.mark.parametrize(
    "source",
    [
        "def f(value: int = compute()) -> int:\n    return value\n",
        "def f(*, value: int = compute()) -> int:\n    return value\n",
        "def f(value: compute()) -> int:\n    return 1\n",
        "def f() -> compute():\n    return 1\n",
        "def f(value: 'compute()') -> int:\n    return 1\n",
        "def f(value: \"'compute()'\") -> int:\n    return 1\n",
        "from __future__ import annotations\n\ndef f(value: int = 0) -> compute():\n    return 1\n",
        "async def f(value: int = compute()) -> int:\n    return value\n",
        "f: compute() = 1\n",
        "f = [compute for _ in range(3)]\n",
        "f = f'{compute}'\n",
        "class _Helper:\n    def method(self, value: int = compute()) -> int:\n        return value\n\ndef f() -> int:\n    return 1\n",
    ],
)
def test_definition_time_evaluation_in_defaults_annotations_and_expressions_is_rejected(source: str) -> None:
    assert _rejection_reason(source) == "materialization_effect"


def test_plain_defaults_and_subscripted_annotations_pass() -> None:
    _analyze("def f(value: int = 0, items: list[int] | None = None, key=lambda item: item) -> dict[str, int]:\n    return {}\n")


def test_dependency_declarations_carry_a_declared_type() -> None:
    with pytest.raises(ValidationError):
        DependencyDeclaration(name="lookup", kind="capability")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        DependencyDeclaration(name="lookup", kind="capability", declared_type=int)
    with pytest.raises(ValidationError):
        DependencyDeclaration(name="lookup", kind="reference", declared_type="str")
    DependencyDeclaration(name="lookup", kind="capability", declared_type="(key: str) -> str")
    DependencyDeclaration(name="items", kind="reference", declared_type=list[int])


def test_generator_lambdas_and_suspendable_methods_are_unsupported() -> None:
    assert _rejection_reason("def f():\n    return lambda: (yield 1)\n") == "unsupported_syntax"
    assert _rejection_reason("class _Helper:\n    def items(self):\n        yield 1\n\ndef f() -> int:\n    return 1\n") == "unsupported_syntax"
    assert (
        _rejection_reason("class _Helper:\n    async def fetch(self) -> int:\n        return 1\n\ndef f() -> int:\n    return 1\n")
        == "unsupported_syntax"
    )
    _analyze("def f():\n    yield 1\n\nasync def _g():\n    return 1\n")


def test_explicit_reference_to_compiler_annotation_state_still_requires_a_dependency() -> None:
    proposal = CodeProposal(source="def read(): return __conditional_annotations__", export_name_tuple=("read",))
    with pytest.raises(SourceRejectedError, match="undeclared_dependency: __conditional_annotations__"):
        analyze_generated_source(proposal, filename="<test>")
