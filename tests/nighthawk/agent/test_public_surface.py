"""Pin the public surfaces of ``nighthawk.agent`` and its submodules."""

from __future__ import annotations

import importlib.util
import pkgutil
from types import ModuleType

import pytest

import nighthawk.agent.catalog as catalog_module
import nighthawk.agent.conversation as conversation_module
import nighthawk.agent.errors as errors_module
import nighthawk.agent.generated as generated_module
import nighthawk.agent.generation as generation_module
import nighthawk.agent.history as history_module
import nighthawk.agent.learning as learning_module
import nighthawk.agent.procedures as procedures_module
import nighthawk.agent.reference as reference_module
import nighthawk.agent.runtime as runtime_module
import nighthawk.agent.session as session_module
import nighthawk.agent.session.errors as session_errors_module
import nighthawk.agent.sources as sources_module
import nighthawk.agent.tools as tools_module
import nighthawk.agent.tools.artifacts as artifacts_module
import nighthawk.agent.values as values_module
from nighthawk import agent

EXPECTED_ERROR_NAMES = {
    "CapabilityRejectedError",
    "DispatchRejectedError",
    "DispatchRejectionReason",
    "GeneratedExecutionError",
    "GeneratedExecutionFailureKind",
    "GrantError",
    "GrantErrorReason",
    "NaturalExecutionError",
    "NaturalExecutionFailureKind",
    "PerimeterEffectRejectedError",
    "PerimeterEffectRejectionReason",
    "RunFailedError",
    "RunRejectedError",
    "RunRejectionReason",
    "RunTerminalKind",
    "RunTerminationError",
    "TerminationError",
    "TerminationFailureReason",
}

EXPECTED_TOOLS_NAMES = {
    "CodingActionPolicy",
    "artifacts",
    "build_artifact_storage",
    "build_coding_actions",
    "build_default_file_writer",
    "build_default_web_fetch_client",
    "build_inspect_workspace",
    "build_research_web",
    "coding_actions",
    "inspect_workspace",
    "requests",
    "research_web",
}

EXPECTED_RUNTIME_NAMES = {
    "ApprovedFunctionDefinition",
    "AsyncScaffoldComposition",
    "ConfinementConfirmation",
    "ConfinementRequest",
    "ExecutorStepAdmission",
    "ExecutorStepObservation",
    "InMemoryPerimeterAuditStore",
    "PerimeterAuditRecord",
    "PerimeterAuditStore",
    "PerimeterConfinement",
    "ResourceAllocation",
    "ResourceConsumption",
    "ResourceRejection",
    "ResourceRequest",
    "ResourceRequestKind",
    "RunContract",
    "RunHandle",
    "RunIdentity",
    "Runner",
    "ScaffoldComposition",
    "TerminationMechanism",
    "TerminationReport",
    "TerminationRequest",
    "bounded_resource_allocation",
    "unavailable_termination_mechanism",
    "unconfirmed_perimeter_confinement",
}


@pytest.mark.parametrize(
    "module",
    [
        agent,
        errors_module,
        procedures_module,
        reference_module,
        runtime_module,
        session_module,
        session_errors_module,
        tools_module,
        values_module,
        history_module,
        catalog_module,
        conversation_module,
        generated_module,
        generation_module,
        sources_module,
        learning_module,
        artifacts_module,
    ],
)
def test_all_names_are_unique_and_exported(module: ModuleType) -> None:
    public_names = module.__all__
    assert len(public_names) == len(set(public_names))
    for name in public_names:
        assert hasattr(module, name), f"public name {name!r} is missing from {module!r}"


def test_error_surface_is_pinned() -> None:
    assert set(errors_module.__all__) == EXPECTED_ERROR_NAMES


def test_tools_surface_is_pinned() -> None:
    assert set(tools_module.__all__) == EXPECTED_TOOLS_NAMES


def test_root_surface_covers_substrate_and_control_plane() -> None:
    for name in ("Capability", "Grant", "CodeProposal", "dispatch", "scope", "RunLedger", "runtime"):
        assert name in agent.__all__
    assert "execute_code_capability" in agent.__all__
    assert "terminate_descendant_capability" in agent.__all__


def test_runtime_owns_service_surface_and_retired_entries_are_not_importable() -> None:
    assert set(runtime_module.__all__) == EXPECTED_RUNTIME_NAMES
    for name in EXPECTED_RUNTIME_NAMES - {
        "ApprovedFunctionDefinition",
        "AsyncScaffoldComposition",
        "RunContract",
        "RunHandle",
        "ScaffoldComposition",
        "ExecutorStepObservation",
    }:
        assert getattr(runtime_module, name).__module__ == runtime_module.__name__
    assert runtime_module.RunHandle is agent.RunHandle
    assert runtime_module.RunContract is agent.RunContract
    assert runtime_module.ScaffoldComposition is agent.ScaffoldComposition
    for module in (agent, runtime_module):
        for name in ("Platform", "run_agent", "platform"):
            assert name not in module.__all__
            assert not hasattr(module, name)
    assert "platform" not in {module.name for module in pkgutil.iter_modules(agent.__path__)}
    assert importlib.util.find_spec("nighthawk.agent.platform") is None
    with pytest.raises(ModuleNotFoundError, match="nighthawk.agent.platform"):
        importlib.import_module("nighthawk.agent.platform")


def test_no_loop_framework_symbols_remain() -> None:
    assert not hasattr(agent, "run_agent_in_context")
    assert not hasattr(agent, "decide_next")
    assert not hasattr(agent, "ActionProposal")
    assert not hasattr(agent, "CompletionGateDecision")
    assert not hasattr(agent, "DispatchHandle")
    assert importlib.util.find_spec("nighthawk.agent.tools.dispatch") is None
