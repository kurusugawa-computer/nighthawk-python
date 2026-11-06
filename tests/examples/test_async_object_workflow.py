import asyncio
from dataclasses import replace

import pytest

from examples.async_object_workflow import (
    AnalysisConfiguration,
    AnalysisRequest,
    BatchFailure,
    BatchFinding,
    SampleBatch,
    WorkflowInput,
    analyze_batches,
    run_async_object_workflow,
)
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.context import current_capabilities
from nighthawk.harness.errors import RunFailedError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_handle import RunHandle
from nighthawk.parts.procedures import materialize_procedure
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureSelection
from tests.examples.workflow_support import ANALYSIS_SOURCE
from tests.support.harness import make_runner
from tests.support.runs import EchoInput, EchoResult, run_body


class OverlappingProposalExecutor:
    def __init__(self, batches: tuple[SampleBatch, ...], configuration: AnalysisConfiguration) -> None:
        self.barrier = asyncio.Barrier(len(batches))
        self.batches = batches
        self.configuration = configuration
        self.request_list: list[AnalysisRequest] = []

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        step_context = request.step_context
        workflow_request = step_context.step_locals["request"]
        assert isinstance(workflow_request, AnalysisRequest)
        assert any(workflow_request.batch is batch for batch in self.batches)
        assert workflow_request.configuration is self.configuration
        assert current_capabilities() == {}
        self.request_list.append(workflow_request)
        await asyncio.wait_for(self.barrier.wait(), timeout=5)
        proposal = CodeProposal(source=ANALYSIS_SOURCE, export_name_tuple=("analyze",))
        return StepCandidate(outcome=Pass(), binding_name_to_value={"proposal": proposal})


def test_async_batch_proposals_overlap_and_keep_explicit_object_references() -> None:
    batches = (SampleBatch(batch_id="a", sample_tuple=(1, -2)), SampleBatch(batch_id="b", sample_tuple=(-3, 0)))
    configuration = AnalysisConfiguration()
    executor = OverlappingProposalExecutor(batches, configuration)
    result = asyncio.run(
        run_async_object_workflow(
            WorkflowInput(batch_tuple=batches, configuration=configuration),
            catalog=ProcedureCatalog(),
            selection=ProcedureSelection(procedure_id=None, reason="Propose"),
            run_entry_grant=Grant.EMPTY,
            runner=replace(make_runner(), step_executor=executor),
        )
    )
    assert [finding.selected_index_value_tuple for finding in result.result_tuple if isinstance(finding, BatchFinding)] == [((1, -2),), ((0, -3),)]
    assert len(executor.request_list) == 2
    configuration.label = "shared mutation"
    assert all(request.configuration.label == "shared mutation" for request in executor.request_list)


def test_ordinary_branch_failure_preserves_successful_neighbor_in_input_order() -> None:
    catalog = ProcedureCatalog()
    barrier = asyncio.Barrier(2)
    finished = []

    async def meet(value: int) -> int:
        await asyncio.wait_for(barrier.wait(), timeout=5)
        finished.append(value)
        return value

    capability = Capability(name="meet", function=meet, authority_boundary="cooperative numeric meeting")
    proposal = CodeProposal(
        source="async def analyze(samples: tuple[int, ...], criterion: int) -> tuple[tuple[int, int], ...]:\n    value = await meet(samples[0])\n    if value == 0:\n        raise ValueError('ordinary analysis failure')\n    return ((0, value),)\n",
        export_name_tuple=("analyze",),
        dependency_tuple=(DependencyDeclaration(name="meet", kind="capability", declared_type=capability.declared_type),),
        effect_footprint=EffectFootprint(capability_name_set=frozenset({"meet"})),
    )
    reports = []

    def body(run_input: EchoInput) -> EchoResult:
        selection = materialize_procedure(catalog, proposal, export_name="analyze", purpose="cooperative fixture")
        reports.append(
            asyncio.run(
                analyze_batches(
                    WorkflowInput(
                        batch_tuple=(SampleBatch(batch_id="failure", sample_tuple=(0,)), SampleBatch(batch_id="success", sample_tuple=(-2,)))
                    ),
                    catalog=catalog,
                    selection=selection,
                )
            )
        )
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(capability))
    assert sorted(finished) == [-2, 0]
    assert isinstance(reports[0].result_tuple[0], BatchFailure)
    assert isinstance(reports[0].result_tuple[1], BatchFinding)
    assert reports[0].result_tuple[1].selected_index_value_tuple == ((0, -2),)


def test_allocation_exhaustion_propagates_instead_of_becoming_batch_failure() -> None:
    handle = RunHandle()
    runner = make_runner(allocation_factory=lambda identity: bounded_resource_allocation(maximum_natural_execution_count=0))
    with pytest.raises(RunFailedError):
        asyncio.run(
            run_async_object_workflow(
                WorkflowInput(batch_tuple=(SampleBatch(batch_id="one", sample_tuple=(-1,)), SampleBatch(batch_id="two", sample_tuple=(-2,)))),
                catalog=ProcedureCatalog(),
                selection=ProcedureSelection(procedure_id=None, reason="Propose"),
                run_entry_grant=Grant.EMPTY,
                handle=handle,
                runner=replace(runner, step_executor=ScriptedExecutor(default_response=StepCandidate())),
            )
        )
    assert handle.ledger.entries[-1].event.kind == "run_end"


def test_host_termination_cancels_and_joins_cooperative_sibling() -> None:
    handle = RunHandle()
    barrier = asyncio.Barrier(2)
    sibling_waiting = asyncio.Event()
    joined = []

    class TerminatingExecutor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            workflow_request = step_context.step_locals["request"]
            assert isinstance(workflow_request, AnalysisRequest)
            await asyncio.wait_for(barrier.wait(), timeout=5)
            if workflow_request.batch.batch_id == "terminate":
                await asyncio.wait_for(sibling_waiting.wait(), timeout=5)
                handle.terminate("test host termination")
                proposal = CodeProposal(source=ANALYSIS_SOURCE, export_name_tuple=("analyze",))
                return StepCandidate(outcome=Pass(), binding_name_to_value={"proposal": proposal})
            try:
                sibling_waiting.set()
                await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
            finally:
                joined.append(workflow_request.batch.batch_id)
            raise AssertionError("Sibling should be cancelled")

    with pytest.raises(RunFailedError) as failure:
        asyncio.run(
            run_async_object_workflow(
                WorkflowInput(
                    batch_tuple=(SampleBatch(batch_id="terminate", sample_tuple=(0,)), SampleBatch(batch_id="sibling", sample_tuple=(-1,)))
                ),
                catalog=ProcedureCatalog(),
                selection=ProcedureSelection(procedure_id=None, reason="Propose"),
                run_entry_grant=Grant.EMPTY,
                handle=handle,
                runner=replace(make_runner(), step_executor=TerminatingExecutor()),
            )
        )
    assert failure.value.terminal_kind == "host_termination"
    assert joined == ["sibling"]
