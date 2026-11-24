"""The installed deployment's two accounts (open-items round, lane D): what they share is arranged by
`deploy/install.sh --idle-access` and proven with real accounts in a user namespace (scripts/two_account_check.py);
files of `<db>.resources/` take their mode from the folder; an account that may not read the database blocks the
idle pass instead of reading as "nothing runs"."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

import pytest

from neuroscifi.resources import idle, shared
from test_resources_idle import MIN, fake, memory_host, spool, _unload_idle      # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[1]
INSTALL = str(ROOT / "deploy" / "install.sh")


# ---- two real accounts ---------------------------------------------------------------------------------------------

def test_the_two_account_install_works_with_real_accounts_in_a_user_namespace(tmp_path):
    """The service account, the idle account and a third one as real uids, the real installer run as root of the
    namespace, this checkout's code acting as each. Skipped, with the reason, where the host forbids it."""
    report = tmp_path / "report.json"
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "two_account_check.py"), "--json", str(report)],
                          capture_output=True, text=True, timeout=600)
    if done.returncode == 77:
        pytest.skip(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-1000:]
    result = json.loads(report.read_text())
    assert result["failed"] == 0 and result["passed"] >= 41
    names = {c["check"] for c in result["checks"]}
    assert {"the idle account cannot write the database", "a third account's listener is refused",
            "the idle account serves the request and reloads the service",
            "the service account cannot remove another account's entry"} <= names


# ---- the folder's mode decides the files' mode ---------------------------------------------------------------------

def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_private_folder_keeps_private_files_and_a_shared_one_gets_group_files_whatever_the_umask(tmp_path):
    old = os.umask(0o077)                       # the units' UMask
    try:
        private = shared.ensure_folder(tmp_path / "private")
        assert _mode(private) == 0o700 and not shared.is_shared(private)
        shared.write_text(private / "state.json", "{}")
        os.close(shared.open_file(private / "a.lock", os.O_RDWR))
        shared.touch(private / "used", 1_700_000_000.0)
        assert {_mode(private / n) for n in ("state.json", "a.lock", "used")} == {0o600}
        assert os.stat(private / "used").st_mtime == 1_700_000_000.0

        both = tmp_path / "both"
        both.mkdir()
        os.chmod(both, 0o2770)                  # as deploy/install.sh --idle-access makes it
        assert shared.ensure_folder(both) == both and _mode(both) == 0o2770      # never changed by the code
        shared.write_text(both / "state.json", "{}")
        os.close(shared.open_file(both / "a.lock", os.O_RDWR))
        shared.touch(both / "used")
        assert {_mode(both / n) for n in ("state.json", "a.lock", "used")} == {0o660}
        assert not list(both.glob("*.tmp"))
        # group-writable without setgid, or setgid without group write: not the shared shape, files stay private
        for odd in (0o770, 0o2750):
            folder = tmp_path / f"odd{odd:o}"
            folder.mkdir()
            os.chmod(folder, odd)
            shared.write_text(folder / "x", "1")
            assert _mode(folder / "x") == 0o600
    finally:
        os.umask(old)


def test_an_existing_file_is_opened_as_it_is_never_rechmodded(tmp_path):
    folder = tmp_path / "both"
    folder.mkdir()
    os.chmod(folder, 0o2770)
    (folder / "theirs").write_text("x")
    os.chmod(folder / "theirs", 0o640)          # the other account's choice
    os.close(shared.open_file(folder / "theirs", os.O_RDONLY))
    shared.touch(folder / "theirs")
    assert _mode(folder / "theirs") == 0o640


def test_a_marker_the_other_account_created_is_still_marked_now(tmp_path, monkeypatch):
    """os.utime with a chosen time needs the file's owner; with write permission only "now" may be set. A use marker
    the idle account created must still record the service account's use, or the service unloads while in use."""
    marker = tmp_path / "used"
    marker.write_text("")
    os.utime(marker, (1.0, 1.0))
    real = os.utime

    def utime(path, times=None, **kwargs):
        if times is not None:
            raise PermissionError(1, "Operation not permitted")
        return real(path, times, **kwargs)
    monkeypatch.setattr(os, "utime", utime)
    shared.touch(marker, 1_700_000_000.0)
    assert time.time() - os.stat(marker).st_mtime < 5


def test_the_policy_files_follow_the_folders_mode(fake):      # noqa: F811
    old = os.umask(0o077)
    try:
        folder = Path(f"{fake.path}.resources")
        folder.mkdir()
        os.chmod(folder, 0o2770)
        p = fake.make()
        p.configure({"vllm": 30 * MIN})
        p.touch("vllm")
        with idle.using(fake.path, "vllm", purpose="chat"):
            pass
        from neuroscifi.resources import policy
        from neuroscifi.voice.beacon import SharedAudioBeacon
        SharedAudioBeacon(fake.path).write(True, True)
        with policy.background_model_slot(fake.path) as slot:
            assert slot == 0
        files = sorted(f.name for f in folder.iterdir())
        assert {"audio.json", "audio.lock", "idle.json", "idle-events.jsonl", "idle-used-vllm", "idle-inuse-vllm.lock",
                "model-slot-0.lock"} <= set(files)
        assert {f: oct(_mode(folder / f)) for f in files} == dict.fromkeys(files, "0o660")
        assert _mode(folder) == 0o2770
    finally:
        os.umask(old)


# ---- a database this account may not read --------------------------------------------------------------------------

def test_an_unreadable_database_blocks_the_pass_instead_of_reading_as_no_task(fake):      # noqa: F811
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    p = fake.make()
    p.configure({"vllm": 15 * MIN})
    fake.now += 16 * MIN
    assert p.running_tasks() == 0
    os.chmod(fake.path, 0)
    try:
        assert p.running_tasks() is None
        out = p.tick()
        assert out["decisions"]["vllm"]["action"] == "none" and "vllm" not in [c[-1] for c in fake.calls if "stop" in c]
        assert any("cannot be told" in b and "--idle-access" in b for b in out["blockers"])
    finally:
        os.chmod(fake.path, 0o600)
    assert p.tick()["decisions"]["vllm"]["action"] == "unload"      # readable again: the pass goes on


def test_a_database_without_web_cortex_tables_or_without_a_file_still_reads_as_no_task(tmp_path):
    import sqlite3
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).close()
    assert idle.Idle(empty).running_tasks() == 0
    assert idle.Idle(tmp_path / "missing.sqlite").running_tasks() == 0


# ---- the installer -------------------------------------------------------------------------------------------------

def _dry(*args):
    return subprocess.run(["bash", INSTALL, "--dry-run", *args], capture_output=True, text=True,
                          env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})


def test_idle_access_arranges_what_the_two_accounts_share_and_nothing_else():
    done = _dry("--idle-access", "--idle-user", "nsfmodels")
    assert done.returncode == 0, done.stderr
    out = done.stdout
    # Root's own steps act on root's paths only; every step inside the service account's folder is done as that
    # account (review F1: as root they followed whatever links it planted there).
    for line in ("+ gpasswd -a nsfmodels neuroscifi\n",
                 "+ install -d -m 3770 -o nsfmodels -g neuroscifi /var/lib/neuroscifi-wake\n",
                 "+ (as neuroscifi) chmod 0710 /var/lib/neuroscifi\n",
                 "+ (as neuroscifi) chgrp neuroscifi /var/lib/neuroscifi/instance.sqlite\n",
                 "+ (as neuroscifi) chmod 0640 /var/lib/neuroscifi/instance.sqlite\n",
                 "+ (as neuroscifi) mkdir -m 2770 /var/lib/neuroscifi/instance.sqlite.resources\n",
                 "+ (as neuroscifi) chmod 2770 /var/lib/neuroscifi/instance.sqlite.resources\n"):
        assert line in out, line
    inside = [row for row in out.splitlines() if row.startswith("+ ") and "/var/lib/neuroscifi/" in row]
    assert inside and all(row.startswith("+ (as neuroscifi) ") for row in inside), inside
    assert "pip" not in out and "venv" not in out and "+ sed" not in out      # no package, no units
    assert "can now read /etc/neuroscifi too" in out                         # the consequence is said
    full = _dry("--idle", "--idle-user", "nsfmodels", "--db", "/var/lib/neuroscifi/ind.sqlite")
    assert full.returncode == 0 and "+ (as neuroscifi) chmod 0640 /var/lib/neuroscifi/ind.sqlite\n" in full.stdout
    assert "+ install -d -m 0710 -o neuroscifi -g neuroscifi /var/lib/neuroscifi\n" in full.stdout
    assert "Environment=NSF_DB=/var/lib/neuroscifi/ind.sqlite" in full.stdout
    assert "does not arrange" not in full.stdout
    plain = _dry()
    assert "gpasswd" not in plain.stdout and "0710" not in plain.stdout and "polkit" not in plain.stdout


@pytest.mark.parametrize("args,why", [
    (("--idle-access", "--idle-user", "neuroscifi"), "must not be neuroscifi"),
    (("--idle-access", "--idle-user", "root"), "must not be root"),
    (("--idle-access", "--idle-user", "x", "--db", "/srv/elsewhere.sqlite"), "must be a file in /var/lib/neuroscifi"),
    (("--idle-access", "--idle-user", "x", "--db", "/var/lib/neuroscifi/../x.sqlite"), "'..'"),
    (("--idle-access", "--idle-user", "x", "--db", "/var/lib/neuroscifi/a b.sqlite"), "plain absolute path"),
    (("--db", "/var/lib/neuroscifi/x.sqlite"), "goes with --idle"),
])
def test_idle_access_refuses_what_it_cannot_do_safely(args, why):
    done = _dry(*args)
    assert done.returncode == 1 and why in done.stderr, done.stderr


def test_the_polkit_rule_allows_the_idle_account_start_and_stop_of_exactly_the_policys_units():
    out = _dry("--idle-access", "--idle-user", "nsfmodels").stdout
    rule = "\n".join(line[4:] for line in out.split("(mode 0644):\n", 1)[1].splitlines() if line.startswith("    "))
    units = set(re.findall(r'"(neuroscifi-[a-z-]+\.service)"', rule))
    policy = {svc.unit for svc in idle.SERVICES.values() if svc.unit}
    memory = {"neuroscifi-memory.service", "neuroscifi-memory-embed.service", "neuroscifi-memory-rerank.service"}
    # every unit the policy manages, plus the memory units of the other NSF_MEMORY_SERVER way and nothing else
    assert policy <= units and units - policy <= memory and memory <= units
    assert 'subject.user == "nsfmodels"' in rule and 'var verbs = ["start", "stop"];' in rule
    assert 'action.id == "org.freedesktop.systemd1.manage-units"' in rule
    assert rule.count("polkit.Result.YES") == 1 and "restart" not in rule and "enable" not in rule
    node = __import__("shutil").which("node")
    if node:                                      # it is JavaScript: at least it must parse
        check = subprocess.run([node, "-e", "var polkit={addRule:function(f){},Result:{YES:1}};" + rule],
                               capture_output=True, text=True)
        assert check.returncode == 0, check.stderr
