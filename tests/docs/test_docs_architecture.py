"""Docs architecture regression tests.

Guards against stale references, canonical example drift, and orphan pages.
"""

import re
from pathlib import Path

import pytest
import yaml
from markdown.extensions.toc import slugify as _toc_slugify

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS_DIR = _REPO_ROOT / "docs"
_MKDOCS_PATH = _REPO_ROOT / "mkdocs.yml"
_INDEX_PATH = _DOCS_DIR / "index.md"
_README_PATH = _REPO_ROOT / "README.md"


# -- Helpers ------------------------------------------------------------------


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


# -- Stale reference tests ----------------------------------------------------


_OBSOLETE_FILENAMES = {"guide.md", "design.md", "providers.md"}


class TestStaleReferences:
    """Fail on stale references to deleted/renamed docs."""

    @pytest.fixture()
    def docs_files(self) -> list[Path]:
        return list(_DOCS_DIR.glob("*.md"))

    def test_no_link_references_to_obsolete_files(self, docs_files: list[Path]) -> None:
        """No markdown link targets should reference deleted filenames."""
        for doc_file in docs_files:
            text = doc_file.read_text(encoding="utf-8")
            for obsolete in _OBSOLETE_FILENAMES:
                pattern = re.compile(rf"\({re.escape(obsolete)}[)#]")
                matches = pattern.findall(text)
                assert not matches, f"{doc_file.name} contains link reference to obsolete file {obsolete}: {matches}"

    def test_no_absolute_url_references_to_obsolete_pages(self) -> None:
        """for-coding-agents.md should not reference obsolete page URLs."""
        for_agents = (_DOCS_DIR / "for-coding-agents.md").read_text(encoding="utf-8")
        for obsolete_slug in ("guide", "design", "providers"):
            pattern = f"/nighthawk-python/{obsolete_slug}/"
            assert pattern not in for_agents, f"for-coding-agents.md contains absolute URL to obsolete page: {pattern}"

    def test_readme_no_obsolete_urls(self) -> None:
        """README.md should not reference obsolete page URLs."""
        readme = _README_PATH.read_text(encoding="utf-8")
        for obsolete_slug in ("guide", "design", "providers", "tutorial", "practices"):
            pattern = f"/nighthawk-python/{obsolete_slug}/"
            assert pattern not in readme, f"README.md contains URL to obsolete page: {pattern}"


# -- Canonical example tests --------------------------------------------------


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


# -- Nav vs docs file existence -----------------------------------------------


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

    def test_no_obsolete_files_remain_published(self) -> None:
        """Obsolete canonical pages must not remain in docs/ as publishable files."""
        for obsolete in _OBSOLETE_FILENAMES:
            path = _DOCS_DIR / obsolete
            assert not path.exists(), f"Obsolete file '{obsolete}' still exists in docs/ and may be published accidentally"


# -- Canonical ownership expectations -----------------------------------------


class TestCanonicalOwnership:
    """Guard selected canonical-owner expectations where drift is likely."""

    def test_prompt_example_anchors_in_natural_blocks(self) -> None:
        """basic-binding, fstring-injection, local-function-signature,
        global-function-reference must be in natural-blocks.md."""
        text = (_DOCS_DIR / "natural-blocks.md").read_text(encoding="utf-8")
        for anchor in ("basic-binding", "fstring-injection", "local-function-signature", "global-function-reference"):
            assert f"prompt-example:{anchor}" in text, f"prompt-example:{anchor} must be in natural-blocks.md"

    def test_carry_pattern_anchor_in_patterns(self) -> None:
        """carry-pattern must be in patterns.md, not natural-blocks.md."""
        patterns_text = (_DOCS_DIR / "patterns.md").read_text(encoding="utf-8")
        assert "prompt-example:carry-pattern" in patterns_text, "prompt-example:carry-pattern must be in patterns.md"

    def test_agents_md_excluded_from_nav(self) -> None:
        """AGENTS.md must not appear in mkdocs.yml nav."""
        config = yaml.safe_load(_MKDOCS_PATH.read_text(encoding="utf-8"))
        nav_files = _collect_nav_files(config["nav"])
        assert "AGENTS.md" not in nav_files, "AGENTS.md must not appear in mkdocs.yml nav"


# -- Boundary rules -----------------------------------------------------------


class TestBoundaryRules:
    """Guard only high-value boundary checks that affect discoverability."""

    def test_readme_links_to_core_docs_entry_points(self) -> None:
        """README.md should link to the main docs entry points, not mirror the whole nav."""
        readme_text = _README_PATH.read_text(encoding="utf-8")
        core_docs_pages = (
            "quickstart.md",
            "natural-blocks.md",
            "executors.md",
            "patterns.md",
            "coding-agent-backends.md",
            "philosophy.md",
        )
        for nav_file in core_docs_pages:
            slug = nav_file.removesuffix(".md")
            url_fragment = f"/nighthawk-python/{slug}/"
            assert url_fragment in readme_text, f"README.md is missing documentation link for '{nav_file}' (expected URL containing '{url_fragment}')"


# -- Philosophy structure -----------------------------------------------------


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

    def test_dependent_anchor_validity(self) -> None:
        """Dependent inbound links from other docs pages target anchors that must exist in philosophy.md."""
        valid_anchors = _heading_anchors((_DOCS_DIR / "philosophy.md").read_text(encoding="utf-8"))

        # #why-evaluate-every-time is referenced by natural-blocks.md and patterns.md.
        assert "why-evaluate-every-time" in valid_anchors, "philosophy.md must contain the heading 'Why evaluate every time'"
        for source in ("natural-blocks.md", "patterns.md"):
            text = (_DOCS_DIR / source).read_text(encoding="utf-8")
            assert "philosophy.md#why-evaluate-every-time" in text, f"{source} must link to philosophy.md#why-evaluate-every-time"

        # #execution-model is referenced by coding-agent-backends.md.
        assert "execution-model" in valid_anchors, "philosophy.md must contain the heading 'Execution model'"
        cab_text = (_DOCS_DIR / "coding-agent-backends.md").read_text(encoding="utf-8")
        assert "philosophy.md#execution-model" in cab_text, "coding-agent-backends.md must link to philosophy.md#execution-model"

    def test_self_reference_validity(self) -> None:
        """Every same-document anchor link in philosophy.md resolves."""
        text = (_DOCS_DIR / "philosophy.md").read_text(encoding="utf-8")
        valid_anchors = _heading_anchors(text)
        self_anchors = re.findall(r"\]\(#([A-Za-z0-9_-]+)\)", text)
        for anchor in self_anchors:
            assert anchor in valid_anchors, f"philosophy.md self-reference #{anchor} does not resolve. Valid anchors: {sorted(valid_anchors)}"
