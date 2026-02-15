import os

import pytest
from _pytest.runner import runtestprotocol


def pytest_sessionstart(session: pytest.Session) -> None:
    _ = session

    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") == "1":
        has_openai = bool(os.getenv("OPENAI_API_KEY"))
        has_anthropic = bool(os.getenv("ANTHROPIC_AUTH_TOKEN"))

        if not has_openai and not has_anthropic:
            raise RuntimeError(
                "Integration tests require provider credentials. Set OPENAI_API_KEY for OpenAI integration tests and/or ANTHROPIC_AUTH_TOKEN (and ANTHROPIC_BASE_URL if applicable) for Claude Agent SDK integration tests."
            )


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

    max_attempts = 2
    reports: list[pytest.TestReport] = []

    for attempt_number in range(1, max_attempts + 1):
        reports = runtestprotocol(item, log=False, nextitem=nextitem)
        failed = any(report.failed for report in reports)

        if not failed:
            break

        if attempt_number == max_attempts:
            break

    for report in reports:
        item_hook.pytest_runtest_logreport(report=report)

    item_hook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
