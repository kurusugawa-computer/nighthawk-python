from pathlib import Path

from .composed_support import combined_scenario


def test_all_features_with_saved_continuation(tmp_path: Path) -> None:
    combined_scenario(tmp_path)
