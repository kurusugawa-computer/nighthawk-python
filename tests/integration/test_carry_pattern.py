import nighthawk as nh
from tests.integration.skip_helpers import requires_openai_integration


def test_carry_continuity_across_blocks():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            execution_model="openai-responses:gpt-5.6-luna",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="medium"),
        ),
    )

    with nh.run(step_executor):

        @nh.function
        def step_1(carry: list[str]) -> int:
            result = 0
            """execute
            Set <:result> to 10.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        @nh.function
        def step_2(carry: list[str]) -> int:
            result = 0
            """execute
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
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            execution_model="openai-responses:gpt-5.6-luna",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low"),
        ),
    )

    with nh.run(step_executor):

        @nh.function
        def seed_step(carry: list[str]) -> int:
            result = 0
            """execute
            Set <:result> to 100.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        @nh.function
        def branch_add(carry: list[str]) -> int:
            result = 0
            """execute
            Read <carry> to find the previous result.
            Add 5 to it and set <:result>.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        @nh.function
        def branch_multiply(carry: list[str]) -> int:
            result = 0
            """execute
            Read <carry> to find the previous result.
            Multiply it by 2 and set <:result>.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        carry: list[str] = []
        seed_result = seed_step(carry)
        assert seed_result == 100

        carry_a = carry.copy()
        carry_b = carry.copy()

        result_a = branch_add(carry_a)
        assert result_a == 105

        result_b = branch_multiply(carry_b)
        assert result_b == 200

        assert carry_a != carry_b
        assert len(carry_a) >= 2
        assert len(carry_b) >= 2


def test_carry_with_fstring_injection():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            execution_model="openai-responses:gpt-5.6-luna",
            model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low"),
        ),
    )

    with nh.run(step_executor):

        @nh.function
        def compute_with_context(context_text: str) -> int:
            result = 0
            f"""execute
            Prior context: {context_text}
            Add 1 to the previous result mentioned in the context and set <:result>.
            """
            return result

        context = "Step 0 produced result=42."
        r = compute_with_context(context)
        assert r == 43
