import importlib.util
import re
from pathlib import Path


def test_operation_feedback_examples_execute_without_a_provider(tmp_path: Path) -> None:
    document = Path(__file__).resolve().parents[2] / "docs" / "operation-feedback.md"
    blocks = re.findall(r"```py\n(.*?)```", document.read_text(), flags=re.DOTALL)
    assert len(blocks) == 2
    source = tmp_path / "operation_feedback_examples.py"
    source.write_text("\n\n".join(blocks))
    specification = importlib.util.spec_from_file_location("operation_feedback_examples", source)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
