import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NaturalExecutionError


class Memory(BaseModel):
    pass


def test_decorator_updates_output_binding_via_docstring_natural_block(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f(x: int):
            """natural
            <:result>
            {{"natural_final": {{"effect": null, "error": null}}, "outputs": {{"result": {x + 1}}}}}
            """
            result = 0
            return result

        assert f(10) == 11


def test_stub_return_effect_parses_and_coerces_value_json(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            {{"natural_final": {{"effect": {{"type": "return", "value_json": "11"}}, "error": null}}, "outputs": {{}}}}
            """
            return 0

        assert f() == 11


def test_stub_return_effect_invalid_value_json_raises(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            {{"natural_final": {{"effect": {{"type": "return", "value_json": "\\\"not an int\\\""}}, "error": null}}, "outputs": {{}}}}
            """
            return 0

        with pytest.raises(NaturalExecutionError):
            f()


def test_stub_continue_effect_skips_following_statements(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f() -> int:
            total = 0
            for i in range(5):
                total += 1
                """natural
                {{"natural_final": {{"effect": {{"type": "continue", "value_json": null}}, "error": null}}, "outputs": {{}}}}
                """
                total += 100
            return total

        assert f() == 5


def test_stub_break_effect_breaks_loop(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f() -> int:
            total = 0
            for i in range(5):
                total += 1
                """natural
                {{"natural_final": {{"effect": {{"type": "break", "value_json": null}}, "error": null}}, "outputs": {{}}}}
                """
                total += 100
            return total

        assert f() == 1


def test_stub_break_outside_loop_raises(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            {{"natural_final": {{"effect": {{"type": "break", "value_json": null}}, "error": null}}, "outputs": {{}}}}
            """
            return 1

        with pytest.raises(NaturalExecutionError):
            f()


def test_decorator_updates_output_binding_via_inline_natural_block(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f(x: int):
            result = 0
            """natural
            <:result>
            {{"natural_final": {{"effect": null, "error": null}}, "outputs": {{"result": {x * 2}}}}}
            """
            return result

        assert f(6) == 12


def test_decorator_uses_agent_backend_by_default(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):
            from nighthawk.agent import NaturalEffect, NaturalFinal, assign_tool

            assert deps is not None
            assign_tool(deps, "<result>", "x + 1", type_hints={})
            return FakeRunResult(
                NaturalFinal(
                    effect=NaturalEffect(type="return", value_json="11"),
                    error=None,
                )
            )

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    agent = FakeAgent()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f(x: int):
            """natural
            <:result>
            {{"natural_final": {{"effect": null, "error": null}}, "outputs": {{"result": {x + 1}}}}}
            """
            result = 0
            return result

        assert f(10) == 11
