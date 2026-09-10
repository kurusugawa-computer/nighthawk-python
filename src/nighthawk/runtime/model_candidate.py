"""Resolve model-facing expressions through the common operation boundary."""

from collections.abc import Mapping

from ..errors import ExecutionError
from .expression import evaluate_expression_operation
from .operation_contract import OperationError
from .step_contract import RaiseStepOutcome, ReturnStepOutcome, StepOutcome
from .step_request import StepCandidate, StepRequest
from .step_result import Break, Continue, Pass, Raise, Return, StepResult


async def resolve_model_candidate(
    request: StepRequest,
    outcome: StepOutcome,
    binding_name_to_value: Mapping[str, object],
) -> StepCandidate:
    """Resolve a provider response once; leave commit validation to the runner."""
    if outcome.kind not in request.allowed_step_kinds:
        raise ExecutionError(f"Step '{outcome.kind}' is not allowed for this step. Allowed kinds: {request.allowed_step_kinds}")
    undeclared = binding_name_to_value.keys() - set(request.output_binding_names)
    if undeclared:
        raise ExecutionError(f"Undeclared output binding names: {sorted(undeclared)}")
    resolved: StepResult
    if isinstance(outcome, ReturnStepOutcome):
        value = await evaluate_expression_operation(
            request.step_context,
            outcome.return_expression,
            purpose="return",
            allow_awaitable_result=request.allow_awaitable_return,
            expected_type=request.return_annotation,
            override_name_to_value=binding_name_to_value,
        )
        resolved = Return(value)
    elif isinstance(outcome, RaiseStepOutcome):
        error_type: object = Exception
        if outcome.raise_error_type is not None:
            try:
                error_type = await evaluate_expression_operation(
                    request.step_context, outcome.raise_error_type, purpose="evaluation", allow_awaitable_result=False
                )
            except OperationError as exception:
                raise ExecutionError(f"Invalid raise_error_type: {outcome.raise_error_type!r}") from exception
        if not isinstance(error_type, type) or not issubclass(error_type, BaseException):
            raise ExecutionError(f"Invalid raise_error_type: {outcome.raise_error_type!r}")
        resolved = Raise(outcome.raise_message, error_type)
    else:
        resolved = {"pass": Pass, "break": Break, "continue": Continue}[outcome.kind]()
    return StepCandidate(outcome=resolved, binding_name_to_value=binding_name_to_value)
