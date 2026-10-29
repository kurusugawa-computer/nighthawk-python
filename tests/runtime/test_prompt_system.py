import nighthawk as nh
from nighthawk.runtime.prompt import resolve_system_prompt_template_text


def test_system_prompt_suffix_fragment_injects_tool_result_max_tokens() -> None:
    configuration = nh.StepExecutorConfiguration(context_limits=nh.StepContextLimits(tool_result_max_tokens=4_321))

    resolved_fragment_text = resolve_system_prompt_template_text(
        template_text="Preview budget: max $tool_result_max_tokens tokens.",
        tool_result_max_tokens=configuration.context_limits.tool_result_max_tokens,
    )

    assert resolved_fragment_text == "Preview budget: max 4321 tokens."
