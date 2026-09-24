import re
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest

import nighthawk as nh
from nighthawk.testing import ScriptedExecutor, pass_response


@pytest.fixture
def guide_examples(tmp_path: Path) -> list[Path]:
    guide_path = Path(__file__).resolve().parents[2] / "docs" / "for-coding-agents.md"
    sources = re.findall(r"```py\n(.*?)```", guide_path.read_text(), re.DOTALL)
    paths: list[Path] = []
    for name, source in zip(("classify.py", "quotes.py", "test_quotes.py"), sources, strict=True):
        path = tmp_path / name
        path.write_text(source)
        paths.append(path)
    return paths


def test_working_template(guide_examples: list[Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    executor = ScriptedExecutor(responses=[pass_response(label="bug")])

    def build_executor(*, configuration: nh.StepExecutorConfiguration) -> ScriptedExecutor:
        assert isinstance(configuration, nh.StepExecutorConfiguration)
        return executor

    monkeypatch.setattr(nh.AgentStepExecutor, "from_configuration", staticmethod(build_executor))
    runpy.run_path(str(guide_examples[0]), run_name="__main__")
    assert capsys.readouterr().out == "bug\n"
    assert len(executor.calls) == 1


def test_published_quote_contracts(guide_examples: list[Path], monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("quotes")
    monkeypatch.setitem(sys.modules, "quotes", module)
    module.__dict__.update(runpy.run_path(str(guide_examples[1])))
    name_to_value = runpy.run_path(str(guide_examples[2]))
    tests = [value for name, value in name_to_value.items() if name.startswith("test_") and callable(value)]
    assert tests, "The guide must provide executable contract tests"
    for test in tests:
        test()
