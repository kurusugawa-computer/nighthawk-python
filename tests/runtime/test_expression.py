from __future__ import annotations

import pytest

from nighthawk.runtime import OperationError, eval_expression
from tests.support.contexts import new_context as context_for


@pytest.mark.parametrize("exception_type", [NameError, ValueError, TypeError])
def test_helper_failures_propagate_without_retry_conversion(exception_type: type[Exception]) -> None:
    error = exception_type("application failure")

    def fail() -> None:
        raise error

    with pytest.raises(exception_type) as caught:
        eval_expression(context_for(fail=fail), "fail()")
    assert caught.value is error


def test_lookup_failure_is_recoverable_and_named_assignment_is_rejected() -> None:
    context = context_for()
    with pytest.raises(OperationError) as caught:
        eval_expression(context, "missing")
    assert caught.value.phase == "evaluation"
    with pytest.raises(OperationError) as caught:
        eval_expression(context, "(value := 7)")
    assert caught.value.phase == "admission"
    assert not caught.value.evaluation_started
