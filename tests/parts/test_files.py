from __future__ import annotations

from pathlib import Path

import pytest

import nighthawk as nh
from nighthawk.adapters.files import FileSystemWriter
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import CallbackExecutor
from nighthawk.harness.context import current_capabilities
from nighthawk.harness.errors import CapabilityRejectedError, RunRejectedError
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import PerimeterEffectAttemptEvent
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.run_handle import RunHandle
from nighthawk.parts.files import AddFileOperation, FilePatchOutcome, FilePatchPolicy, FilePatchRequest, UpdateFileOperation, file_patch_capability
from tests.support.harness import make_runner
from tests.support.runs import EchoResult, run_body


def test_apply_file_patch_enforces_writable_roots_and_applies_atomically(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("keep", encoding="utf-8")
    target = workspace / "note.txt"
    target.write_text("old", encoding="utf-8")
    apply_file_patch = file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(workspace,)), file_writer=FileSystemWriter()).function
    outcome = apply_file_patch(
        FilePatchRequest(
            operations=(
                UpdateFileOperation(file_path=target, old_text="old", new_text="new"),
                AddFileOperation(file_path=workspace / "added.txt", new_text="hello\n"),
            )
        )
    )
    assert outcome == FilePatchOutcome(applied_operation_count=2)
    assert target.read_text(encoding="utf-8") == "new"
    with pytest.raises(CapabilityRejectedError) as captured:
        apply_file_patch(FilePatchRequest(operations=(UpdateFileOperation(file_path=outside / "secret.txt", old_text="keep", new_text="x"),)))
    assert captured.value.reason_code == "path_outside_allow_list"
    with pytest.raises(CapabilityRejectedError) as missing_parent:
        apply_file_patch(FilePatchRequest(operations=(AddFileOperation(file_path=workspace / "missing" / "x.txt", new_text="x"),)))
    assert missing_parent.value.reason_code == "path_parent_missing"
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"


def test_file_patch_allows_update_of_a_file_added_in_the_same_request(tmp_path: Path) -> None:
    target = tmp_path / "created.txt"
    capability = file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(tmp_path,)), file_writer=FileSystemWriter())
    result = capability.function(
        FilePatchRequest(
            operations=(
                AddFileOperation(file_path=target, new_text="first"),
                UpdateFileOperation(file_path=target, old_text="first", new_text="last"),
            )
        )
    )
    assert result == FilePatchOutcome(applied_operation_count=2)
    assert target.read_text() == "last"


@pytest.mark.parametrize("add", [False, True])
def test_file_patch_rejects_symlink_targets_outside_writable_roots(tmp_path: Path, add: bool) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    link = workspace / "link.txt"
    link.symlink_to(outside)
    capability = file_patch_capability(
        policy=FilePatchPolicy(path_write_allow_list=(workspace,)), file_writer=FileSystemWriter(overwrite_on_add=True)
    )
    operation = (
        AddFileOperation(file_path=link, new_text="changed") if add else UpdateFileOperation(file_path=link, old_text="keep", new_text="changed")
    )
    with pytest.raises(CapabilityRejectedError) as captured:
        capability.function(FilePatchRequest(operations=(operation,)))
    assert captured.value.reason_code == "path_outside_allow_list"
    assert link.is_symlink()
    assert outside.read_text() == "keep"


def test_file_patch_rejects_outside_directory_entries_pointing_into_writable_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("keep")
    outside_link = tmp_path / "link.txt"
    outside_link.symlink_to(target)
    capability = file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(workspace,)), file_writer=FileSystemWriter())
    with pytest.raises(CapabilityRejectedError) as captured:
        capability.function(FilePatchRequest(operations=(UpdateFileOperation(file_path=outside_link, old_text="keep", new_text="changed"),)))
    assert captured.value.reason_code == "path_outside_allow_list"
    assert outside_link.is_symlink()
    assert target.read_text() == "keep"


@nh.function
def _edit(text: str) -> str:
    summary: str = ""
    """execute
    Read <text>, apply the patch, set <:summary>.
    """
    return summary


def test_file_patch_inside_a_run_records_perimeter_attempts_and_requires_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    grant = Grant((file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(workspace,)), file_writer=FileSystemWriter()),))

    def handler(call: StepRequest) -> StepCandidate:
        apply_file_patch = current_capabilities()["apply_file_patch"]
        outcome = apply_file_patch({"operations": [{"verb": "add", "file_path": str(workspace / "new.txt"), "new_text": "hi\n"}]})  # type: ignore[operator]
        assert isinstance(outcome, FilePatchOutcome)
        return StepCandidate(binding_name_to_value={"summary": f"applied {outcome.applied_operation_count}"})

    handle = RunHandle()
    result = run_body(
        lambda run_input: EchoResult(value=_edit(run_input.text)),
        runner=make_runner(),
        step_executor=CallbackExecutor(handler),
        run_entry_grant=grant,
        handle=handle,
    )
    assert result.value == "applied 1"
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "hi\n"
    attempt = handle.ledger.select(PerimeterEffectAttemptEvent)[0].event
    assert isinstance(attempt, PerimeterEffectAttemptEvent)
    assert attempt.perimeter_effect_class_tuple == ("destructive", "durable_write_back")

    with pytest.raises(RunRejectedError) as captured:
        run_body(
            lambda run_input: EchoResult(),
            runner=make_runner(perimeter_policy=PerimeterPolicy.permitting("egress")),
            run_entry_grant=grant,
            composition_perimeter_policy=PerimeterPolicy.permitting("egress"),
        )
    assert captured.value.reason == "capability_outside_perimeter_policy"
