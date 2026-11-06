"""Resolve model-facing expressions through the common operation boundary."""

from nighthawk.adapters.pydantic_ai.model_outcome import ModelOutcome, ModelRaise, ModelReturn
from nighthawk.core.expression import evaluate_expression_operation
from nighthawk.core.operation_contract import OperationError
from nighthawk.core.operations import failed_operation
from nighthawk.core.step_outcome import Break, Continue, Pass, Raise, Return, StepOutcome
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.errors import NighthawkError


async def resolve_model_candidate(request: StepRequest, outcome: ModelOutcome) -> StepCandidate:
    """Resolve a provider response once; leave output selection and commit validation to the runner."""
    if outcome.kind not in request.allowed_outcome_kinds:
        raise NighthawkError(f"Step '{outcome.kind}' is not allowed for this step. Allowed kinds: {request.allowed_outcome_kinds}")
    resolved: StepOutcome
    if isinstance(outcome, ModelReturn):
        value = await evaluate_expression_operation(
            request.step_context,
            outcome.return_expression,
            purpose="return",
            allow_awaitable_result=request.allow_awaitable_return,
            expected_type=request.return_annotation,
        )
        resolved = Return(value)
    elif isinstance(outcome, ModelRaise) and outcome.raise_operation_id is not None:
        finished = failed_operation(request.step_context, operation_id=outcome.raise_operation_id)
        if finished is None or finished.original_exception is None:
            raise NighthawkError(f"No failed operation has raise_operation_id {outcome.raise_operation_id!r}")
        resolved = Raise.of(finished.original_exception)
    elif isinstance(outcome, ModelRaise):
        error_type: object = Exception
        if outcome.raise_error_type is not None:
            try:
                error_type = await evaluate_expression_operation(
                    request.step_context, outcome.raise_error_type, purpose="evaluation", allow_awaitable_result=False
                )
            except OperationError as exception:
                raise NighthawkError(f"Invalid raise_error_type: {outcome.raise_error_type!r}") from exception
        if not isinstance(error_type, type) or not issubclass(error_type, BaseException):
            raise NighthawkError(f"Invalid raise_error_type: {outcome.raise_error_type!r}")
        resolved = Raise(outcome.raise_message, error_type)
    else:
        resolved = {"pass": Pass, "break": Break, "continue": Continue}[outcome.kind]()
    return StepCandidate(outcome=resolved)
