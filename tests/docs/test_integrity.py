import ast
import re
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DOCS_DIR = _REPOSITORY_ROOT / "docs"
_INDEX_PATH = _DOCS_DIR / "index.md"
_README_PATH = _REPOSITORY_ROOT / "README.md"


def _extract_python_code_blocks(text: str) -> list[str]:
    pattern = re.compile(r"```py\n(.*?)```", re.DOTALL)
    return [m.group(1).strip() for m in pattern.finditer(text)]


class TestCanonicalExample:
    def test_index_and_readme_share_canonical_example(self) -> None:
        index_blocks = _extract_python_code_blocks(_INDEX_PATH.read_text(encoding="utf-8"))
        readme_blocks = _extract_python_code_blocks(_README_PATH.read_text(encoding="utf-8"))

        assert len(index_blocks) >= 1, "index.md must have at least one code block"
        assert len(readme_blocks) >= 1, "README.md must have at least one code block"

        index_example = index_blocks[0]
        readme_example = readme_blocks[0]

        assert index_example == readme_example, (
            f"Canonical example in index.md and README.md must match.\nindex.md:\n{index_example}\n\nREADME.md:\n{readme_example}"
        )


def test_explicit_api_members_exist_in_their_documented_modules() -> None:
    """MkDocs silently omits unknown members."""
    api_text = (_DOCS_DIR / "api.md").read_text(encoding="utf-8")
    module_sections = re.split(r"^::: ([\w.]+)\s*$", api_text, flags=re.MULTILINE)
    for module_name, section in zip(module_sections[1::2], module_sections[2::2], strict=True):
        member_names = re.findall(r"^        - (\w+)\s*$", section, flags=re.MULTILINE)
        if not member_names:
            continue
        module_path = _REPOSITORY_ROOT / "src" / module_name.replace(".", "/")
        source_path = module_path.with_suffix(".py")
        if not source_path.is_file():
            source_path = module_path / "__init__.py"
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        defined_names: set[str] = set()
        for statement in module.body:
            if isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                defined_names.add(statement.name)
            elif isinstance(statement, ast.ImportFrom):
                defined_names.update(alias.asname or alias.name for alias in statement.names)
            elif isinstance(statement, ast.Import):
                defined_names.update(alias.asname or alias.name.split(".")[0] for alias in statement.names)
            elif isinstance(statement, ast.Assign):
                defined_names.update(target.id for target in statement.targets if isinstance(target, ast.Name))
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                defined_names.add(statement.target.id)
            elif isinstance(statement, ast.TypeAlias):
                defined_names.add(statement.name.id)
        missing_names = set(member_names) - defined_names
        assert not missing_names, f"API members missing from {module_name}: {sorted(missing_names)}"
