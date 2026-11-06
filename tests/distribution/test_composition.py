import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.environ.get("NIGHTHAWK_DISTRIBUTION_PYTHON"), reason="Requires an installed distribution outside the source tree")
def test_installed_harness_parts_and_sessions(package_python: Path, tmp_path: Path) -> None:
    script = """
import asyncio
import importlib.metadata
import importlib.util
from pathlib import Path
import sys

import nighthawk
assert 'nighthawk.harness' not in sys.modules
assert 'nighthawk.adapters' not in sys.modules
assert 'pydantic_ai' not in sys.modules
import nighthawk.harness
import nighthawk.harness.runner
import nighthawk.parts.conversation
import nighthawk.parts.generation
import nighthawk.parts.sources
assert 'nighthawk.session' not in sys.modules
import nighthawk.session
from nighthawk.harness.perimeter import PerimeterPolicy; from nighthawk.harness.context import current_executor_step_admission
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract, ScaffoldComposition; from nighthawk.harness.run_handle import RunHandle; from nighthawk.harness.runner import Runner
from nighthawk.session import CellInput, SessionManager, create_session; from nighthawk.parts.conversation import ConversationResult, UserFacingBlock
from nighthawk.core.step_request import StepCandidate

for module in (nighthawk.harness, nighthawk.harness.runner, nighthawk.session):
    origin = Path(module.__file__).resolve()
    assert origin.is_relative_to(Path(sys.prefix).resolve()) and 'site-packages' in origin.parts
distribution = importlib.metadata.distribution('nighthawk-python')
assert 'http' in distribution.metadata.get_all('Provides-Extra')
for role in ('core', 'harness', 'parts', 'session', 'adapters'):
    assert any(str(path) == f'nighthawk/{role}/__init__.py' for path in distribution.files)
assert importlib.util.find_spec('prompt_toolkit') is None
assert importlib.util.find_spec('rich') is None
session = create_session(execution_model="openai-responses:gpt-6-luna")
execution = session.submit(CellInput('> samples = [1, 2, 3]'))
assert execution.outcome is None
outcome = execution.wait()
assert outcome.status == 'success' and outcome.run_id_tuple == ()
assert session.binding_name_to_value['samples'] == [1, 2, 3]
assert execution.wait() is outcome
assert session.close() and session.close()

class Executor:
    admits_executor_steps = True
    async def run_step_async(self, **arguments):
        admission = current_executor_step_admission()
        admission.admit()
        await asyncio.sleep(0)
        admission.observe()
        result = ConversationResult((UserFacingBlock('answer', 'The sum is 6.'),), {'total': 6})
        return StepCandidate(binding_name_to_value={'result': result})

runner = Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation(maximum_executor_step_count=1))
handle = RunHandle()
assert runner.run(ScaffoldComposition(RunContract(int, int), lambda value: value + 1), 6, run_id='installed-sync', handle=handle) == 7
assert handle.run_id == 'installed-sync'

class ApplicationState:
    pass
state = ApplicationState()
assert runner.run(ScaffoldComposition(RunContract(ApplicationState, ApplicationState), lambda value: value), state) is state

from nighthawk.harness.proposals import CodeProposal
from nighthawk.parts.procedures.catalog import ProcedureCatalog
from nighthawk.harness.evidence import EvidenceReference, resolve_evidence
from nighthawk.parts.procedures import materialize_procedure, selected_procedure

def compose(value):
    catalog = ProcedureCatalog()
    selected = materialize_procedure(catalog, CodeProposal(source="def increment(value: int) -> int: return value + 1", export_name_tuple=("increment",)), export_name="increment", purpose="Increment")
    with selected_procedure(catalog, selected) as procedure:
        return procedure(value)
handle = RunHandle()
assert runner.run(ScaffoldComposition(RunContract(int, int), compose), 6, handle=handle) == 7
entry = next(entry for entry in handle.ledger.entries if entry.event.kind == 'dispatch' and entry.event.dispatch_kind == 'invocation')
assert resolve_evidence(handle.ledger, EvidenceReference(run_id=handle.run_id, event_id=entry.event_id, value_path_tuple=('argument_snapshot', 'positional', 0))) == 6
from nighthawk.harness.generated import retain_function
from nighthawk.parts.conversation import ConversationInput, ConversationStep, choose_conversation_step
proposal = CodeProposal(source="def increment(value: int) -> int: return value + 1", export_name_tuple=("increment",))
retained = runner.run(ScaffoldComposition(RunContract(type(None), object), lambda value: retain_function(proposal, export_name="increment")), None)
assert runner.run(ScaffoldComposition(RunContract(int, object), retained), 6) == 7
assert runner.run(ScaffoldComposition(RunContract(int, object), retained), 9) == 10
async def increment(value):
    await asyncio.sleep(0)
    return value + 1
handle = RunHandle()
pending = runner.run(AsyncScaffoldComposition(RunContract(int, int), increment), 6, run_id='installed-async', handle=handle)
assert handle.run_id is None
assert asyncio.run(pending) == 7 and handle.run_id == 'installed-async'
session = SessionManager(runner=runner).create_session()
assert session.submit(CellInput('> samples = [1, 2, 3]')).wait().status == 'success'
outcome = session.submit(CellInput('Explain samples and assign its sum to <:total>.')).wait()
assert outcome.status == 'success' and len(outcome.run_id_tuple) == 1
assert session.binding_name_to_value['total'] == 6
assert session.read_run_evidence(outcome.run_id_tuple[0]).evidence_tuple
assert session.close()
print('installed Session acceptance passed')
"""

    def run(arguments: list[str]) -> None:
        completed = subprocess.run(arguments, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr

    run([str(package_python), "-I", "-c", script])
    adapter_script = tmp_path / "installed_session_adapters.py"
    adapter_script.write_text(Path(__file__).with_name("installed_session_adapters.py").read_text())
    run([str(package_python), "-I", str(adapter_script)])
