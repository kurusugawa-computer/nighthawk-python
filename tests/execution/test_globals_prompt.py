from __future__ import annotations

import builtins

import nighthawk as nh
from nighthawk.execution.context import ExecutionContext
from nighthawk.execution.environment import ExecutionEnvironment, environment
from nighthawk.execution.executors import AgentExecutor, build_user_prompt


def _build_execution_context(*, execution_globals: dict[str, object], execution_locals: dict[str, object]) -> ExecutionContext:
    return ExecutionContext(
        execution_id="test",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals=execution_globals,
        execution_locals=execution_locals,
        binding_commit_targets=set(),
    )


def _globals_section(prompt: str) -> str:
    return prompt.split("<<<NH:GLOBALS>>>\n", 1)[1].split("\n<<<NH:END_GLOBALS>>>", 1)[0]


def _locals_section(prompt: str) -> str:
    return prompt.split("<<<NH:LOCALS>>>\n", 1)[1].split("\n<<<NH:END_LOCALS>>>", 1)[0]


def test_globals_markers_present_even_when_empty(tmp_path) -> None:
    execution_context = _build_execution_context(
        execution_globals={"__builtins__": builtins},
        execution_locals={"x": 10},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with environment(
        ExecutionEnvironment(
            execution_configuration=execution_context.execution_configuration,
            execution_executor=AgentExecutor(agent=NoopAgent()),
            workspace_root=tmp_path,
        )
    ):
        prompt = build_user_prompt(
            processed_natural_program="Say hi.",
            execution_context=execution_context,
        )
    assert "<<<NH:GLOBALS>>>" in prompt
    assert "<<<NH:END_GLOBALS>>>" in prompt


def test_globals_selection_escaping_and_omission(tmp_path) -> None:
    module = type("Module", (), {})()
    module.attr = "value"  # type: ignore[attr-defined]

    execution_context = _build_execution_context(
        execution_globals={
            "__builtins__": builtins,
            "module": module,
        },
        execution_locals={"x": 10},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with environment(
        ExecutionEnvironment(
            execution_configuration=execution_context.execution_configuration,
            execution_executor=AgentExecutor(agent=NoopAgent()),
            workspace_root=tmp_path,
        )
    ):
        prompt = build_user_prompt(
            processed_natural_program=("Use <module.attr>.\nDo not select \\u005c<module.attr>.\nAlso mention <module.missing>.\n").encode("utf-8").decode("unicode_escape"),
            execution_context=execution_context,
        )

    globals_section = _globals_section(prompt)

    assert "module:" in globals_section
    assert "module.missing" not in globals_section
    assert "\\<module.attr>" not in prompt
    assert "<module.attr>" in prompt


def test_locals_first_prevents_globals_entry(tmp_path) -> None:
    global_module = type("GlobalModule", (), {})()
    global_module.attr = "global"  # type: ignore[attr-defined]

    local_module = type("LocalModule", (), {})()
    local_module.attr = "local"  # type: ignore[attr-defined]

    execution_context = _build_execution_context(
        execution_globals={
            "__builtins__": builtins,
            "module": global_module,
        },
        execution_locals={
            "module": local_module,
        },
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with environment(
        ExecutionEnvironment(
            execution_configuration=execution_context.execution_configuration,
            execution_executor=AgentExecutor(agent=NoopAgent()),
            workspace_root=tmp_path,
        )
    ):
        prompt = build_user_prompt(processed_natural_program="Use <module.attr>.", execution_context=execution_context)

    globals_section = _globals_section(prompt)
    assert "module" not in globals_section
    assert "<snipped>" not in globals_section


def test_same_reference_is_deduplicated(tmp_path) -> None:
    module = type("Module", (), {})()
    module.attr = "value"  # type: ignore[attr-defined]

    execution_context = _build_execution_context(
        execution_globals={
            "__builtins__": builtins,
            "module": module,
        },
        execution_locals={},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with environment(
        ExecutionEnvironment(
            execution_configuration=execution_context.execution_configuration,
            execution_executor=AgentExecutor(agent=NoopAgent()),
            workspace_root=tmp_path,
        )
    ):
        prompt = build_user_prompt(
            processed_natural_program="<module.attr> <module.attr> <module.attr>",
            execution_context=execution_context,
        )

    globals_section = _globals_section(prompt)
    assert globals_section.count("module:") == 1


def test_globals_ordering_is_lexicographic_by_top_level_name(tmp_path) -> None:
    a_module = type("AModule", (), {})()
    b_module = type("BModule", (), {})()

    execution_context = _build_execution_context(
        execution_globals={
            "__builtins__": builtins,
            "b_module": b_module,
            "a_module": a_module,
        },
        execution_locals={},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with environment(
        ExecutionEnvironment(
            execution_configuration=execution_context.execution_configuration,
            execution_executor=AgentExecutor(agent=NoopAgent()),
            workspace_root=tmp_path,
        )
    ):
        prompt = build_user_prompt(
            processed_natural_program="<b_module.attr> <a_module.attr>",
            execution_context=execution_context,
        )

    globals_section = _globals_section(prompt)
    assert globals_section.splitlines()[0].startswith("a_module:")
    assert globals_section.splitlines()[1].startswith("b_module:")


def test_locals_ordering_is_lexicographic_by_name(tmp_path) -> None:
    execution_context = _build_execution_context(
        execution_globals={"__builtins__": builtins},
        execution_locals={
            "b": 2,
            "a": 1,
        },
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with environment(
        ExecutionEnvironment(
            execution_configuration=execution_context.execution_configuration,
            execution_executor=AgentExecutor(agent=NoopAgent()),
            workspace_root=tmp_path,
        )
    ):
        prompt = build_user_prompt(
            processed_natural_program="Say hi.",
            execution_context=execution_context,
        )

    locals_section = _locals_section(prompt)
    assert locals_section.splitlines()[0].startswith("a:")
    assert locals_section.splitlines()[1].startswith("b:")
