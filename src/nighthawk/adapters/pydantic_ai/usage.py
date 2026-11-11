from pydantic_ai.usage import RequestUsage, RunUsage

from nighthawk.core.usage import ExecutionUsage


def execution_usage(usage: RequestUsage | RunUsage) -> ExecutionUsage:
    """Project provider usage into the common accounting contract.

    Pydantic AI reports reasoning tokens in ``output_reasoning_tokens`` or, for some providers, only under the
    ``reasoning_tokens`` detail; the larger of the two is kept.
    """
    return ExecutionUsage(
        request_count=max(0, usage.requests if isinstance(usage, RunUsage) else 1),
        input_token_count=max(0, usage.input_tokens),
        cache_read_token_count=max(0, usage.cache_read_tokens),
        cache_write_token_count=max(0, usage.cache_write_tokens),
        output_token_count=max(0, usage.output_tokens),
        reasoning_token_count=max(0, usage.output_reasoning_tokens, usage.details.get("reasoning_tokens", 0)),
        total_token_count=max(0, usage.total_tokens),
    )
