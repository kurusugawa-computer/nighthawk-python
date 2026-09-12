"""Documentation integrity tests.

Check navigation targets, canonical examples, anchors, and documented API members.
"""

import ast
import re
from pathlib import Path

import pytest
import yaml
from markdown.extensions.toc import slugify as _toc_slugify

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DOCS_DIR = _REPOSITORY_ROOT / "docs"
_MKDOCS_PATH = _REPOSITORY_ROOT / "mkdocs.yml"
_INDEX_PATH = _DOCS_DIR / "index.md"
_README_PATH = _REPOSITORY_ROOT / "README.md"


def _extract_python_code_blocks(text: str) -> list[str]:
    """Extract all fenced Python code blocks from markdown text."""
    pattern = re.compile(r"```py\n(.*?)```", re.DOTALL)
    return [m.group(1).strip() for m in pattern.finditer(text)]


def _heading_anchors(text: str) -> set[str]:
    """Compute heading anchors from markdown text using the MkDocs-aligned slug helper."""
    return {_toc_slugify(match.group(1).strip(), "-") for match in re.finditer(r"^#{2,}\s+(.*)$", text, re.MULTILINE)}


def _collect_nav_files(nav: list) -> set[str]:
    """Recursively collect all file paths from a mkdocs nav structure."""
    files: set[str] = set()
    for entry in nav:
        if isinstance(entry, str):
            files.add(entry)
        elif isinstance(entry, dict):
            for value in entry.values():
                if isinstance(value, str):
                    files.add(value)
                elif isinstance(value, list):
                    files.update(_collect_nav_files(value))
    return files


class TestCanonicalExample:
    """Guard the canonical example relationship between index.md and README.md."""

    def test_index_and_readme_share_canonical_example(self) -> None:
        """The first Python code block in index.md and README.md must match."""
        index_blocks = _extract_python_code_blocks(_INDEX_PATH.read_text(encoding="utf-8"))
        readme_blocks = _extract_python_code_blocks(_README_PATH.read_text(encoding="utf-8"))

        assert len(index_blocks) >= 1, "index.md must have at least one code block"
        assert len(readme_blocks) >= 1, "README.md must have at least one code block"

        # The canonical example is the first Python code block in each file.
        index_example = index_blocks[0]
        readme_example = readme_blocks[0]

        assert index_example == readme_example, (
            f"Canonical example in index.md and README.md must match.\nindex.md:\n{index_example}\n\nREADME.md:\n{readme_example}"
        )


class TestNavStructure:
    """Automate mkdocs.yml nav entries vs docs/ file existence."""

    @pytest.fixture()
    def mkdocs_config(self) -> dict:
        return yaml.safe_load(_MKDOCS_PATH.read_text(encoding="utf-8"))

    def test_all_nav_files_exist(self, mkdocs_config: dict) -> None:
        """Every file listed in mkdocs.yml nav must exist in docs/."""
        nav_files = _collect_nav_files(mkdocs_config["nav"])
        for nav_file in nav_files:
            assert (_DOCS_DIR / nav_file).exists(), f"Nav entry '{nav_file}' does not exist in docs/"


class TestPhilosophyStructure:
    """Guard philosophy.md link integrity without freezing its prose structure."""

    def test_inbound_reference_validity(self) -> None:
        """Every philosophy.md#... link in docs/ resolves to a valid anchor."""
        valid_anchors = _heading_anchors((_DOCS_DIR / "philosophy.md").read_text(encoding="utf-8"))
        for doc_file in _DOCS_DIR.glob("*.md"):
            text = doc_file.read_text(encoding="utf-8")
            for match in re.finditer(r"philosophy\.md#([-a-z0-9]+)", text):
                anchor = match.group(1)
                assert anchor in valid_anchors, (
                    f"{doc_file.name} links to philosophy.md#{anchor} which does not exist. Valid anchors: {sorted(valid_anchors)}"
                )

    def test_self_reference_validity(self) -> None:
        """Every same-document anchor link in philosophy.md resolves."""
        text = (_DOCS_DIR / "philosophy.md").read_text(encoding="utf-8")
        valid_anchors = _heading_anchors(text)
        self_anchors = re.findall(r"\]\(#([A-Za-z0-9_-]+)\)", text)
        for anchor in self_anchors:
            assert anchor in valid_anchors, f"philosophy.md self-reference #{anchor} does not resolve. Valid anchors: {sorted(valid_anchors)}"


def test_explicit_api_members_exist_in_their_documented_modules() -> None:
    """MkDocs silently omits unknown members; validate every explicit inventory entry."""
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
