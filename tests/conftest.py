import importlib.metadata
from collections.abc import Awaitable, Callable, Generator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nighthawk.runtime import StepOutcome, execute_step, execute_step_async
from nighthawk.runtime import scoping as runtime_scoping


@pytest.fixture
def step_span_exporter() -> Generator[InMemorySpanExporter]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    previous_tracer_provider = runtime_scoping._tracer
    runtime_scoping._tracer = tracer_provider.get_tracer("nighthawk", importlib.metadata.version("nighthawk-python"))
    try:
        yield span_exporter
    finally:
        runtime_scoping._tracer = previous_tracer_provider


@pytest.fixture(params=[False, True], ids=["synchronous", "asynchronous"])
def execute(request: pytest.FixtureRequest) -> Callable[..., Awaitable[StepOutcome]]:
    if request.param:
        return execute_step_async

    async def synchronous(*arguments, **keywords) -> StepOutcome:
        return execute_step(*arguments, **keywords)

    return synchronous
