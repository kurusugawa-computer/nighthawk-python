from __future__ import annotations

from nighthawk.core.usage import ExecutionUsage
from nighthawk.harness.ledger import DispatchEvent, NaturalExecutionOutcomeEvent
from nighthawk.harness.perimeter import EffectFootprint


def _dispatch_event(node_id: str) -> DispatchEvent:
    return DispatchEvent(
        dispatch_node_id=node_id,
        parent_node_id="root",
        recursion_depth=1,
        dispatch_kind="definition_only",
        defining_source_identity="abc",
        definition_id=None,
        export_name_tuple=("f",),
        dependency_name_tuple=(),
        effect_footprint=EffectFootprint(),
        grant_capability_name_tuple=(),
        confinement_identity="test",
    )


def _outcome(node_id: str, steps: int, tokens: int) -> NaturalExecutionOutcomeEvent:
    return NaturalExecutionOutcomeEvent(
        natural_execution_id=f"execution-{node_id}-{steps}",
        natural_block_identity="module:1",
        attribution_node_id=node_id,
        block_kind="execute",
        status="success",
        outcome_kind="pass",
        executor_step_count=steps,
        executor_usage=ExecutionUsage(request_count=steps, total_token_count=tokens),
    )
