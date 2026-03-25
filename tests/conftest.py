import os

import pytest
from _pytest.runner import runtestprotocol


def _is_integration_test_item(item: pytest.Item) -> bool:
    item_path = getattr(item, "path", None)
    if item_path is None:
        return False

    normalized_path = str(item_path).replace("\\", "/")
    return "/tests/integration/" in normalized_path


def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> object | None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        return None

    if not _is_integration_test_item(item):
        return None

    item_hook = item.ihook
    item_hook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)

    reports = runtestprotocol(item, log=False, nextitem=nextitem)

    for report in reports:
        item_hook.pytest_runtest_logreport(report=report)

    item_hook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
