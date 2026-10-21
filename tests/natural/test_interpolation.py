import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import nighthawk as nh
from nighthawk.natural.blocks import find_natural_blocks
from nighthawk.testing import ScriptedExecutor, pass_response


def test_escaped_markers_do_not_declare_inputs_or_outputs() -> None:
    @nh.function
    def describe_syntax() -> int:
        result = 7
        r"""execute
        Explain \<missing>, \<:result>, and \<return> as literal syntax.
        """
        return result

    executor = ScriptedExecutor(responses=[pass_response()])
    with nh.run(executor):
        assert describe_syntax() == 7
    assert executor.calls[0].input_binding_name_to_value == {}
    assert executor.calls[0].output_binding_names == []


def test_escaped_markers_are_rendered_as_literal_model_instructions() -> None:
    @nh.function
    def describe_syntax() -> None:
        r"""execute
        Explain \<missing>, \<:result>, and \<module.field> as literal syntax.
        """

    observed_instructions: list[str] = []

    def respond(messages, information):
        observed_instructions.extend(str(message) for message in messages)
        return ModelResponse(parts=[ToolCallPart("final_result", {"result": {"kind": "pass"}})])

    with nh.run(execution_model=FunctionModel(respond)):
        describe_syntax()
    instructions = "\n".join(observed_instructions)
    assert "Explain <missing>, <:result>, and <module.field> as literal syntax." in instructions


def test_interpolated_decision_instruction_is_validated_after_rendering() -> None:
    @nh.function
    def judge(criterion: str) -> bool:
        result = False
        f"""decide <:result>
        {criterion}
        """
        return result

    executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"result": True})])
    with nh.run(executor):
        assert judge("Is the evidence sufficient?") is True
        with pytest.raises(nh.ExecutionError, match="judgment instruction"):
            judge(" \n\t")
    assert len(executor.calls) == 1
    assert executor.calls[0].natural_program.strip() == "Is the evidence sufficient?"


def test_literal_null_character_does_not_impersonate_interpolation() -> None:
    @nh.function
    def describe_literal(number: int) -> None:
        f"""execute
        Explain literal <\x00> and number {number}.
        """  # noqa: B021 - a first-statement f-string is an inline Natural block.

    executor = ScriptedExecutor(responses=[pass_response()])
    with nh.run(executor):
        describe_literal(3)
    assert "literal <\x00> and number 3" in executor.calls[0].natural_program


@pytest.mark.parametrize("body", ["<{name}>", "<:{name}>", "<pre{name}post>", "<{first}{second}>"])
def test_binding_markers_cannot_cross_interpolation_boundaries(body: str) -> None:
    source = f'def example():\n    f"execute\\n{body}"\n'
    with pytest.raises(nh.NaturalParseError, match="span formatted value boundary"):
        find_natural_blocks(source)


@pytest.mark.parametrize("header", ["decide {name}", "decide <:res{name}>", "decide <:result>{suffix}"])
def test_interpolated_headers_are_rejected_before_body_evaluation(header: str) -> None:
    source = f'def example():\n    f"{header}\\nIs this supported?"\n'
    with pytest.raises(nh.NaturalParseError, match="header must be static"):
        find_natural_blocks(source)


def test_escaped_interpolated_markers_and_format_specifications_preserve_python_semantics() -> None:
    @nh.function
    def describe_literal(name: str, number: float, precision: int) -> None:
        rf"""execute
        Explain \<{name}> and \<:{name}> with value {number:.{precision}f} and {{braces}}.
        """  # noqa: B021 - a first-statement f-string is an inline Natural block.

    executor = ScriptedExecutor(responses=[pass_response()])
    with nh.run(executor):
        describe_literal("sample", 1.234, 2)
    call = executor.calls[0]
    assert call.input_binding_name_to_value == {}
    assert call.output_binding_names == []
    assert r"Explain \<sample> and \<:sample> with value 1.23 and {braces}." in call.natural_program


def test_block_discovery_accepts_a_fully_interpolated_decision_instruction() -> None:
    source = 'def judge():\n    f"decide <:result>\\n{criterion}"\n'
    block = find_natural_blocks(source)[0]
    assert block.block_kind == "decide"
    assert block.input_bindings == ()
    assert block.output_bindings == ("result",)


def test_interpolation_cannot_join_a_backslash_to_a_static_binding() -> None:
    @nh.function
    def read_evidence(separator: str, evidence: str) -> None:
        rf"""execute
        A backslash \{separator}<evidence>.
        """  # noqa: B021 - a first-statement f-string is an inline Natural block.

    executor = ScriptedExecutor(responses=[pass_response()])
    with nh.run(executor):
        read_evidence(" ", "complete evidence")
    assert executor.calls[0].input_binding_name_to_value == {"evidence": "complete evidence"}
