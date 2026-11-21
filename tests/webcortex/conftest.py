"""Web Cortex test fixtures. Unit tests never touch the real network: they use FixtureWeb or fakes.

Tests marked `live` contact the real internet and run only with NSF_LIVE=1.
"""
import os

import pytest

from neuroscifi.webcortex.fixtureweb import FixtureWeb


def pytest_collection_modifyitems(config, items):
    if os.environ.get("NSF_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live internet test; set NSF_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def web():
    """A started FixtureWeb; register routes with web.add(host, path, body)."""
    w = FixtureWeb().start()
    try:
        yield w
    finally:
        w.stop()
