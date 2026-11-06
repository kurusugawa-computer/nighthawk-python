"""Complete atomic saves, ordinary errors, and existing host effect admission."""

from pathlib import Path

import pytest

from nighthawk.agent import PerimeterPolicy, RunFailedError, RunHandle, RunRejectedError, current_capabilities
from nighthawk.agent.tools._artifact_backends import DirectoryArtifactStore, InMemoryArtifactStore
from nighthawk.agent.tools.artifacts import LoadArtifactRequest, SaveArtifactRequest, StoredArtifactDescriptor, build_artifact_storage

from ..conftest import FailingAuditStore, make_runner
from ..support import EchoInput, EchoResult, run_body


@pytest.mark.parametrize("directory", [False, True])
def test_capacity_complete_loads_and_prior_save_preservation(tmp_path: Path, directory: bool) -> None:
    store = DirectoryArtifactStore(tmp_path, maximum_character_count=8) if directory else InMemoryArtifactStore(maximum_character_count=8)
    descriptor = store.put_text(("日本", "text"), media_type="text/plain")
    assert descriptor.character_count == 6
    assert store.load_text(descriptor, maximum_character_count=6) == "日本text"
    with pytest.raises(ValueError, match="capacity"):
        store.put_text(("longer",), media_type="text/plain")
    assert store.load_text(descriptor, maximum_character_count=6) == "日本text"
    with pytest.raises(ValueError, match="limit"):
        store.load_text(descriptor, maximum_character_count=5)
    with pytest.raises(ValueError, match="another store"):
        store.load_text(descriptor.model_copy(update={"store_id": "other"}), maximum_character_count=10)
    with pytest.raises(ValueError, match="identity"):
        store.load_text(descriptor.model_copy(update={"artifact_id": "../../secret"}), maximum_character_count=10)
    with pytest.raises(ValueError, match="match"):
        store.load_text(descriptor.model_copy(update={"media_type": "other"}), maximum_character_count=10)


def test_directory_reopens_and_failed_publication_preserves_prior_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    store = DirectoryArtifactStore(tmp_path)
    descriptor = store.put_text(("first",), media_type="text/plain")
    reopened = DirectoryArtifactStore(tmp_path)
    assert reopened.identity == store.identity
    assert reopened.load_text(descriptor, maximum_character_count=10) == "first"

    def reject_publication(source, destination) -> None:
        raise OSError("publication failed")

    monkeypatch.setattr(os, "link", reject_publication)
    with pytest.raises(OSError, match="publication failed"):
        store.put_text(("second",), media_type="text/plain")
    assert list(tmp_path.glob(".staging-*")) == []
    assert len(list(tmp_path.glob("*.artifact"))) == 1
    assert store.load_text(descriptor, maximum_character_count=10) == "first"
    with pytest.raises(FileNotFoundError):
        store.load_text(descriptor.model_copy(update={"artifact_id": "0" * 32}), maximum_character_count=10)


def test_storage_grant_defaults_to_read_only_and_bounds_complete_payloads() -> None:
    store = InMemoryArtifactStore()
    assert set(build_artifact_storage(store=store, maximum_payload_character_count=5).capabilities) == {"load_artifact"}
    grant = build_artifact_storage(store=store, maximum_payload_character_count=5, allow_write=True)
    save = grant.capabilities["save_artifact"].function
    descriptor = save(SaveArtifactRequest(text="hello", media_type="text/plain"))
    assert isinstance(descriptor, StoredArtifactDescriptor)
    assert grant.capabilities["load_artifact"].function(LoadArtifactRequest(descriptor=descriptor)) == "hello"
    with pytest.raises(ValueError):
        save(SaveArtifactRequest(text="longer", media_type="text/plain"))
    with pytest.raises(ValueError):
        build_artifact_storage(store=store, maximum_payload_character_count=0)


def test_host_denies_durable_authority_and_failed_attempt_audit_before_writing(tmp_path: Path) -> None:
    store = DirectoryArtifactStore(tmp_path)
    grant = build_artifact_storage(store=store, maximum_payload_character_count=100, allow_write=True)

    def body(run_input: EchoInput) -> EchoResult:
        current_capabilities()["save_artifact"](SaveArtifactRequest(text="handoff", media_type="application/json"))  # type: ignore[operator]
        return EchoResult()

    with pytest.raises(RunRejectedError):
        run_body(body, runner=make_runner(), run_entry_grant=grant, composition_perimeter_policy=PerimeterPolicy.NONE)
    with pytest.raises(RunFailedError):
        run_body(body, runner=make_runner(audit_store=FailingAuditStore()), run_entry_grant=grant)
    assert list(tmp_path.glob("*.artifact")) == []
    handle = RunHandle()
    run_body(body, runner=make_runner(), run_entry_grant=grant, handle=handle)
    assert len(list(tmp_path.glob("*.artifact"))) == 1
    kinds = [entry.event.kind for entry in handle.ledger.entries]
    assert kinds.index("perimeter_effect_attempt") < kinds.index("perimeter_effect_completion") < kinds.index("capability_invocation")


def test_completed_artifact_is_readable_in_a_new_process(tmp_path: Path) -> None:
    import subprocess
    import sys

    store = DirectoryArtifactStore(tmp_path)
    descriptor = store.put_text(("selected state",), media_type="text/plain")
    script = "from pathlib import Path; import sys; from nighthawk.agent.tools.artifacts import DirectoryArtifactStore, StoredArtifactDescriptor; store = DirectoryArtifactStore(Path(sys.argv[1])); print(store.load_text(StoredArtifactDescriptor.model_validate_json(sys.argv[2]), maximum_character_count=100))"
    process = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), descriptor.model_dump_json()], capture_output=True, text=True, check=True, timeout=10
    )
    assert process.stdout.strip() == "selected state"


def test_review_export_uses_the_same_storage_operation_without_execution() -> None:
    from nighthawk.agent import CodeProposal
    from nighthawk.agent.learning import ImprovementCandidate, format_candidate_review

    candidate = ImprovementCandidate(
        proposal=CodeProposal(source="def candidate() -> int:\n    raise AssertionError('must remain inert')\n", export_name_tuple=("candidate",)),
        intended_behavior="Review before trying",
        evidence_reference_tuple=(),
        validation_description="Not yet executed",
    )
    review = format_candidate_review(candidate, evidence=(), observed_checks="Formatting only")
    store = InMemoryArtifactStore()
    grant = build_artifact_storage(store=store, maximum_payload_character_count=10000, allow_write=True)
    descriptor = grant.capabilities["save_artifact"].function(SaveArtifactRequest(text=review, media_type="text/plain"))
    assert isinstance(descriptor, StoredArtifactDescriptor)
    assert candidate.proposal.source in store.load_text(descriptor, maximum_character_count=10000)
