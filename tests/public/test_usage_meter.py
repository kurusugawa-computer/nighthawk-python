from __future__ import annotations

from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.runtime.scoping import UsageMeter, get_current_usage_meter
from tests.execution.stub_executor import StubExecutor

# ---------------------------------------------------------------------------
# UsageMeter unit tests
# ---------------------------------------------------------------------------


class TestUsageMeterRecord:
    def test_record_accumulates_correctly(self) -> None:
        meter = UsageMeter()
        meter.record(RunUsage(input_tokens=10, output_tokens=5))
        meter.record(RunUsage(input_tokens=20, output_tokens=15))
        assert meter.total_tokens == 50

    def test_total_tokens_reflects_input_plus_output(self) -> None:
        meter = UsageMeter()
        meter.record(RunUsage(input_tokens=100, output_tokens=50))
        assert meter.total_tokens == 150

    def test_snapshot_returns_independent_copy(self) -> None:
        meter = UsageMeter()
        meter.record(RunUsage(input_tokens=10, output_tokens=5))
        snap = meter.snapshot()
        meter.record(RunUsage(input_tokens=30, output_tokens=20))
        assert snap.total_tokens == 15
        assert meter.total_tokens == 65

    def test_snapshot_reflects_current_state(self) -> None:
        meter = UsageMeter()
        meter.record(RunUsage(input_tokens=7, output_tokens=3))
        snap = meter.snapshot()
        assert snap.input_tokens == 7
        assert snap.output_tokens == 3
        assert snap.total_tokens == 10

    def test_empty_meter_has_zero_tokens(self) -> None:
        meter = UsageMeter()
        assert meter.total_tokens == 0


# ---------------------------------------------------------------------------
# Context variable integration tests
# ---------------------------------------------------------------------------


class TestUsageMeterContextVariable:
    def test_returns_none_outside_run(self) -> None:
        assert get_current_usage_meter() is None

    def test_returns_meter_inside_run(self) -> None:
        with nh.run(StubExecutor()):
            meter = get_current_usage_meter()
            assert isinstance(meter, UsageMeter)

    def test_meter_is_reset_after_run_exits(self) -> None:
        with nh.run(StubExecutor()):
            assert get_current_usage_meter() is not None
        assert get_current_usage_meter() is None

    def test_nested_runs_have_independent_meters(self) -> None:
        with nh.run(StubExecutor()):
            outer_meter = get_current_usage_meter()
            assert outer_meter is not None
            outer_meter.record(RunUsage(input_tokens=100, output_tokens=50))

            with nh.run(StubExecutor()):
                inner_meter = get_current_usage_meter()
                assert inner_meter is not None
                assert inner_meter is not outer_meter
                assert inner_meter.total_tokens == 0

            assert get_current_usage_meter() is outer_meter
            assert outer_meter.total_tokens == 150


# ---------------------------------------------------------------------------
# Host-installed meters on run() and scope()
# ---------------------------------------------------------------------------


class _UsageAgent:
    """Fake agent whose run_sync returns a pass outcome with fixed usage."""

    def run_sync(self, *args: object, **kwargs: object) -> object:  # noqa: ARG002
        class Result:
            output = {"result": {"kind": "pass"}}
            usage = RunUsage(input_tokens=10, output_tokens=5)

        return Result()


class TestHostInstalledUsageMeter:
    def test_run_accepts_usage_meter(self) -> None:
        meter = UsageMeter()
        with nh.run(StubExecutor(), usage_meter=meter):
            assert get_current_usage_meter() is meter
        assert get_current_usage_meter() is None

    def test_scope_replaces_meter_and_restores_parent(self) -> None:
        scoped_meter = UsageMeter()
        with nh.run(StubExecutor()):
            run_meter = get_current_usage_meter()
            with nh.scope(usage_meter=scoped_meter):
                assert get_current_usage_meter() is scoped_meter
            assert get_current_usage_meter() is run_meter

    def test_scope_without_usage_meter_inherits(self) -> None:
        with nh.run(StubExecutor()):
            run_meter = get_current_usage_meter()
            with nh.scope(mode="replace"):
                assert get_current_usage_meter() is run_meter

    def test_step_records_into_scoped_meter_only(self) -> None:
        scoped_meter = UsageMeter()
        step_executor = nh.AgentStepExecutor.from_agent(agent=_UsageAgent())

        @nh.natural_function
        def natural_pass_function() -> None:
            """natural
            Do nothing.
            """

        with nh.run(step_executor):
            run_meter = get_current_usage_meter()
            assert run_meter is not None
            with nh.scope(usage_meter=scoped_meter):
                natural_pass_function()
            assert scoped_meter.total_tokens == 15
            assert run_meter.total_tokens == 0
