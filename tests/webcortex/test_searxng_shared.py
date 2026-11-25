"""`nsf web searxng` by an account that may read the instance database but not write it (§6 item 2 of
docs/acceleration/ENGINEERING_PLAN.md): the account that runs the containers in a two-account install. The real
accounts are exercised by scripts/two_account_check.py; here one account and a database it may only read."""
import hashlib
import json
import os

import pytest

from neuroscifi.instance import store
from neuroscifi.instance.cli import with_db
from neuroscifi.webcortex import cli as wcli
from neuroscifi.webcortex.providers import searxng as sx

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root writes every file")


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    from neuroscifi.cli import main
    calls = []
    monkeypatch.setattr(sx.Container, "start", lambda self, **kw: calls.append(("start", self.port)) or
                        {"started": True, "url": self.base_url})
    monkeypatch.setattr(sx.Container, "stop", lambda self: calls.append(("stop", self.port)) or {"stopped": True})
    monkeypatch.setattr(sx.Container, "status", lambda self: {"container": sx.CONTAINER_NAME, "running": True})
    # The reserved ports read this account's NSF_..._PORT settings and its service overrides: none from the host.
    for name in list(os.environ):
        if name.startswith("NSF_") and (name.endswith("PORT") or name.endswith("_DIR")):
            monkeypatch.delenv(name)
    overrides = tmp_path / "config"
    for variable, folder in (("NSF_INFERENCE_DIR", "inference"), ("NSF_MEMORY_DIR", "memory"),
                             ("NSF_VOICE_DIR", "voice")):
        monkeypatch.setenv(variable, str(overrides / folder))
    db = tmp_path / "individual.sqlite"

    def nsf(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, (json.loads(out.out) if code == 0 else out.err)

    assert nsf("init", "--db", str(db))[0] == 0

    class Box:
        pass
    b = Box()
    b.db, b.calls, b.nsf, b.overrides = db, calls, nsf, overrides
    def lock():
        """What deploy/install.sh --idle-access leaves the other account: the database to read, and nothing to
        create beside it (the folder is the service account's)."""
        os.chmod(db, 0o440)
        os.chmod(tmp_path, 0o550)

    def unlock():
        os.chmod(tmp_path, 0o700)
        os.chmod(db, 0o640)
    b.lock, b.unlock = lock, unlock
    b.digest = lambda: hashlib.sha256(db.read_bytes()).hexdigest()
    yield b
    unlock()


def test_start_by_the_reading_account_starts_searxng_and_writes_nothing(box):
    assert box.nsf("web", "config", "--enable", "--set", 'searxng_url="http://127.0.0.1:18080"',
                   "--db", str(box.db))[0] == 0
    box.lock()
    before, mtime = box.digest(), box.db.stat().st_mtime_ns
    code, out = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 0, out
    assert box.calls == [("start", 18080)] and out["started"] is True
    assert out["database"].startswith("read only") and "note" not in out
    assert box.digest() == before and box.db.stat().st_mtime_ns == mtime
    code, out = box.nsf("web", "searxng", "status", "--db", str(box.db))
    assert code == 0 and out["searxng"] == {"configured": True, "url": "http://127.0.0.1:18080",
                                            "container": {"container": sx.CONTAINER_NAME, "running": True}}
    code, out = box.nsf("web", "searxng", "stop", "--db", str(box.db))
    assert code == 0 and box.calls[-1] == ("stop", 18080)
    assert box.digest() == before


def test_the_ordinary_path_could_not_start_it_for_that_account(box):
    """What the unit ran before: the instance opened to write (migration, the sweep, searxng_url on a first start)."""
    assert box.nsf("web", "config", "--enable", "--db", str(box.db))[0] == 0
    box.lock()
    args = wcli.argparse.Namespace(db=box.db, action="start", probe=False, command="web")
    assert with_db(wcli._network_errors(wcli._searxng_cmd))(args) != 0
    assert box.calls == []


def test_a_first_start_without_searxng_url_says_what_the_owner_runs_once(box):
    assert box.nsf("web", "config", "--enable", "--db", str(box.db))[0] == 0
    box.lock()
    before = box.digest()
    code, out = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 0 and box.calls == [("start", sx.DEFAULT_PORT)] and out["configured"] is None
    assert f"nsf web config --db {box.db} --set 'searxng_url=\"{sx.DEFAULT_URL}\"'" in out["note"]
    assert box.digest() == before
    box.unlock()                                             # the owner runs it
    assert box.nsf("web", "config", "--db", str(box.db), "--set", f'searxng_url="{sx.DEFAULT_URL}"')[0] == 0
    db = store.open_existing(box.db)
    try:
        from neuroscifi.webcortex import state
        assert state.get_config(db).searxng_url == sx.DEFAULT_URL
    finally:
        db.close()


def test_web_access_off_is_honoured_and_stop_still_works(box):
    box.lock()
    code, err = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 2 and "disabled" in err and box.calls == []
    code, out = box.nsf("web", "searxng", "stop", "--db", str(box.db))
    assert code == 0 and box.calls == [("stop", sx.DEFAULT_PORT)]


def test_health_stays_with_the_account_that_owns_the_database(box):
    box.lock()
    code, err = box.nsf("web", "searxng", "health", "--probe", "--db", str(box.db))
    assert code == 2 and "may only read" in err and box.calls == []


def test_the_account_that_owns_the_database_keeps_the_ordinary_path(box):
    """A single-account install: a first start still configures SearXNG, as in 0.7.0."""
    assert box.nsf("web", "config", "--enable", "--db", str(box.db))[0] == 0
    code, out = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 0 and out["configured"] == sx.DEFAULT_URL and "database" not in out


def _tamper(path, sql, *args):
    os.chmod(path.parent, 0o700)
    os.chmod(path, 0o640)
    db = store.connect(path)
    try:
        db.execute(sql, args)
    finally:
        db.close()


@pytest.mark.parametrize("value, why", [
    ("[" * 200_000 + "]" * 200_000, "malformed"),
    ('["not", "an", "object"]', "not an object"),
    ('{"enabled": "yes"}', "wrong type"),
    ('{"enabled": true, "searxng_url": 5}', "wrong type"),
    ('{"enabled": true, "searxng_url": "http://0.0.0.0:8080"}', "not usable"),
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:99999"}', "not usable"),
    # a port the account that runs containers must not be made to take (review finding L6)
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:8811"}', "not allowed"),      # the answer model
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:18441"}', "not allowed"),     # the memory models
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:18431"}', "not allowed"),     # voice
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:8765"}', "not allowed"),      # Pocket
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:8812"}', "not allowed"),      # the TensorRT-LLM window
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:8814"}', "not allowed"),
    ('{"enabled": true, "searxng_url": "http://127.0.0.1:631"}', "not allowed"),       # privileged
    ('{"enabled": true, "searxng_url": "http://127.0.0.1"}', "not allowed"),           # no port: 80
])
def test_a_configuration_the_other_account_wrote_badly_is_an_error_not_a_crash_or_a_public_port(box, value, why):
    _tamper(box.db, "INSERT OR REPLACE INTO meta(key, value) VALUES ('webcortex', ?)", value)
    box.lock()
    code, err = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 2 and why in err and box.calls == []


def test_a_view_in_place_of_the_meta_table_or_a_fifo_beside_the_database_is_not_read(box):
    os.mkfifo(f"{box.db}-wal")
    box.lock()
    code, err = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 2 and "not a plain file" in err and box.calls == []
    box.unlock()
    os.unlink(f"{box.db}-wal")
    _tamper(box.db, "ALTER TABLE meta RENAME TO meta_kept")
    _tamper(box.db, "CREATE VIEW meta AS SELECT key, value FROM meta_kept")
    box.lock()
    code, err = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 2 and "no meta table" in err and box.calls == []


@pytest.mark.parametrize("how", ["environment", "override file"])
def test_a_port_an_override_moved_a_service_to_is_refused_too(box, monkeypatch, how):
    """Re-review (L6 residue): the answer model, memory or voice moved to another port by NSF_..._PORT (in the
    environment, or in the owner's override that deploy/*/run.sh reads) is as reserved as its default."""
    if how == "environment":
        monkeypatch.setenv("NSF_VLLM_PORT", "18500")
    else:
        folder = box.overrides / "memory"
        folder.mkdir(parents=True)
        (folder / "memory.env").write_text("# moved\nNSF_MEMORY_SERVER=single\nexport NSF_MEMORY_EMBED_PORT='18500'\n")
    assert box.nsf("web", "config", "--enable", "--set", 'searxng_url="http://127.0.0.1:18500"',
                   "--db", str(box.db))[0] == 0
    box.lock()
    code, err = box.nsf("web", "searxng", "start", "--db", str(box.db))
    assert code == 2 and "not allowed" in err and box.calls == []
    assert 18080 not in wcli._reserved_ports()       # another port is still allowed

