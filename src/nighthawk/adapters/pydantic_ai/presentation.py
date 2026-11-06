from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from nighthawk.adapters.pydantic_ai._rendering import JsonRendererStyle


@dataclass(frozen=True)
class ToolResultRenderingPolicy:
    tokenizer_encoding_name: str
    tool_result_max_tokens: int
    json_renderer_style: JsonRendererStyle


DEFAULT_TOOL_RESULT_RENDERING_POLICY = ToolResultRenderingPolicy(
    tokenizer_encoding_name="o200k_base",
    tool_result_max_tokens=2_000,
    json_renderer_style="default",
)

_rendering_policy: ContextVar[ToolResultRenderingPolicy] = ContextVar("nighthawk_pydantic_ai_rendering", default=DEFAULT_TOOL_RESULT_RENDERING_POLICY)


def get_rendering_policy() -> ToolResultRenderingPolicy:
    return _rendering_policy.get()


@contextmanager
def rendering_scope(policy: ToolResultRenderingPolicy) -> Generator[None]:
    """Select rendering for one executor call, including transported tool calls."""
    token = _rendering_policy.set(policy)
    try:
        yield
    finally:
        _rendering_policy.reset(token)
