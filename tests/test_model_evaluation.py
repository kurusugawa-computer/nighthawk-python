from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.model_evaluation import _json_value

_ROOT = Path(__file__).resolve().parents[1]


def _run(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONPATH": str(_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.support.model_evaluation", "-q", *arguments],
        cwd=directory,
        env=environment,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )


@pytest.fixture
def evaluation_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nasyncio_mode = "auto"\n')
    (tmp_path / "conftest.py").write_text("""
import pytest
import nighthawk as nh
from pydantic_ai.usage import RunUsage
from nighthawk.testing import CallbackExecutor, pass_response

@pytest.fixture(autouse=True)
def local_executor(monkeypatch):
    def respond(call):
        nh.get_usage_meter().record(RunUsage(input_tokens=11, output_tokens=7, requests=2))
        call.input_binding_name_to_value["items"].append(9)
        return pass_response(binding_name_to_value={"result": None})
    monkeypatch.setattr(nh.AgentStepExecutor, "resolve_model_selection", lambda self, selection: CallbackExecutor(respond))
""")
    (tmp_path / "test_trials.py").write_text("""
import pytest
from nighthawk.runtime import StepDefinition, execute_step_async

@pytest.fixture
def broken_setup():
    raise RuntimeError("setup failure")

@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown failure")

async def test_pass(model_evaluation):
    items = []
    committed = {}
    model_evaluation.name_to_observation.update(items=items, bindings=committed)
    await execute_step_async(StepDefinition("write", input_binding_names=("items",), output_binding_names=("result",)), name_to_value={"items": items}, write_binding=committed.__setitem__)
    assert items == [9]
    assert committed == {"result": None}

async def test_assertion_failure(model_evaluation):
    items = []
    model_evaluation.name_to_observation["items"] = items
    await execute_step_async(StepDefinition("write", input_binding_names=("items",), output_binding_names=("result",)), name_to_value={"items": items}, write_binding=lambda name, value: None)
    assert False, "incorrect task result"

def test_error(model_evaluation):
    raise RuntimeError("provider unavailable")

def test_explicit_failure(model_evaluation):
    pytest.fail("incorrect control result")

def test_skip(model_evaluation):
    pytest.skip("not applicable")

def test_setup_failure(broken_setup, model_evaluation):
    pass

def test_teardown_failure(model_evaluation, broken_teardown):
    pass
""")
    return tmp_path


def test_evaluation_records_every_repeat_and_separates_failures(evaluation_project: Path) -> None:
    output = evaluation_project / "records"
    result = _run(
        evaluation_project,
        "--evaluation-model",
        "test:first",
        "--evaluation-model",
        "test:second",
        "--evaluation-repeat",
        "2",
        "--evaluation-settings",
        '{"temperature": 0.2}',
        "--evaluation-output",
        str(output),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["trials"]) == 28
    assert manifest["versions"]["pytest"]
    assert manifest["source_sha256"]
    records = [json.loads(path.read_text()) for path in sorted(output.glob("trial-*.json"))]
    assert len(records) == 28
    assert {record["repeat"] for record in records} == {1, 2}
    assert {record["model"] for record in records} == {"test:first", "test:second"}
    assert all(record["settings"] == {"temperature": 0.2} for record in records)
    for record in records:
        if record["case"].endswith("::test_pass") or record["case"].endswith("::test_assertion_failure"):
            assert record["observations"]["items"] == [9]
            assert record["usage"] == {"requests": 2, "input_tokens": 11, "output_tokens": 7}
            assert record["steps"][0]["bindings"] == {"result": None}
        if record["case"].endswith("::test_teardown_failure"):
            assert record["status"] == "error"
            assert record["phases"]["call"]["outcome"] == "passed"
            assert "teardown failure" in record["phases"]["teardown"]["detail"]
        if record["case"].endswith("::test_setup_failure"):
            assert "setup failure" in record["phases"]["setup"]["detail"]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["counts"] == {"passed": 4, "failed": 8, "error": 12, "skipped": 4}
    assert len(summary["groups"]) == 14
    assert {group["pass_rate"] for group in summary["groups"]} == {None, 0.0, 1.0}
    assert sum(group["observed_usage"]["requests"] for group in summary["groups"]) == 16


def test_evaluation_is_disabled_without_explicit_models(evaluation_project: Path) -> None:
    result = _run(evaluation_project, "-k", "test_pass")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert not (evaluation_project / "evals").exists()


def test_evaluation_selection_and_collection_have_no_extra_trials(evaluation_project: Path) -> None:
    output = evaluation_project / "records"
    options = ("--evaluation-model", "test:model", "--evaluation-repeat", "3", "--evaluation-output", str(output))
    result = _run(evaluation_project, *options, "--collect-only", "-m", "model_evaluation", "-k", "test_pass")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3/21 tests collected" in result.stdout
    assert not output.exists()
    result = _run(evaluation_project, *options, "-m", "model_evaluation", "-k", "test_pass")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((output / "summary.json").read_text())["counts"] == {"passed": 3}
    result = _run(evaluation_project, *options, "-k", "test_pass")
    assert result.returncode == 4
    assert "Cannot create evaluation directory" in result.stderr


def test_early_stop_retains_unattempted_trials(evaluation_project: Path) -> None:
    output = evaluation_project / "records"
    result = _run(evaluation_project, "--evaluation-model", "test:model", "--evaluation-output", str(output), "-x")
    assert result.returncode == 1, result.stdout + result.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["counts"] == {"passed": 1, "failed": 1, "not_run": 5}


@pytest.mark.parametrize("interruption", ["KeyboardInterrupt", "asyncio.CancelledError"])
def test_interruption_keeps_usage_and_is_never_success(evaluation_project: Path, interruption: str) -> None:
    (evaluation_project / "test_trials.py").write_text(f"""
import asyncio
import nighthawk as nh
from pydantic_ai.usage import RunUsage

async def test_interrupt(model_evaluation):
    nh.get_usage_meter().record(RunUsage(input_tokens=11, output_tokens=7, requests=2))
    model_evaluation.name_to_observation["retained_effect"] = True
    raise {interruption}()

def test_after(model_evaluation):
    pass
""")
    output = evaluation_project / "records"
    result = _run(evaluation_project, "--evaluation-model", "test:model", "--evaluation-output", str(output))
    assert result.returncode == (2 if interruption == "KeyboardInterrupt" else 1), result.stdout + result.stderr
    record = json.loads((output / "trial-0001.json").read_text())
    assert record["status"] == "interrupted"
    assert record["observations"] == {"retained_effect": True}
    assert record["usage"] == {"requests": 2, "input_tokens": 11, "output_tokens": 7}
    assert json.loads((output / "summary.json").read_text())["counts"]["interrupted"] == 1


@pytest.mark.parametrize("option,value", [("--evaluation-repeat", "0"), ("--evaluation-settings", "[]"), ("--evaluation-settings", "invalid")])
def test_invalid_evaluation_options_fail_before_execution(evaluation_project: Path, option: str, value: str) -> None:
    result = _run(evaluation_project, option, value)
    assert result.returncode == 4
    assert not (evaluation_project / "evals").exists()


def test_diagnostic_serialization_does_not_execute_object_representation() -> None:
    class Opaque:
        def __repr__(self) -> str:
            pytest.fail("Serialization must not invoke application representations")

    assert _json_value(Opaque()) == {"python_type": "test_diagnostic_serialization_does_not_execute_object_representation.<locals>.Opaque"}
    cyclic: list[object] = []
    cyclic.append(cyclic)
    assert "serialization_error" in _json_value(cyclic)
