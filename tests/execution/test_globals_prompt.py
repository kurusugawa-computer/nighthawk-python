from __future__ import annotations

import builtins
import logging

import nighthawk as nh
from nighthawk.runtime.prompt import build_user_prompt
from tests.execution.prompt_test_helpers import build_step_context, build_user_prompt_text, globals_section, locals_section


def test_globals_markers_present_even_when_empty(tmp_path) -> None:
    _ = tmp_path
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"x": 10},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent())):
        prompt = build_user_prompt_text(
            processed_natural_program="Say hi.",
            step_context=step_context,
        )
    assert "<<<NH:GLOBALS>>>" in prompt
    assert "<<<NH:END_GLOBALS>>>" in prompt


def test_globals_selection_escaping_and_omission(tmp_path) -> None:
    _ = tmp_path
    module = type("Module", (), {})()
    module.attr = "value"  # type: ignore[attr-defined]

    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "module": module,
        },
        python_locals={"x": 10},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent())):
        prompt = build_user_prompt_text(
            processed_natural_program=(b"Use <module.attr>.\nDo not select \\u005c<module.attr>.\nAlso mention <module.missing>.\n").decode(
                "unicode_escape"
            ),
            step_context=step_context,
        )

    globals_text = globals_section(prompt)

    assert "module:" in globals_text
    assert "module.missing" not in globals_text
    assert "\\<module.attr>" not in prompt
    assert "<module.attr>" in prompt


def test_locals_first_prevents_globals_entry(tmp_path) -> None:
    global_module = type("GlobalModule", (), {})()
    global_module.attr = "global"  # type: ignore[attr-defined]

    local_module = type("LocalModule", (), {})()
    local_module.attr = "local"  # type: ignore[attr-defined]

    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "module": global_module,
        },
        python_locals={
            "module": local_module,
        },
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent())):
        prompt = build_user_prompt_text(processed_natural_program="Use <module.attr>.", step_context=step_context)

    globals_text = globals_section(prompt)
    assert "module" not in globals_text
    assert "<snipped>" not in globals_text


def test_same_reference_is_deduplicated(tmp_path) -> None:
    module = type("Module", (), {})()
    module.attr = "value"  # type: ignore[attr-defined]

    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "module": module,
        },
        python_locals={},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent())):
        prompt = build_user_prompt_text(
            processed_natural_program="<module.attr> <module.attr> <module.attr>",
            step_context=step_context,
        )

    globals_text = globals_section(prompt)
    assert globals_text.count("module:") == 1


def test_globals_ordering_is_lexicographic_by_top_level_name(tmp_path) -> None:
    _ = tmp_path
    a_module = type("AModule", (), {})()
    b_module = type("BModule", (), {})()

    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "b_module": b_module,
            "a_module": a_module,
        },
        python_locals={},
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent())):
        prompt = build_user_prompt_text(
            processed_natural_program="<b_module.attr> <a_module.attr>",
            step_context=step_context,
        )

    globals_text = globals_section(prompt)
    assert globals_text.splitlines()[0].startswith("a_module:")
    assert globals_text.splitlines()[1].startswith("b_module:")


def test_locals_ordering_is_lexicographic_by_name(tmp_path) -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "b": 2,
            "a": 1,
        },
    )

    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent())):
        prompt = build_user_prompt_text(
            processed_natural_program="Say hi.",
            step_context=step_context,
        )

    locals_text = locals_section(prompt)
    assert locals_text.splitlines()[0].startswith("a:")
    assert locals_text.splitlines()[1].startswith("b:")


def test_user_prompt_template_can_inject_tool_result_max_tokens() -> None:
    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"x": 10},
    )
    configuration = nh.StepExecutorConfiguration(
        prompts=nh.StepPromptTemplates(
            step_user_prompt_template=(
                "<<<NH:PROGRAM>>>\n"
                "$program\n"
                "<<<NH:END_PROGRAM>>>\n\n"
                "limit=$tool_result_max_tokens\n\n"
                "<<<NH:LOCALS>>>\n"
                "$locals\n"
                "<<<NH:END_LOCALS>>>\n\n"
                "<<<NH:GLOBALS>>>\n"
                "$globals\n"
                "<<<NH:END_GLOBALS>>>\n"
            )
        ),
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
        ),
    )

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent(), configuration=configuration)):
        prompt = build_user_prompt(
            processed_natural_program="Say hi.",
            step_context=step_context,
            configuration=configuration,
        )

    assert "limit=4321" in prompt
    assert "$tool_result_max_tokens" not in prompt
    assert "<<<NH:PROGRAM>>>" in prompt
    assert "<<<NH:LOCALS>>>" in prompt
    assert "<<<NH:GLOBALS>>>" in prompt


def test_prompt_context_token_truncation_emits_audit_log(monkeypatch) -> None:
    class NoopAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Agent should not be invoked by build_user_prompt")

    seen_log_record_list: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen_log_record_list.append(record)

    logger = logging.getLogger("nighthawk")
    handler = _Handler()
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.DEBUG)
    monkeypatch.setattr(logger, "level", logging.DEBUG)

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "a": "x" * 300,
            "b": "y" * 300,
        },
    )
    configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=10,
            locals_max_items=80,
            globals_max_tokens=4_000,
            globals_max_items=40,
            value_max_tokens=120,
            object_max_methods=16,
            object_max_fields=16,
            object_field_value_max_tokens=120,
            tool_result_max_tokens=1_200,
        )
    )

    with nh.run(nh.AgentStepExecutor.from_agent(agent=NoopAgent(), configuration=configuration)):
        prompt = build_user_prompt(
            processed_natural_program="Say hi.",
            step_context=step_context,
            configuration=configuration,
        )

    logger.removeHandler(handler)
    logger.setLevel(original_level)

    locals_text = locals_section(prompt)
    assert "<snipped>" in locals_text

    truncation_log_record_list = [record for record in seen_log_record_list if "prompt_context_truncated" in record.getMessage()]
    assert len(truncation_log_record_list) == 1
    message = truncation_log_record_list[0].getMessage()
    assert "'nighthawk.prompt_context.section': 'locals'" in message
    assert "'nighthawk.prompt_context.reason': 'token_limit'" in message
    assert "'nighthawk.prompt_context.max_tokens': 10" in message
    assert "'step.id': 'test'" in message
