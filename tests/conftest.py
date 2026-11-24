"""Suite-wide fixtures. The host's real memory pressure must not decide test outcomes: resources.monitor reads a
fixed, unpressured memory sample unless a test replaces it (tests/test_resources.py). The owner's search API keys
(~/.config/neuroscifi/search) must not either: NSF_SEARCH_KEY_DIR points at an empty location for every test but
the live ones, so no test builds a keyed provider or spends a paid query unless it stores a key of its own. Pocket
bridges started by tests put their `nsf pocket invite` control sockets under a per-session XDG_RUNTIME_DIR, never
the owner's."""
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


@pytest.fixture(autouse=True)
def no_host_memory_models(request, monkeypatch, tmp_path_factory):
    """The memory models that may run on this host (deploy/memory) must not decide test outcomes either: `nsf setup`
    looks for their key file under NSF_MEMORY_DIR and contacts nothing without it."""
    if "live" not in request.keywords:
        monkeypatch.setenv("NSF_MEMORY_DIR", str(tmp_path_factory.getbasetemp() / "no-memory-models"))


@pytest.fixture(autouse=True)
def private_runtime_dir(monkeypatch, tmp_path_factory):
    folder = tmp_path_factory.getbasetemp() / "runtime"
    folder.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(folder))
    monkeypatch.delenv("NSF_POCKET_CONTROL_DIR", raising=False)


# ---- no thread is left behind (tests/thread_check.py says why) --------------------------------------------------------

@pytest.fixture(autouse=True)
def _thread_births(request):
    import threading
    import thread_check
    before = set(threading.enumerate())
    yield
    thread_check.note_births(before, request.node.nodeid)


@pytest.fixture(scope="session", autouse=True)
def no_thread_is_left_behind():
    import thread_check
    yield
    rows = thread_check.left_behind()
    assert not rows, thread_check.MESSAGE + "\n".join(rows)
