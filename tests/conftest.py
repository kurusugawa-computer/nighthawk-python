import importlib.metadata
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Generator
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nighthawk.runtime import StepOutcome, execute_step, execute_step_async
from nighthawk.runtime import scoping as runtime_scoping

pytest_plugins = ("tests.support.model_evaluation",)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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


@pytest.fixture(scope="module")
def package_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    installed_python = os.environ.get("NIGHTHAWK_DISTRIBUTION_PYTHON")
    if installed_python is None:
        return Path(sys.executable)
    executable = Path(installed_python).expanduser().absolute()
    assert executable.is_file(), f"Distribution interpreter does not exist: {executable}"
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import json, sys, nighthawk; print(json.dumps({'prefix': sys.prefix, 'package': nighthawk.__file__}))",
        ],
        cwd=tmp_path_factory.mktemp("distribution_origin"),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    origin_name_to_path = json.loads(probe.stdout)
    prefix = Path(origin_name_to_path["prefix"]).resolve()
    package = Path(origin_name_to_path["package"]).resolve()
    assert not prefix.is_relative_to(_REPOSITORY_ROOT), "Distribution environment must be outside the source tree"
    assert package.is_relative_to(prefix), f"Nighthawk must come from the distribution environment: {package}"
    return executable
