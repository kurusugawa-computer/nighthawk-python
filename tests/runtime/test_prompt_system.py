import nighthawk as nh
from nighthawk.runtime.prompt import build_system_prompt, resolve_system_prompt_template_text


def test_step_system_prompt_omits_preview_budget_by_default() -> None:
    configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=8_000,
            locals_max_items=80,
            globals_max_tokens=4_000,
            globals_max_items=40,
            value_max_tokens=200,
            object_max_methods=16,
            object_max_fields=16,
            object_field_value_max_tokens=120,
            tool_result_max_tokens=4_321,
        )
    )

    resolved_system_prompt_text = build_system_prompt(configuration=configuration)
    assert "max 4321 tokens" not in resolved_system_prompt_text
    assert "$tool_result_max_tokens" not in resolved_system_prompt_text


def test_system_prompt_suffix_fragment_injects_tool_result_max_tokens() -> None:
    configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=8_000,
            locals_max_items=80,
            globals_max_tokens=4_000,
            globals_max_items=40,
            value_max_tokens=200,
            object_max_methods=16,
            object_max_fields=16,
            object_field_value_max_tokens=120,
            tool_result_max_tokens=4_321,
        )
    )

    resolved_fragment_text = resolve_system_prompt_template_text(
        template_text="Preview budget: max $tool_result_max_tokens tokens.",
        tool_result_max_tokens=configuration.context_limits.tool_result_max_tokens,
    )

    assert resolved_fragment_text == "Preview budget: max 4321 tokens."
