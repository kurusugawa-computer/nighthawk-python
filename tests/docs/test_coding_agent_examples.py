"""Smoke tests verifying that code patterns from docs/for-coding-agents.md are executable.

These tests catch drift between the documentation and the actual API by running
the key patterns described in the coding agent guide.
"""

from __future__ import annotations

import asyncio

import nighthawk as nh
from nighthawk.testing import (
    CallbackExecutor,
    ScriptedExecutor,
    break_response,
    continue_response,
    pass_response,
    raise_response,
    return_response,
)

# ── Module-level helpers (simulate module-level binding functions) ──


def helper(query: str) -> list[str]:
    """Fetch items matching the query."""
    return [f"item_{query}"]


# ── Section 2: Writing Natural blocks ──


class TestNaturalFunctionTemplate:
    """Section 2 sync and async Natural function templates compile and execute."""

    def test_sync_template(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(result="processed output")])

        @nh.natural_function
        def my_function(input_data: str) -> str:
            result: str = ""
            """natural
            Read <input_data> and set <:result> to the processed output.
            """
            return result

        with nh.run(executor):
            assert my_function("raw data") == "processed output"

    def test_async_template(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(result="summarized")])

        @nh.natural_function
        async def my_async_function(text: str) -> str:
            result: str = ""
            """natural
            Summarize <text> and set <:result>.
            """
            return result

        with nh.run(executor):
            assert asyncio.run(my_async_function("long text")) == "summarized"


# ── Section 3: Binding function pattern ──


class TestBindingFunctionPattern:
    """Section 3 binding function pattern: helper visible in GLOBALS."""

    def test_binding_function_visible_in_globals(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(result="item summary")])

        @nh.natural_function
        def process(query: str) -> str:
            result = ""
            """natural
            Call <helper> with <query> and set <:result> to a summary of the results.
            """
            return result

        with nh.run(executor):
            output = process("test")

        assert output == "item summary"
        call = executor.calls[0]
        assert "helper" in call.step_globals
        assert call.step_globals["helper"] is helper


# ── Section 4: Deny frontmatter ──


class TestDenyFrontmatter:
    """Section 4 deny frontmatter restricts allowed outcomes."""

    def test_deny_frontmatter_allows_pass(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(result="ok")])

        @nh.natural_function
        def summarize(text: str) -> str:
            result: str = ""
            """natural
            ---
            deny: [raise, return]
            ---
            Read <text> and set <:result> to a summary.
            """
            return result

        with nh.run(executor):
            assert summarize("input") == "ok"

    def test_deny_frontmatter_rejects_denied_outcome(self) -> None:
        executor = ScriptedExecutor(responses=[raise_response("should not reach", error_type="ValueError")])

        @nh.natural_function
        def summarize(text: str) -> str:
            result: str = ""
            """natural
            ---
            deny: [raise, return]
            ---
            Read <text> and set <:result>.
            """
            return result

        import pytest

        with nh.run(executor), pytest.raises(nh.ExecutionError, match="not allowed"):
            summarize("input")


# ── Section 5: Carry pattern ──


class TestCarryPatternTemplate:
    """Section 5 carry pattern: mutable object passed as read binding."""

    def test_carry_passed_to_consecutive_steps(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(result=10),
                pass_response(result=20),
            ]
        )

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
            Read <carry> and set <:result>.
            """
            return result

        carry: list[str] = []
        with nh.run(executor):
            assert step_1(carry) == 10
            assert step_2(carry) == 20

        assert "carry" in executor.calls[0].step_locals
        assert "carry" in executor.calls[1].step_locals


# ── Section 7: Testing patterns ──


class TestTestingPatterns:
    """Section 7 testing utility imports and patterns are valid."""

    def test_all_response_factories_callable(self) -> None:
        """All factories listed in the outcome factories table are importable and callable."""
        assert callable(pass_response)
        assert callable(raise_response)
        assert callable(return_response)
        assert callable(break_response)
        assert callable(continue_response)

    def test_both_executors_record_calls(self) -> None:
        """Both ScriptedExecutor and CallbackExecutor expose .calls for post-inspection."""
        scripted = ScriptedExecutor(responses=[pass_response()])
        callback = CallbackExecutor(lambda _: pass_response())

        @nh.natural_function
        def f() -> None:
            """natural
            Do something.
            """

        with nh.run(scripted):
            f()
        with nh.run(callback):
            f()

        assert len(scripted.calls) == 1
        assert len(callback.calls) == 1

    def test_error_handling_pattern(self) -> None:
        """Section 7 error handling test pattern works end-to-end."""
        import pytest

        executor = ScriptedExecutor(responses=[raise_response("invalid input", error_type="ValueError")])

        @nh.natural_function
        def classify(text: str) -> str:
            category: str = ""
            """natural
            <ValueError>
            Classify <text> and set <:category>.
            """
            return category

        with nh.run(executor), pytest.raises(ValueError, match="invalid input"):
            classify("???")

    def test_default_response_pattern(self) -> None:
        """Section 7 default_response pattern for multi-step functions."""
        executor = ScriptedExecutor(default_response=pass_response(result="default"))

        @nh.natural_function
        def f() -> str:
            result: str = ""
            """natural
            Set <:result>.
            """
            return result

        with nh.run(executor):
            assert f() == "default"
            assert f() == "default"

        assert len(executor.calls) == 2

    def test_binding_wiring_verification_pattern(self) -> None:
        """Section 7 binding wiring verification pattern including all StepCall fields."""
        executor = ScriptedExecutor(responses=[pass_response(result="")])

        @nh.natural_function
        def process(query: str) -> str:
            result = ""
            """natural
            Call <helper> with <query> and set <:result> to a summary of the results.
            """
            return result

        with nh.run(executor):
            process(query="test")

        call = executor.calls[0]
        assert "helper" in call.step_globals
        assert "query" in call.step_locals
        assert "result" in call.binding_names
        assert "query" in call.natural_program
        assert "break" not in call.allowed_step_kinds
