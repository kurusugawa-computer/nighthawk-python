from pathlib import Path

from nighthawk.templates import include


def test_include_allows_docs_and_md_only(tmp_path: Path):
    repo = tmp_path
    (repo / "docs").mkdir()
    (repo / "tests").mkdir()
    (repo / "docs" / "a.md").write_text("hello", encoding="utf-8")

    assert include("docs/a.md", repo_root=repo) == "hello"


def test_include_rejects_traversal(tmp_path: Path):
    repo = tmp_path
    (repo / "docs").mkdir()

    try:
        include("docs/../secrets.md", repo_root=repo)
    except Exception as e:
        assert "traversal" in str(e)
    else:
        assert False, "expected exception"
