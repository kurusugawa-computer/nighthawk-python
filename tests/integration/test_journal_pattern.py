"""Integration tests for the journal pattern: cross-block context continuity via user-managed objects."""

import nighthawk as nh
from tests.integration.skip_helpers import requires_openai_integration


def test_journal_continuity_across_blocks():
    """Journal list carries context from step 1 into step 2 via in-place mutation."""
    OpenAIResponsesModelSettings = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini",
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def step_1(journal: list[str]) -> int:
            result = 0
            """natural
            Set <:result> to 10.
            Append a one-line summary of what you did to <journal>.
            """
            return result

        @nh.natural_function
        def step_2(journal: list[str]) -> int:
            result = 0
            """natural
            Read <journal> for prior context.
            The journal says the previous result was 10.
            Set <:result> to 20 (previous result plus 10).
            Append a one-line summary of what you did to <journal>.
            """
            return result

        journal: list[str] = []

        r1 = step_1(journal)
        assert r1 == 10
        assert len(journal) >= 1

        r2 = step_2(journal)
        assert r2 == 20
        assert len(journal) >= 2


def test_journal_branching():
    """Branching a journal creates independent continuations."""
    OpenAIResponsesModelSettings = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini",
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def seed_step(journal: list[str]) -> int:
            result = 0
            """natural
            Set <:result> to 100.
            Append "seed: set result to 100" to <journal>.
            """
            return result

        @nh.natural_function
        def branch_add(journal: list[str]) -> int:
            result = 0
            """natural
            Read <journal> for prior context. The seed step set result to 100.
            Set <:result> to 105 (100 + 5).
            Append "branch_add: added 5" to <journal>.
            """
            return result

        @nh.natural_function
        def branch_multiply(journal: list[str]) -> int:
            result = 0
            """natural
            Read <journal> for prior context. The seed step set result to 100.
            Set <:result> to 200 (100 * 2).
            Append "branch_multiply: multiplied by 2" to <journal>.
            """
            return result

        journal: list[str] = []
        seed_result = seed_step(journal)
        assert seed_result == 100

        # Branch: copy the journal at this point
        journal_a = journal.copy()
        journal_b = journal.copy()

        result_a = branch_add(journal_a)
        assert result_a == 105

        result_b = branch_multiply(journal_b)
        assert result_b == 200

        # Journals diverged
        assert journal_a != journal_b
        assert len(journal_a) >= 2
        assert len(journal_b) >= 2


def test_journal_with_fstring_injection():
    """f-string inline block injects journal content directly into the Natural program text."""
    OpenAIResponsesModelSettings = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini",
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def compute_with_context(context_text: str) -> int:
            result = 0
            f"""natural
            Prior context: {context_text}
            Based on the context, the previous result was 42.
            Set <:result> to 43 (previous result plus 1).
            """
            return result

        context = "Step 0 produced result=42."
        r = compute_with_context(context)
        assert r == 43
