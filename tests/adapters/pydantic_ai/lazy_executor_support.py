from dataclasses import replace

import nighthawk as nh
from nighthawk.harness.resources import ResourceAllocation, bounded_resource_allocation
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from tests.support.harness import make_runner
from tests.support.natural_calls import LazyReportingExecutor


@nh.function
def _answer() -> int:
    result: int = 0
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Set <:result> to 7.
    """
    return result


@nh.function
async def _answer_async() -> int:
    result: int = 0
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Set <:result> to 7.
    """
    return result


def _run(
    executor: LazyReportingExecutor,
    handle: RunHandle,
    *,
    limit: int = 24,
    calls: int = 1,
    allocation: ResourceAllocation | None = None,
) -> int:
    def body(value: int) -> int:
        result = 0
        for _ in range(calls):
            result = _answer()
        return result

    return replace(
        make_runner(
            accepts_aggregate_executor_accounting=False,
            allocation_factory=lambda identity: (
                allocation
                if allocation is not None
                else bounded_resource_allocation(
                    identity=identity.run_id,
                    maximum_executor_step_count=limit,
                    maximum_total_token_count=60000,
                )
            ),
        ),
        step_executor=executor,
    ).run(ScaffoldComposition(RunContract(int, int), body), 0, handle=handle)
