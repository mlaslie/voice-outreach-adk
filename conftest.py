import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("VOA_RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live cloud test; set VOA_RUN_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _log_at_info(caplog):
    # Production logs at INFO; tests must exercise the same logging paths
    # (a LogRecord key collision once only failed at INFO, in the cloud).
    caplog.set_level("INFO")
