"""Integration tests for the carry pattern: cross-block context continuity via user-managed objects."""

import nighthawk as nh
from tests.integration.skip_helpers import requires_openai_integration


def test_carry_continuity_across_blocks():
    """Carry list carries context from step 1 into step 2 via in-place mutation."""
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="medium"),
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def step_1(carry: list[str]) -> int:
            result = 0
            """natural
            Set <:result> to 10.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        @nh.natural_function
        def step_2(carry: list[str]) -> int:
            result = 0
            """natural
            Read <carry> to find the previous result.
            Add 10 to it and set <:result>.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        carry: list[str] = []

        r1 = step_1(carry)
        assert r1 == 10
        assert len(carry) >= 1

        r2 = step_2(carry)
        assert r2 == 20
        assert len(carry) >= 2


def test_carry_branching():
    """Branching a carry creates independent continuations."""
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low"),
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def seed_step(carry: list[str]) -> int:
            result = 0
            """natural
            Set <:result> to 100.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        @nh.natural_function
        def branch_add(carry: list[str]) -> int:
            result = 0
            """natural
            Read <carry> to find the previous result.
            Add 5 to it and set <:result>.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        @nh.natural_function
        def branch_multiply(carry: list[str]) -> int:
            result = 0
            """natural
            Read <carry> to find the previous result.
            Multiply it by 2 and set <:result>.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        carry: list[str] = []
        seed_result = seed_step(carry)
        assert seed_result == 100

        # Branch: copy the carry at this point
        carry_a = carry.copy()
        carry_b = carry.copy()

        result_a = branch_add(carry_a)
        assert result_a == 105

        result_b = branch_multiply(carry_b)
        assert result_b == 200

        # Carries diverged
        assert carry_a != carry_b
        assert len(carry_a) >= 2
        assert len(carry_b) >= 2


def test_carry_with_fstring_injection():
    """f-string inline block injects carry content directly into the Natural program text."""
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-nano",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low"),
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def compute_with_context(context_text: str) -> int:
            result = 0
            f"""natural
            Prior context: {context_text}
            Add 1 to the previous result mentioned in the context and set <:result>.
            """
            return result

        context = "Step 0 produced result=42."
        r = compute_with_context(context)
        assert r == 43
