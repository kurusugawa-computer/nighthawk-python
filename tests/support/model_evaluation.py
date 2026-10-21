from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Generator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic_core import to_jsonable_python

import nighthawk as nh
from nighthawk.lifecycle import StepFinished, StepLifecycle


def _json_value(value: object) -> Any:
    try:
        return to_jsonable_python(
            value,
            fallback=lambda item: {"python_type": f"{item.__module__}.{item.__qualname__}" if isinstance(item, type) else type(item).__qualname__},
        )
    except (TypeError, ValueError) as exception:
        return {"serialization_error": str(exception), "python_type": type(value).__qualname__}


@dataclass
class ModelEvaluation:
    configuration: nh.StepExecutorConfiguration
    name_to_observation: dict[str, object] = field(default_factory=dict)
    steps: list[StepFinished] = field(default_factory=list)
    usage: nh.UsageMeter = field(default_factory=nh.UsageMeter)


@dataclass
class _Trial:
    path: Path
    record: dict[str, Any]
    evaluation: ModelEvaluation | None = None
    started_at: float | None = None

    def save(self) -> None:
        if self.started_at is not None:
            self.record["seconds"] = time.monotonic() - self.started_at
        if self.evaluation is not None:
            evaluation = self.evaluation
            usage = evaluation.usage.snapshot()
            self.record.update(
                observations=_json_value(evaluation.name_to_observation),
                usage={"requests": usage.requests, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                steps=[
                    {
                        "kind": step.kind,
                        "execution": _json_value(step.execution_reference),
                        "program": step.processed_natural_program,
                        "bindings": _json_value(dict(step.assigned_binding_name_to_value)),
                        "outcome": _json_value(getattr(step, "outcome", None)),
                        "failure_stage": getattr(step, "failure_stage", None),
                        "messages": json.loads(step.model_result.all_messages_json()) if step.model_result is not None else None,
                    }
                    for step in evaluation.steps
                ],
            )
        self.path.write_text(json.dumps(self.record, indent=2, ensure_ascii=False) + "\n")


_TRIAL = pytest.StashKey[_Trial]()
_TRIALS = pytest.StashKey[list[_Trial]]()
_SETTINGS = pytest.StashKey[dict[str, Any]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("model evaluation")
    group.addoption("--evaluation-model", action="append", default=[], help="Run model evaluations against this model; repeat to compare models.")
    group.addoption("--evaluation-repeat", type=int, default=1, help="Execute every selected model case this many times.")
    group.addoption("--evaluation-settings", default="{}", help="JSON execution_model_settings applied to each selected model.")
    group.addoption("--evaluation-output", help="New directory for trial records and summary; defaults to evals/outputs/<timestamp>.")


def pytest_configure(config: pytest.Config) -> None:
    if config.option.evaluation_repeat < 1:
        raise pytest.UsageError("--evaluation-repeat must be positive")
    try:
        settings = json.loads(config.option.evaluation_settings)
    except json.JSONDecodeError as exception:
        raise pytest.UsageError("--evaluation-settings must be a JSON object") from exception
    if not isinstance(settings, dict):
        raise pytest.UsageError("--evaluation-settings must be a JSON object")
    if config.getoption("evaluation_output") and not config.getoption("evaluation_model"):
        raise pytest.UsageError("--evaluation-output requires --evaluation-model")
    config.stash[_SETTINGS] = settings
    config.addinivalue_line("markers", "model_evaluation: explicitly selected, repeated live-model evaluation")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "model_evaluation" in metafunc.fixturenames:
        models = metafunc.config.option.evaluation_model
        metafunc.parametrize("evaluation_model", list(dict.fromkeys(models)) or [None])
        repeats = range(1, metafunc.config.option.evaluation_repeat + 1) if models else [1]
        metafunc.parametrize("evaluation_repeat", repeats, ids=lambda value: f"trial-{value}")


def _source_manifest(root: Path) -> dict[str, Any]:
    paths = [*root.glob("src/**/*.py"), *root.glob("tests/**/*.py"), root / "pyproject.toml", root / "uv.lock"]
    path_to_digest = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths) if path.is_file()}
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    distribution_to_version = {}
    for name in ("nighthawk-python", "pytest", "pytest-asyncio", "pydantic", "pydantic-ai-slim", "openai", "mcp", "claude-agent-sdk"):
        with suppress(importlib.metadata.PackageNotFoundError):
            distribution_to_version[name] = importlib.metadata.version(name)
    return {
        "revision": revision.stdout.strip() or None,
        "source_sha256": hashlib.sha256(json.dumps(path_to_digest, sort_keys=True).encode()).hexdigest(),
        "files": path_to_digest,
        "python": platform.python_version(),
        "versions": distribution_to_version,
    }


@pytest.hookimpl(wrapper=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> Generator[None]:
    for item in items:
        if isinstance(item, pytest.Function) and "model_evaluation" in item.fixturenames:
            item.add_marker("model_evaluation")
    yield
    selected = [item for item in items if isinstance(item, pytest.Function) and "model_evaluation" in item.fixturenames]
    if not config.getoption("evaluation_model"):
        return
    if not selected or config.option.collectonly:
        return
    output = config.getoption("evaluation_output")
    directory = Path(output) if output else config.rootpath / "evals/outputs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except OSError as exception:
        raise pytest.UsageError(f"Cannot create evaluation directory {directory}: {exception}") from exception
    trials = []
    for i, item in enumerate(selected):
        parameters = item.callspec.params
        case_parameters = {name: _json_value(value) for name, value in parameters.items() if name not in ("evaluation_model", "evaluation_repeat")}
        record = {
            "test": item.nodeid,
            "case": f"{item.location[0]}::{item.originalname}",
            "parameters": case_parameters,
            "model": parameters["evaluation_model"],
            "repeat": parameters["evaluation_repeat"],
            "settings": config.stash[_SETTINGS],
            "status": "not_run",
            "phases": {},
        }
        trial = _Trial(directory / f"trial-{i + 1:04}.json", record)
        item.stash[_TRIAL] = trial
        trials.append(trial)
    config.stash[_TRIALS] = trials
    manifest = _source_manifest(config.rootpath)
    manifest.update(started_at=datetime.now(UTC).isoformat(), trials=[trial.record["test"] for trial in trials])
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


@pytest.fixture
def model_evaluation(request: pytest.FixtureRequest, evaluation_model: str | None, evaluation_repeat: int) -> Generator[ModelEvaluation]:
    if evaluation_model is None:
        pytest.skip("Select live models with --evaluation-model")
    trial = request.node.stash[_TRIAL]
    configuration = nh.StepExecutorConfiguration(execution_model=evaluation_model, execution_model_settings=request.config.stash[_SETTINGS])
    evaluation = ModelEvaluation(configuration)
    trial.evaluation = evaluation
    with (
        nh.run(step_executor_configuration=configuration, usage_meter=evaluation.usage),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=evaluation.steps.append)),
    ):
        yield evaluation


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    if _TRIAL in item.stash:
        trial = item.stash[_TRIAL]
        trial.started_at = time.monotonic()
        trial.record["status"] = "running"
        trial.save()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if _TRIAL not in item.stash:
        return report
    trial = item.stash[_TRIAL]
    phase = {"outcome": report.outcome, "seconds": report.duration}
    if not report.passed:
        phase["detail"] = report.longreprtext
    trial.record["phases"][report.when] = phase
    if report.failed:
        status = (
            "failed"
            if call.when == "call" and call.excinfo is not None and call.excinfo.errisinstance((AssertionError, pytest.fail.Exception))
            else "error"
        )
        if call.excinfo is not None and call.excinfo.errisinstance(asyncio.CancelledError):
            status = "interrupted"
        trial.record["status"] = status
    elif report.skipped and trial.record["status"] not in ("failed", "error"):
        trial.record["status"] = "skipped"
    elif report.when == "teardown" and trial.record["status"] == "running":
        trial.record["status"] = "passed"
    trial.save()
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    trials = session.config.stash.get(_TRIALS, [])
    if not trials:
        return
    group_to_record_list: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        if trial.record["status"] == "running":
            trial.record["status"] = "interrupted"
            trial.save()
        elif trial.record["status"] == "not_run":
            trial.save()
        record = trial.record
        group_to_record_list[(record["model"], record["case"], json.dumps(record["parameters"], sort_keys=True))].append(record)
    groups = []
    for (model, case, parameters), records in group_to_record_list.items():
        counts = Counter(record["status"] for record in records)
        attempted = len(records) - counts["not_run"] - counts["skipped"]
        groups.append(
            {
                "model": model,
                "case": case,
                "parameters": json.loads(parameters),
                "counts": dict(counts),
                "attempted": attempted,
                "pass_rate": counts["passed"] / attempted if attempted else None,
                "seconds": sum(record.get("seconds", 0) for record in records),
                "observed_usage": {
                    name: sum(record.get("usage", {}).get(name, 0) for record in records) for name in ("requests", "input_tokens", "output_tokens")
                },
            }
        )
    summary = {"exit_status": int(exitstatus), "counts": dict(Counter(trial.record["status"] for trial in trials)), "groups": groups}
    (trials[0].path.parent / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    trials = config.stash.get(_TRIALS, [])
    if trials:
        terminalreporter.write_sep("=", "model evaluation")
        terminalreporter.write_line(str(dict(Counter(trial.record["status"] for trial in trials))))
        terminalreporter.write_line(f"Records: {trials[0].path.parent}")
