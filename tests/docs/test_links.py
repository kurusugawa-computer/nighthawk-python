import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _RenderedPage(HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.anchor_set: set[str] = set()
        self.reference_list: list[str] = []
        self.feed(path.read_text(encoding="utf-8"))

    # Preserve HTMLParser's keyword-compatible parameter name.
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name == "id" or (tag == "a" and name == "name"):
                self.anchor_set.add(value)
            if name in {"href", "src"}:
                self.reference_list.append(value)


def test_published_internal_links_and_anchors_resolve(tmp_path: Path) -> None:
    site_directory = tmp_path / "site"
    completed = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--site-dir", str(site_directory)],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    configuration = yaml.safe_load((_REPOSITORY_ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
    site_url = configuration["site_url"].rstrip("/") + "/"
    site_address = urlsplit(site_url)
    path_to_page = {path: _RenderedPage(path) for path in site_directory.rglob("*.html")}
    assert path_to_page, "MkDocs must render published pages"
    failures: list[str] = []
    for path, page in path_to_page.items():
        page_url = urljoin(site_url, path.relative_to(site_directory).as_posix())
        for reference in page.reference_list:
            address = urlsplit(urljoin(page_url, reference))
            if address.netloc != site_address.netloc:
                continue
            source = path.relative_to(site_directory)
            if not address.path.startswith(site_address.path):
                original_address = urlsplit(reference)
                if not original_address.scheme and not original_address.netloc:
                    failures.append(f"{source}: {reference} escapes the published site")
                continue
            target = site_directory / unquote(address.path.removeprefix(site_address.path))
            if target.is_dir():
                target /= "index.html"
            if not target.is_file():
                failures.append(f"{source}: {reference} does not resolve")
            elif address.fragment and target in path_to_page and unquote(address.fragment) not in path_to_page[target].anchor_set:
                failures.append(f"{source}: {reference} has no matching anchor")
    assert not failures, "\n".join(failures)
