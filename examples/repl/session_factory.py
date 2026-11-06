"""Lazy fixed model pairs for the terminal, with optional supplied source services."""

from itertools import product
from pathlib import Path

import nighthawk as nh
from nighthawk.agent import Grant, PerimeterPolicy
from nighthawk.agent.runtime import PerimeterAuditStore, PerimeterConfinement, Runner, TerminationMechanism, bounded_resource_allocation
from nighthawk.agent.session import SessionManager
from nighthawk.agent.tools import build_default_file_writer, build_inspect_workspace
from nighthawk.agent.tools.coding_actions import CodingActionPolicy, build_coding_actions

EXECUTION_MODEL_TUPLE = ("openai-responses:gpt-6-luna", "openai-responses:gpt-6.1-sol")
DECISION_MODEL_TUPLE = (None, "openai-decisions:gpt-6-luna")


def _runner(
    execution_model: str | None,
    decision_model: str | None,
    *,
    perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE,
    perimeter_confinement: PerimeterConfinement | None = None,
    perimeter_audit_store: PerimeterAuditStore | None = None,
    termination_mechanism: TerminationMechanism | None = None,
) -> Runner:
    selected_execution_model = EXECUTION_MODEL_TUPLE[0] if execution_model is None else execution_model
    if selected_execution_model not in EXECUTION_MODEL_TUPLE or decision_model not in DECISION_MODEL_TUPLE:
        raise ValueError("this factory permits only its explicitly configured model names")
    return Runner.from_models(
        model_configuration_tuple=tuple(
            nh.StepExecutorConfiguration(execution_model=execution_name, decision_model=decision_name, execution_model_settings={"max_tokens": 4000})
            for execution_name, decision_name in product(EXECUTION_MODEL_TUPLE, DECISION_MODEL_TUPLE)
        ),
        default_execution_model=selected_execution_model,
        default_decision_model=decision_model,
        perimeter_policy=perimeter_policy,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(
            identity=identity.run_id,
            maximum_recursion_depth=8,
            maximum_dispatch_count=24,
            maximum_natural_execution_count=24,
            maximum_executor_step_count=24,
            maximum_total_token_count=60000,
        ),
        perimeter_confinement=perimeter_confinement,
        perimeter_audit_store=perimeter_audit_store,
        termination_mechanism=termination_mechanism,
    )


def build_session_manager(*, execution_model: str | None = None, decision_model: str | None = None) -> SessionManager:
    """Honor the terminal model selections at construction and execute in the Session interpreter."""
    return SessionManager(runner=_runner(execution_model, decision_model))


def build_source_session_manager(
    *,
    execution_model: str | None,
    decision_model: str | None = None,
    directory: Path,
    perimeter_audit_store: PerimeterAuditStore,
    perimeter_confinement: PerimeterConfinement | None = None,
    termination_mechanism: TerminationMechanism | None = None,
) -> SessionManager:
    """Bind actual deployment services and a selected write directory before runs.

    A project terminal factory can call this with its own configured services.
    A confirmation double does not confine Python; an in-memory store is not durable.
    """
    grant = Grant.compose(
        build_inspect_workspace(workspace_root=directory),
        build_coding_actions(policy=CodingActionPolicy(path_write_allow_list=(directory.resolve(),)), file_writer=build_default_file_writer()),
    )
    policy = PerimeterPolicy.permitting(*grant.perimeter_effect_class_set)
    return SessionManager(
        runner=_runner(
            execution_model,
            decision_model,
            perimeter_policy=policy,
            perimeter_confinement=perimeter_confinement,
            perimeter_audit_store=perimeter_audit_store,
            termination_mechanism=termination_mechanism,
        ),
        run_entry_grant=grant,
        composition_perimeter_policy=policy,
    )
