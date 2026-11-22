"""Suite-wide fixtures. The host's real memory pressure must not decide test outcomes: resources.monitor reads a
fixed, unpressured memory sample unless a test replaces it (tests/test_resources.py). The owner's search API keys
(~/.config/neuroscifi/search) must not either: NSF_SEARCH_KEY_DIR points at an empty location for every test but
the live ones, so no test builds a keyed provider or spends a paid query unless it stores a key of its own."""
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


@pytest.fixture(autouse=True)
def no_owner_search_keys(request, monkeypatch, tmp_path_factory):
    if "live" not in request.keywords:
        monkeypatch.setenv("NSF_SEARCH_KEY_DIR", str(tmp_path_factory.getbasetemp() / "no-search-keys"))
