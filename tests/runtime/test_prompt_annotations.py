import ast
from collections.abc import Callable
from typing import Literal

import pytest
from pydantic_ai.messages import BinaryContent

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepEnvironment, StepRequest
from tests.support.prompts import build_step_context, build_user_prompt_content, build_user_prompt_text, locals_section, prompt_content_to_text


def test_selected_contract_and_read_annotation_replace_runtime_types() -> None:
    context = build_step_context(python_globals={}, python_locals={"result": None, "items": [], "value": 1})
    context.binding_commit_targets.update({"result", "priority"})
    context.environment = StepEnvironment(
        python_name_to_value=context.step_locals,
        python_globals={},
        output_name_set=frozenset(context.binding_commit_targets),
        binding_name_to_type={"result": int | None, "priority": Literal["routine", "urgent"]},
        binding_name_to_annotation={"result": "Alias", "items": "list[Record]", "value": "str"},
    )
    prompt = build_user_prompt_text(processed_natural_program="Choose result and priority.", step_context=context)
    section = locals_section(prompt)
    assert "result: int | None = None" in section
    assert "priority: Literal['routine', 'urgent']  # unbound" in section
    assert "items: list[Record] = []" in section
    assert "value: str = 1" in section
    declarations = ast.parse(section).body
    assert all(isinstance(statement, ast.AnnAssign) for statement in declarations)
    assert "Alias" not in section


def test_read_annotations_are_rendered_without_evaluation() -> None:
    effects: list[str] = []
    prompts: list[str] = []

    def annotation_effect() -> type:
        effects.append("annotation")
        return int

    @nh.function
    def workflow() -> None:
        findings: list[annotation_effect()] = []  # type: ignore[valid-type]  # noqa: F841 - referenced by Natural.
        """execute
        Describe <findings>.
        """

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            prompts.append(build_user_prompt_text(processed_natural_program=request.processed_natural_program, step_context=request.step_context))
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()):
        workflow()
    assert "findings: list[annotation_effect()] = []" in locals_section(prompts[0])
    assert effects == []


def test_omitted_contract_remains_discoverable_with_a_colliding_runtime_name() -> None:
    context = build_step_context(python_globals={}, python_locals={"__nighthawk_runtime_0": "application", "message": "Outage."})
    context.binding_commit_targets.add("selection")
    context.read_binding_names = frozenset({"message"})
    context.environment = StepEnvironment(
        python_name_to_value=context.step_locals,
        python_globals={},
        local_name_set=frozenset({"selection"}),
        output_name_set=frozenset({"selection"}),
        binding_name_to_type={"selection": Literal["defer_review", "page_on_call"]},
    )
    configuration = nh.StepExecutorConfiguration(context_limits=nh.StepContextLimits(locals_max_items=1))
    section = locals_section(
        build_user_prompt_text(processed_natural_program="Read <message> and choose <:selection>.", step_context=context, configuration=configuration)
    )
    assert "Literal[" not in section
    runtime_reference = context.environment.runtime_reference
    assert runtime_reference != "__nighthawk_runtime_0"
    discovery_expression = f"{runtime_reference}.environment.discover()"
    description_expression = f"{runtime_reference}.environment.describe('name')"
    assert discovery_expression in section and description_expression in section
    namespace = context.environment.expression_namespace()
    listing = eval(discovery_expression, namespace, namespace)
    discovered_names = {item["name"] for item in listing["names"]}
    while "continuation" in listing:
        listing = eval(listing["continuation"], namespace, namespace)
        discovered_names.update(item["name"] for item in listing["names"])
    assert "selection" in discovered_names
    description = eval(description_expression.replace("'name'", "'selection'"), namespace, namespace)
    assert description["output_contract"] == "Literal['defer_review', 'page_on_call']"
    assert description["write_eligible"] is True


def test_effective_binding_and_unbound_local_do_not_expose_shadowed_values() -> None:
    context = build_step_context(python_globals={"missing": 23}, python_locals={"value": 7})
    context.read_binding_names = frozenset({"value", "missing"})
    context.environment = StepEnvironment(
        python_name_to_value=context.step_locals,
        python_globals=context.step_globals,
        local_name_set=frozenset({"value", "missing"}),
        task_name_to_value={"value": 100, "missing": 200},
    )
    prompt = build_user_prompt_text(processed_natural_program="Read <value> and <missing>.", step_context=context)
    assert "value: int = 7" in prompt
    assert "missing: object  # unbound" in prompt
    assert "= 100" not in prompt and "= 200" not in prompt and "= 23" not in prompt
    assert "source=" not in prompt
    assert context.environment.resolve("value", source="task") == 100


@pytest.mark.parametrize("value", [None, True, False, [None, True, False], {"null": None, "true": True}, 'null true false "true"'])
def test_complete_literal_previews_preserve_python_values(value: object) -> None:
    context = build_step_context(python_globals={}, python_locals={"value": value})
    section = locals_section(build_user_prompt_text(processed_natural_program="Read value.", step_context=context))
    statement = ast.parse(section).body[0]
    assert isinstance(statement, ast.AnnAssign) and statement.value is not None
    assert ast.literal_eval(statement.value) == value


@pytest.mark.parametrize("value", [list(range(20)), "x" * 300, (1, 2), {1: "one"}, b"bytes", float("inf")])
def test_lossy_previews_do_not_claim_an_actual_assignment(value: object) -> None:
    context = build_step_context(python_globals={}, python_locals={"value": value})
    section = locals_section(build_user_prompt_text(processed_natural_program="Read value.", step_context=context))
    statement = ast.parse(section).body[0]
    assert isinstance(statement, ast.AnnAssign) and statement.value is None
    assert "# preview:" in section


def test_preview_comparison_does_not_call_application_equality_or_repr() -> None:
    class Opaque:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("Application equality must not run")

        def __repr__(self) -> str:
            raise AssertionError("Application repr must not run")

    context = build_step_context(python_globals={}, python_locals={"value": [Opaque()]})
    section = locals_section(build_user_prompt_text(processed_natural_program="Read value.", step_context=context))
    assert "# preview:" in section
    ast.parse(section)


def test_callable_annotation_keeps_signature_and_media_leaf_keeps_its_own_type() -> None:
    def transform(value: int) -> str:
        return str(value)

    class Holder:
        def __init__(self) -> None:
            self.photo = BinaryContent(data=b"image", media_type="image/png")

    holder = Holder()
    context = build_step_context(python_globals={}, python_locals={"transform": transform, "holder": holder})
    context.environment = StepEnvironment(
        python_name_to_value=context.step_locals,
        python_globals={},
        output_name_set=frozenset({"holder"}),
        binding_name_to_type={"holder": object},
        binding_name_to_annotation={"transform": Callable[[int], str]},
    )
    content = build_user_prompt_content(processed_natural_program="Inspect <holder.photo> and <transform>.", step_context=context)
    section = locals_section(prompt_content_to_text(content))
    assert "transform: Callable[" in section and "# (value: int) -> str" in section
    assert "holder: object  # Holder instance" in section
    assert "holder.photo: BinaryContent  # attached content:" in section
    assert any(item is holder.photo for item in content)
    ast.parse(section)
