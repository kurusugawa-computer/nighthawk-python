"""Actual durable perimeter events survive a process and gate the attempted effect."""

import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from examples.durable_audit import SqlitePerimeterAuditStore
from nighthawk.agent import (
    Capability,
    EffectFootprint,
    Grant,
    PerimeterEffectAttemptEvent,
    PerimeterEffectRejectedError,
    RunHandle,
    RunRejectedError,
    current_capabilities,
)
from nighthawk.agent.runtime import PerimeterAuditRecord
from tests.nighthawk.agent.conftest import make_runner
from tests.nighthawk.agent.support import EchoInput, EchoResult, run_body


@pytest.mark.parametrize("crash_after_effect", [False, True])
def test_attempt_and_completion_survive_the_effect_process(tmp_path: Path, crash_after_effect: bool) -> None:
    database = tmp_path / "audit.sqlite"
    effect = tmp_path / "effect.txt"
    script = """
import os
from pathlib import Path
import sys
from examples.durable_audit import SqlitePerimeterAuditStore
from nighthawk.agent import Capability, EffectFootprint, Grant, PerimeterPolicy, current_capabilities
from nighthawk.agent.runtime import Runner, ScaffoldComposition, RunContract, bounded_resource_allocation
from nighthawk.testing import ScriptedExecutor
from nighthawk.runtime.step_request import StepCandidate
store = SqlitePerimeterAuditStore(Path(sys.argv[1]))
def write() -> str:
    assert len(store.unmatched_attempts("durable-run")) == 1
    Path(sys.argv[2]).write_text("observed effect")
    if sys.argv[3] == "crash":
        os._exit(17)
    return "written"
capability = Capability(name="write", function=write, authority_boundary="selected file", effect_footprint=EffectFootprint(perimeter_effect_class_set={"durable_write_back"}))
policy = PerimeterPolicy.permitting("durable_write_back")
runner = Runner(ScriptedExecutor(default_response=StepCandidate()), policy, lambda identity: bounded_resource_allocation(), perimeter_audit_store=store, accepts_aggregate_executor_accounting=True)
composition = ScaffoldComposition(RunContract(type(None), str), lambda value: current_capabilities()["write"](), Grant.of(capability), policy)
assert runner.run(composition, None, run_id="durable-run") == "written"
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(database), str(effect), "crash" if crash_after_effect else "complete"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == (17 if crash_after_effect else 0), result.stderr
    assert effect.read_text() == "observed effect"
    reopened = SqlitePerimeterAuditStore(database)
    records = reopened.read("durable-run")
    if crash_after_effect:
        assert [record.event.kind for record in records] == ["perimeter_effect_attempt"]
        assert reopened.unmatched_attempts("durable-run") == records
        return
    assert [record.event.kind for record in records] == ["perimeter_effect_attempt", "perimeter_effect_completion"]
    assert records[1].parent_event_id == records[0].event_id
    assert not reopened.unmatched_attempts("durable-run")
    assert reopened.read("another-run") == ()
    with pytest.raises(sqlite3.IntegrityError):
        reopened.record(records[0])
    assert reopened.read("durable-run") == records


def test_attempt_without_completion_remains_unknown_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite"
    record = PerimeterAuditRecord(
        run_id="interrupted",
        event_id="attempt",
        parent_event_id=None,
        event=PerimeterEffectAttemptEvent(capability_name="write", attribution_node_id="root", perimeter_effect_class_tuple=("durable_write_back",)),
    )
    SqlitePerimeterAuditStore(path).record(record)
    assert SqlitePerimeterAuditStore(path).unmatched_attempts("interrupted") == (record,)


def test_storage_failure_prevents_effect_and_missing_store_prevents_admission(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite"
    store = SqlitePerimeterAuditStore(path)
    effects: list[str] = []

    def write() -> str:
        effects.append("attempted")
        return "written"

    capability = Capability(
        name="write",
        function=write,
        authority_boundary="test effect",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"durable_write_back"})),
    )
    # A verified storage error, rather than a mock claiming durability.
    path.unlink()
    path.mkdir()

    def body(value: EchoInput) -> EchoResult:
        with pytest.raises(PerimeterEffectRejectedError, match="attempt_record_failed"):
            current_capabilities()["write"]()
        return EchoResult()

    run_body(body, runner=replace(make_runner(), perimeter_audit_store=store), run_entry_grant=Grant.of(capability))
    assert not effects
    handle = RunHandle()
    with pytest.raises(RunRejectedError, match="audit store"):
        run_body(body, runner=replace(make_runner(), perimeter_audit_store=None), run_entry_grant=Grant.of(capability), handle=handle)
    assert handle.run_id is None and not effects
