from pydantic_ai.usage import RequestUsage


def normalize_claude_code_usage(usage: object) -> RequestUsage:
    request_usage = RequestUsage()
    if not isinstance(usage, dict):
        return request_usage

    input_tokens = usage.get("input_tokens")
    if isinstance(input_tokens, int):
        request_usage.input_tokens = input_tokens

    output_tokens = usage.get("output_tokens")
    if isinstance(output_tokens, int):
        request_usage.output_tokens = output_tokens

    cache_read_input_tokens = usage.get("cache_read_input_tokens")
    if isinstance(cache_read_input_tokens, int):
        request_usage.cache_read_tokens = cache_read_input_tokens

    cache_creation_input_tokens = usage.get("cache_creation_input_tokens")
    if isinstance(cache_creation_input_tokens, int):
        request_usage.cache_write_tokens = cache_creation_input_tokens

    # Anthropic reports uncached input separately; Pydantic AI includes all input tokens.
    request_usage.input_tokens += request_usage.cache_read_tokens + request_usage.cache_write_tokens
    return request_usage
