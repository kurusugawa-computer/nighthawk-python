import textwrap

import nighthawk as nh


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


G = 1


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
