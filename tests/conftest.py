"""Suite-wide fixtures. The host's real memory pressure must not decide test outcomes: resources.monitor reads a
fixed, unpressured memory sample unless a test replaces it (tests/test_resources.py)."""
import pytest

GIB = 1024 ** 3
STEADY = {"total_bytes": 128 * GIB, "available_bytes": 64 * GIB, "swap_total_bytes": 0, "swap_free_bytes": 0,
          "psi": None}


@pytest.fixture(autouse=True)
def steady_host_memory(monkeypatch):
    from neuroscifi.resources import monitor
    monkeypatch.setattr(monitor, "memory", lambda *a, **k: dict(STEADY))
    monitor.clear_cache()
    yield
    monitor.clear_cache()
