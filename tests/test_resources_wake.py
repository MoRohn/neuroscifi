"""Second-pass review of the reload path (resources/idle.py), findings S2, S3, S6 and S8:

S2  the process that asks for a reload needs nothing of the idle policy's state, and never creates or re-modes the
    request folder; the serving side decides and limits;
S3  an entry that is not a request cannot loop the wake unit;
S6  the memory models' settings files are read as bash reads them, and nothing is read while the module is imported;
S8  a lookup never waits for the lock of a process that is starting a reload.

The fake host, its clock and the request-folder fixture are those of tests/test_resources_idle.py."""
import fcntl
import os
import stat
import subprocess
import sys
import threading

import pytest

from neuroscifi.resources import idle
from test_resources_idle import _unload_idle, fake, memory_host, spool      # noqa: F401 (fixtures)


# ---- S2 ------------------------------------------------------------------------------------------------------------

def test_a_process_that_asks_never_creates_the_wake_folder_and_never_changes_its_mode(spool, monkeypatch, tmp_path):  # noqa: F811
    fake = spool      # noqa: F811
    _unload_idle(fake, "embed", "rerank")
    os.chmod(fake.spool, 0o3770)                                   # as deploy/install.sh --idle makes it
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    assert (fake.spool / "embed.wake").read_bytes() == b""
    assert stat.S_IMODE(fake.spool.stat().st_mode) == 0o3770       # honoured, not forced back to 0700
    missing = tmp_path / "not-made-yet" / "wake"
    monkeypatch.setenv("NSF_WAKE_DIR", str(missing))
    idle._DETACHED.clear()
    with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
        assert state["action"] == "failed" and "does not exist" in state["why"] and "NSF_WAKE_DIR" in state["why"]
    assert not missing.exists() and not missing.parent.exists()    # whoever creates it first would own it: not us


def test_the_wake_command_makes_a_missing_folder_for_its_group_and_leaves_an_existing_one_alone(spool, tmp_path):  # noqa: F811
    fake = spool      # noqa: F811
    made = tmp_path / "spool" / "wake"
    assert idle.serve_wake_requests(fake.path, made) == []
    # setgid for the group that may ask; sticky, so one asker cannot remove another's request (only the owner can)
    assert stat.S_IMODE(made.stat().st_mode) == idle.WAKE_DIR_MODE == 0o3770
    for mode in (0o700, 0o750, 0o2770, 0o2775):
        os.chmod(fake.spool, mode)
        assert idle.serve_wake_requests(fake.path, fake.spool) == []
        assert stat.S_IMODE(fake.spool.stat().st_mode) == mode     # the installer's or the owner's choice stands


def test_with_the_policys_files_unreadable_a_refused_connection_still_gets_the_service_reloaded(spool):  # noqa: F811
    """The two-account shape: the idle policy's files belong to the account that runs the services (0700, 0600).
    The asking process then reads no state at all: it sees "policy off", makes its call, the connection is
    refused, and that alone leaves the request. The serving account decides from its own state."""
    fake = spool      # noqa: F811
    _unload_idle(fake, "embed")
    state_dir = fake.make().folder
    before = sorted(p.name for p in state_dir.iterdir())
    os.chmod(state_dir, 0)
    try:
        if os.access(state_dir, os.R_OK):
            pytest.skip("this account reads anything (root): the unreadable case cannot be made here")
        assert idle.Idle(fake.path).read() == {"timeouts": {}, "services": {}}      # nothing can be known
        with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
            assert state is None                                    # so the call is made ...
        said = idle.asked_on_refusal(18441)                         # ... and is refused: the gateway asks
        assert "its start was asked for" in said and (fake.spool / "embed.wake").exists()
        assert "a moment ago" in idle.asked_on_refusal(18441)       # not once per failed call
        assert idle.asked_on_refusal(11434) is None and not (fake.spool / "11434.wake").exists()   # not our service
    finally:
        os.chmod(state_dir, 0o700)
    assert sorted(p.name for p in state_dir.iterdir()) == before    # the asking process wrote nothing there
    served = idle.serve_wake_requests(fake.path, fake.spool)        # the serving account, with its own state
    assert served == [{"service": "embed", "action": "load", "why": "on demand: wake request"}]
    assert "neuroscifi-memory-embed" in fake.running and not (fake.spool / "embed.wake").exists()


def test_with_the_policys_folder_read_only_a_lookup_asks_without_writing_there(spool):  # noqa: F811
    """Readable but not writable (a group that may read): the request file is the only thing written."""
    fake = spool      # noqa: F811
    _unload_idle(fake, "rerank")
    state_dir = fake.make().folder
    before = sorted(p.name for p in state_dir.iterdir())
    os.chmod(state_dir, 0o500)
    try:
        if os.access(state_dir, os.W_OK):
            pytest.skip("this account writes anywhere (root)")
        with idle.using_now(fake.path, "rerank", purpose="memory lookup") as state:
            assert state["action"] == "loading", state
    finally:
        os.chmod(state_dir, 0o700)
    assert (fake.spool / "rerank.wake").exists() and sorted(p.name for p in state_dir.iterdir()) == before


def test_the_serving_side_applies_the_retry_limit_and_the_policy_itself(spool):  # noqa: F811
    fake = spool      # noqa: F811
    _unload_idle(fake, "embed")
    fake.fail_wait.add("neuroscifi-memory-embed")
    (fake.spool / "embed.wake").write_text("")
    first = idle.serve_wake_requests(fake.path, fake.spool)
    assert first[0]["action"] == "failed"
    (fake.spool / "embed.wake").write_text("")                      # asked again at once, by a process that could
    again = idle.serve_wake_requests(fake.path, fake.spool)         # not see that it had just failed
    assert [item["action"] for item in again] == ["skipped"] and again[0]["service"] == "embed"
    assert again[0]["why"].startswith("not retried within 30 s of: ") and not (fake.spool / "embed.wake").exists()
    starts = [c for c in fake.calls if "start" in [str(a) for a in c]]
    assert len(starts) == 1
    fake.fail_wait.clear()
    fake.now += idle.BACKGROUND_RETRY + 1
    (fake.spool / "embed.wake").write_text("")
    assert idle.serve_wake_requests(fake.path, fake.spool)[0]["action"] == "load"


# ---- S3 ------------------------------------------------------------------------------------------------------------

def test_a_folder_or_anything_else_under_a_requests_name_is_removed_or_moved_aside(spool):  # noqa: F811
    fake = spool      # noqa: F811
    _unload_idle(fake, "embed")
    (fake.spool / "rerank.wake").mkdir()                            # the reviewer's entry
    (fake.spool / "vllm.wake").mkdir()
    (fake.spool / "vllm.wake" / "kept").write_text("x")            # not empty: cannot be removed
    os.mkfifo(fake.spool / "asr.wake")
    (fake.spool / "embed.wake").write_text("")
    slept = []
    served = idle.serve_wake_requests(fake.path, fake.spool, sleep=slept.append)
    assert slept == []                                              # nothing is stuck: no holding
    by_name = {item.get("request") or item["service"]: item for item in served}
    assert by_name["embed"]["action"] == "load"
    assert by_name["rerank"] == {"request": "rerank", "action": "ignored", "why": "not a file: removed"}
    assert by_name["asr"]["why"] == "not a file: removed"
    assert by_name["vllm"] == {"request": "vllm", "action": "ignored", "why": "not a file: moved aside"}
    left = sorted(p.name for p in fake.spool.iterdir())
    assert len(left) == 1 and left[0].startswith(".stuck-vllm.wake.")        # hidden, and no *.wake name
    assert not list(fake.spool.glob("*.wake"))                      # what PathExistsGlob= would start the unit on
    assert not [c for c in fake.calls if "vllm" in " ".join(map(str, c)) and "start" in map(str, c)]
    assert idle.serve_wake_requests(fake.path, fake.spool, sleep=slept.append) == [] and slept == []


def test_an_entry_that_can_be_neither_removed_nor_moved_holds_the_command_instead_of_looping_the_unit(spool, monkeypatch):  # noqa: F811
    """A read-only or sticky folder: the unit would be started again the moment the command exits, 30 times, and
    then not at all. The command stays (a second at a time) and keeps serving."""
    fake = spool      # noqa: F811
    _unload_idle(fake, "embed")
    (fake.spool / "rerank.wake").mkdir()
    monkeypatch.setattr(idle, "_set_aside", lambda path: "stuck")
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 3:                                         # a real request arrives while it holds
            (fake.spool / "embed.wake").write_text("")
    served = idle.serve_wake_requests(fake.path, fake.spool, sleep=sleep)
    assert slept == [1.0] * int(idle.STUCK_HOLD) and idle.STUCK_HOLD == 30.0
    assert served[0] == {"request": "rerank", "action": "ignored", "why": "not a file: stuck"}
    assert served[1:] == [{"service": "embed", "action": "load", "why": "on demand: wake request"}]   # said once
    assert "neuroscifi-memory-embed" in fake.running
    # at 30 s a pass the unit starts at most twice a minute: under its own limit of 30 starts in 10 minutes
    unit = (idle.REPO / "deploy" / "systemd" / "neuroscifi-wake.service").read_text()
    assert "StartLimitIntervalSec=10min\n" in unit and "StartLimitBurst=30\n" in unit
    assert 600 / idle.STUCK_HOLD < 30


def test_a_folder_that_really_cannot_be_changed_is_stuck(tmp_path):
    folder = tmp_path / "wake"
    folder.mkdir()
    (folder / "rerank.wake").mkdir()
    os.chmod(folder, 0o500)
    try:
        if os.access(folder, os.W_OK):
            pytest.skip("this account writes anywhere (root)")
        assert idle._set_aside(folder / "rerank.wake") == "stuck"
    finally:
        os.chmod(folder, 0o700)
    assert idle._set_aside(folder / "rerank.wake") == "removed"


# ---- S6 ------------------------------------------------------------------------------------------------------------

def test_memory_settings_understand_the_forms_a_settings_file_uses(tmp_path):
    repo, folder, moved = tmp_path / "repo", tmp_path / "cfg", tmp_path / "moved"
    (repo / "deploy" / "memory").mkdir(parents=True)
    folder.mkdir()
    (repo / "deploy" / "memory" / "memory.env").write_text(
        "NSF_MEMORY_BIND=127.0.0.1\nNSF_MEMORY_EMBED_PORT=18441\nNSF_MEMORY_RERANK_PORT=18442\n"
        "NSF_MEMORY_DIR=${NSF_MEMORY_DIR:-${HOME}/.config/neuroscifi/memory}\n")
    env = {"HOME": str(tmp_path / "home"), "NSF_MEMORY_DIR": str(folder)}
    (folder / "memory.env").write_text(
        'NSF_MEMORY_EMBED_PORT="28441"\n'                           # quoted
        "export NSF_MEMORY_RERANK_PORT='28442'   # the reranker\n"  # export, single quotes, a trailing comment
        "NSF_MEMORY_BIND=::1 # loopback, IPv6\n"
        f"NSF_MEMORY_DIR={moved}\n"                                  # the key moves with it, as in run.sh
        "NSF_OTHER=1\n# NSF_MEMORY_EMBED_PORT=1\n")
    found = idle.memory_settings(env, repo)
    assert (found["embed_port"], found["rerank_port"], found["bind"]) == (28441, 28442, "::1")
    assert found["dir"] == moved and found["key_file"] == moved / "api_key"
    assert found["ignored"] == [] and found["error"] is None
    # the shell's own default forms, with the home folder
    (folder / "memory.env").write_text(
        "NSF_MEMORY_EMBED_PORT=${NSF_MEMORY_EMBED_PORT:-38441}\n"   # the shipped value stands: it is set
        'NSF_MEMORY_DIR="$HOME/keys"\n')
    found = idle.memory_settings(env, repo)
    assert found["embed_port"] == 18441 and found["dir"] == tmp_path / "home" / "keys"
    without = idle.memory_settings({"HOME": str(tmp_path / "home")}, repo)       # no NSF_MEMORY_DIR: the default
    assert without["dir"] == tmp_path / "home" / ".config" / "neuroscifi" / "memory"
    # single quotes are literal, as in the shell: no expansion, so the value is refused, not guessed
    (folder / "memory.env").write_text("NSF_MEMORY_DIR='$HOME/keys'\nNSF_MEMORY_EMBED_PORT=\"1 2\"\n"
                                       "NSF_MEMORY_RERANK_PORT=$((1+1))\nNSF_MEMORY_BIND=\"127.0.0.1\n")
    found = idle.memory_settings(env, repo)
    override = folder / "memory.env"
    assert found["ignored"] == [f"{override}:{n}" for n in (1, 2, 3, 4)]
    assert (found["dir"], found["embed_port"], found["rerank_port"]) == (folder, 18441, 18442)


def test_a_bind_address_off_this_computer_is_refused_as_the_script_refuses_it(tmp_path, monkeypatch):
    repo, folder = tmp_path / "repo", tmp_path / "cfg"
    (repo / "deploy" / "memory").mkdir(parents=True)
    folder.mkdir()
    (folder / "memory.env").write_text("NSF_MEMORY_BIND=0.0.0.0\n")
    found = idle.memory_settings({"NSF_MEMORY_DIR": str(folder), "HOME": str(tmp_path)}, repo)
    assert found["bind"] == "127.0.0.1" and "must be loopback (got 0.0.0.0)" in found["error"]
    for ok in ("127.0.0.1", "::1", "localhost"):
        (folder / "memory.env").write_text(f"NSF_MEMORY_BIND={ok}\n")
        assert idle.memory_settings({"NSF_MEMORY_DIR": str(folder), "HOME": str(tmp_path)}, repo)["error"] is None
    # nsf setup contacts nothing then, and says why
    from neuroscifi import setup_cli
    (folder / "memory.env").write_text("NSF_MEMORY_BIND=192.168.1.5\nNSF_MEMORY_EMBED_PORT=$(id)\n")
    (folder / "api_key").write_text("k\n")
    os.chmod(folder / "api_key", 0o600)
    monkeypatch.setenv("NSF_MEMORY_DIR", str(folder))
    monkeypatch.setattr(setup_cli, "_memory_probe", lambda *a: pytest.fail("nothing may be contacted"))
    report = {"steps": []}
    setup_cli._memory(report, type("A", (), {"no_memory": False, "memory_embed_url": None, "memory_rerank_url": None,
                                             "memory_key_file": None})(), None)
    (step,) = report["steps"]
    assert (step["step"], step["status"]) == ("memory", "skipped")
    assert "must be loopback (got 192.168.1.5)" in step["detail"]
    assert step["settings_ignored"] == [f"{folder / 'memory.env'}:2"] and "1 line(s)" in step["detail"]   # said once


def test_an_account_without_a_home_folder_gets_the_defaults_not_a_crash(tmp_path, monkeypatch):
    """A unit with DynamicUser: HOME unset and no passwd entry. Path.home() raises there."""
    monkeypatch.setattr(os.path, "expanduser", lambda path: path)          # what it returns when it cannot resolve
    found = idle.memory_settings({}, tmp_path)
    assert (found["embed_port"], found["rerank_port"], found["bind"], found["error"]) == (18441, 18442, "127.0.0.1", None)
    assert str(found["dir"]) == "~/.config/neuroscifi/memory" and not found["key_file"].exists()


def test_importing_the_policy_reads_no_settings_file_and_survives_no_home(tmp_path):
    """SERVICES is built on first use: the inference gateway and the memory lookup import this module, and it must
    not depend on a home folder being resolvable or on what an override file holds. (inference/config.py has its
    own Path.home() at import, older than this module's and not changed here.)"""
    code = ("import pathlib\n"
            "def boom(*a, **k): raise RuntimeError('Path.home() called')\n"
            "pathlib.Path.home = classmethod(boom)\n"
            "import neuroscifi.resources.idle as idle\n"
            "assert 'SERVICES' not in vars(idle), 'built at import'\n"
            "print(sorted(idle.SERVICES), idle.service_port('embed'), 'SERVICES' in vars(idle))\n")
    env = {"PYTHONPATH": str(idle.REPO / "src"), "PATH": os.environ.get("PATH", ""), "NSF_REPO": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "['asr', 'embed', 'rerank', 'searxng', 'tts', 'vllm'] 18441 True"


def test_a_replaced_services_table_is_what_the_policy_uses(monkeypatch):
    from dataclasses import replace
    table = dict(idle.SERVICES, embed=replace(idle.SERVICES["embed"], metrics="http://127.0.0.1:29999/metrics"))
    monkeypatch.setattr(idle, "SERVICES", table)
    assert idle.service_port("embed") == 29999 and idle.managed_service(29999) == "embed"
    assert idle.managed_service(18441) is None and idle.Idle("/nonexistent/db").services is table


# ---- S8 ------------------------------------------------------------------------------------------------------------

def test_a_lookup_does_not_block_behind_a_stuck_holder_of_the_spawn_lock(memory_host, monkeypatch):  # noqa: F811
    fake = memory_host      # noqa: F811
    _unload_idle(fake, "embed")
    spawned = []

    class Started:
        def poll(self):
            return None
    monkeypatch.setattr(idle, "_spawn_load", lambda *a: spawned.append(a[1]) or Started())
    lock = fake.make()._file("idle-spawn-embed.lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)              # a peer stopped while it holds the lock
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        result = []

        def lookup():
            with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
                result.append(state)
        thread = threading.Thread(target=lookup, daemon=True)
        thread.start()
        thread.join(5)
        assert not thread.is_alive(), "the lookup waited for the spawn lock"
        assert result[0]["action"] == "loading" and spawned == []   # whoever holds it is starting the reload
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    with idle.using_now(fake.path, "embed", purpose="memory lookup") as state:
        assert state["action"] == "loading"
    assert spawned == ["embed"]
