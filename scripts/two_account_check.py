#!/usr/bin/env python3
"""The installed two-account deployment, exercised with real accounts in a user namespace.

deploy/install.sh runs the console, the worker and Pocket as the service account `neuroscifi` and the idle pass and
the wake requests as the account that may start and stop the model services. What the two share is arranged by
`deploy/install.sh --idle-access` (RESOURCE_SCHEDULING.md §3d). This script proves that arrangement with the
kernel's own permission checks, without root and without touching the host:

  python scripts/two_account_check.py [--json FILE]

It re-executes itself under `unshare --user --map-root-user --map-auto --mount`: inside, it is root over a range of
further uids and over its own mounts. It puts a private copy of /etc and an empty /var/lib in place, creates the
accounts with the installer's own commands, runs the real `deploy/install.sh --idle-access`, and then acts as each
account in turn (`setpriv --init-groups`, as systemd's User= does) with this checkout's code:

  - the service account creates an individual and runs a research task; the idle account, before the grant, cannot
    tell whether a task runs and unloads nothing; after it, it counts the task, with the database open and closed;
  - the idle account cannot list the state folder, read another file in it or write the database;
  - both read and write `<db>.resources/` under UMask 0077, a folder that existed privately is migrated, and the
    installer run twice changes nothing more;
  - the idle pass unloads an idle service (a fake Docker), the service account sees it and leaves a wake request,
    cannot remove the other account's entry, a third account cannot enter, and the idle account serves the request;
  - a real listening socket on a service's port is refused when a third account owns it and accepted when the
    service account, root or the key file's owner does;
  - the code imports and reads its settings as a uid without an account and without HOME.

Exit 0: every check passed. Exit 1: one failed (printed). Exit 77: this host does not allow such a namespace
(the reason is printed); nothing was checked. What it cannot show: systemd itself (the units, PrivateUsers=yes,
polkit) and the real root account.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import sysconfig
import time

SKIP = 77
MNT = "/mnt"
STATE = "/var/lib/neuroscifi"
DB = f"{STATE}/instance.sqlite"
WAKE = "/var/lib/neuroscifi-wake"
SVC, OPS, OTHER = "neuroscifi", "nsfops", "nsfother"
ISO = "2020-01-01T00:00:00.000000000Z"


# ---- the actor: one action as one account ------------------------------------------------------------------------

class Host:
    """A fake Docker for the idle policy: one container whose state lives in a file both accounts can reach."""

    FILE = f"{MNT}/host.json"

    def state(self):
        try:
            return json.loads(Path(self.FILE).read_text())
        except (OSError, ValueError):
            return {"running": True, "calls": []}

    def __call__(self, argv, timeout):
        argv = [str(a) for a in argv]
        data = self.state()
        done = subprocess.CompletedProcess(argv, 0, "")
        if argv[:2] == ["docker", "inspect"]:
            done = subprocess.CompletedProcess(argv, 0, f"true {ISO}\n") if data["running"] else \
                subprocess.CompletedProcess(argv, 1, "Error: No such object")
        elif argv[:2] == ["systemctl", "is-active"]:
            done = subprocess.CompletedProcess(argv, 3, "")
        else:
            verb = next((v for v in ("start", "stop", "wait") if v in argv), None)
            data["calls"].append(verb)
            if verb == "stop":
                data["running"] = False
            elif verb == "start":
                data["running"] = True
            Path(self.FILE).write_text(json.dumps(data))
        return done


def policy(db, *, ahead=0.0):
    from neuroscifi.resources import idle
    return idle.Idle(db, runner=Host(), clock=lambda: time.time() + ahead,
                     metrics=lambda url: {"in_flight": 0.0, "served": 7.0},
                     sample=lambda: {"level": "ok", "reasons": []},
                     memory=lambda: {"available_bytes": 64 << 30, "total_bytes": 128 << 30})


def facts(path):
    try:
        st = os.lstat(path)
    except OSError as error:
        return {"error": type(error).__name__}
    return {"uid": st.st_uid, "gid": st.st_gid, "mode": oct(stat.S_IMODE(st.st_mode))}


def attempt(action):
    try:
        return {"ok": True, "value": action()}
    except Exception as error:      # the kind of refusal is the result
        return {"ok": False, "error": type(error).__name__}


def act(args):
    os.umask(0o077)                 # the units' UMask
    what, rest = args[0], args[1:]
    out = {}
    if what == "import":            # a uid without an account, without HOME
        from neuroscifi.inference import config as iconfig
        from neuroscifi.resources import idle
        cfg = iconfig.InferenceConfig()
        out = {"uid": os.getuid(), "home": os.environ.get("HOME"), "key_path": str(iconfig.key_path(cfg)),
               "ports": idle.memory_settings()["embed_port"], "services": sorted(idle.SERVICES)}
    elif what == "init":
        from neuroscifi.instance import store
        from neuroscifi.webcortex import orchestrator, state
        db = store.create(DB, time.time())
        state.set_config(db, {"enabled": True}, time.time())
        orchestrator.submit(db, "What is the Varnel River?", time.time(), mode="quick_lookup")
        db.close()
        Path(f"{STATE}/secret.txt").write_text("the service account's own\n")
        from neuroscifi.resources import idle
        idle.Idle(DB).touch("asr")                       # the folder exists privately before the installer runs
        out = {"db": facts(DB), "resources": facts(f"{DB}.resources"), "marker": facts(f"{DB}.resources/idle-used-asr")}
    elif what == "hold":
        from neuroscifi.instance import store
        db = store.open_existing(DB)
        with db:
            db.execute("UPDATE web_tasks SET status='reading', lease_holder='worker', lease_expires_at=?",
                       (time.time() + 600,))
        print("READY", flush=True)
        sys.stdin.readline()
        with db:
            db.execute("UPDATE web_tasks SET status='completed', lease_holder=NULL, lease_expires_at=NULL")
        db.close()
        print("DONE", flush=True)
        return
    elif what == "tasks":
        out = {"tasks": policy(DB).running_tasks(), "wal": os.path.exists(f"{DB}-wal")}
    elif what == "tick":
        result = policy(DB, ahead=float(rest[0])).tick()
        out = {"blockers": result.get("blockers"), "enabled": result.get("enabled"),
               "decision": (result.get("decisions") or {}).get("vllm")}
    elif what == "probe":
        import sqlite3

        def write():
            con = sqlite3.connect(DB, timeout=1)
            try:
                con.execute("CREATE TABLE intruder(x)")
                con.commit()
            finally:
                con.close()
            return "written"
        out = {"list": attempt(lambda: sorted(os.listdir(STATE))),
               "secret": attempt(lambda: Path(f"{STATE}/secret.txt").read_text()),
               "write_db": attempt(write),
               "create": attempt(lambda: Path(f"{STATE}/dropped").write_text("x")),
               "conf": attempt(lambda: sorted(os.listdir("/etc/neuroscifi")))}
    elif what == "configure":
        p = policy(DB)
        p.configure({"vllm": 60.0})
        out = {name: facts(p._file(name)) for name in ("idle.json", "idle-events.jsonl", "idle.lock",
                                                         "idle-events.lock")}
    elif what == "use":
        import fcntl
        from neuroscifi.resources import idle
        from neuroscifi.voice.beacon import SharedAudioBeacon
        p = idle.Idle(DB)
        with idle.using(DB, "vllm", purpose="check") as loaded:
            out["loaded"] = loaded
        with idle._flock(p._file("idle-inuse-vllm.lock"), fcntl.LOCK_SH | fcntl.LOCK_NB):
            pass
        SharedAudioBeacon(DB).write(False, False)
        out |= {"timeouts": p.timeouts(), "events": len(p.events()),
                "marker": facts(p._file("idle-used-vllm")), "lock": facts(p._file("idle-inuse-vllm.lock")),
                "beacon": facts(p._file("audio.json")), "marker_age": time.time() - p.last_used("vllm")}
    elif what == "see":
        from neuroscifi.resources import idle
        p = idle.Idle(DB)
        out = {"unloaded": p.unloaded_by_policy("vllm"), "state": p.read()["services"].get("vllm", {}).get("state"),
               "session": p.voice_session(), "used": p.last_used("vllm") is not None}
    elif what == "ask":
        from neuroscifi.resources import idle
        os.environ["NSF_WAKE_DIR"] = WAKE
        state = idle._background_reload(idle.Idle(DB), "vllm", "check")
        out = {"state": state["action"], "request": facts(f"{WAKE}/vllm.wake"),
               "entries": attempt(lambda: sorted(os.listdir(WAKE)))}
    elif what == "drop":
        Path(rest[0]).write_text("")
        out = facts(rest[0])
    elif what == "remove":
        out = attempt(lambda: os.unlink(rest[0]))
    elif what == "enter":
        out = attempt(lambda: sorted(os.listdir(rest[0])))
    elif what == "serve":
        from neuroscifi.resources import idle
        served = idle.serve_wake_requests(DB, WAKE, idle=policy(DB), hold=0.0)
        out = {"served": served, "left": sorted(os.listdir(WAKE)), "host": Host().state()}
    elif what in ("searxng", "searxng-before"):
        # SearXNG's unit command `nsf web searxng ACTION --db DB` with a fake Docker; "searxng-before" is the path
        # every account took before (the database opened to write: migration, the sweep, searxng_url on a first
        # start), which the unit's account in a two-account install could not take.
        import contextlib
        import hashlib
        import io
        from neuroscifi.cli import main
        from neuroscifi.instance.cli import with_db
        from neuroscifi.webcortex import cli as wcli
        from neuroscifi.webcortex.providers import searxng as sx
        calls = []
        sx.Container.start = lambda self, **kw: calls.append(["start", self.port]) or {"started": True}
        sx.Container.stop = lambda self: calls.append(["stop", self.port]) or {"stopped": True}
        sx.Container.status = lambda self: {"running": bool(calls)}

        def digest():
            return {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() if os.path.exists(name) else None
                    for name in (DB, f"{DB}-wal")}
        before = digest()
        printed, said = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(said):
            if what == "searxng":
                code = main(["web", "searxng", rest[0], "--db", DB])
            else:
                import argparse
                code = with_db(wcli._network_errors(wcli._searxng_cmd))(
                    argparse.Namespace(db=Path(DB), action=rest[0], probe=False, command="web"))
        try:
            result = json.loads(printed.getvalue())
        except ValueError:
            result = said.getvalue().strip()[-300:]
        out = {"exit": code, "calls": calls, "result": result, "unchanged": digest() == before}
    elif what == "searxng-url":
        from neuroscifi.instance import store
        from neuroscifi.webcortex import state
        db = store.open_existing(DB)
        state.set_config(db, {"searxng_url": rest[0]}, time.time())
        db.close()
        out = {"set": rest[0]}
    elif what == "cli":                 # the units' own commands, as the unit's account
        done = subprocess.run([sys.executable, "-m", "neuroscifi.cli", "resources", *rest], capture_output=True,
                              text=True, cwd="/")
        try:
            result = json.loads(done.stdout)
        except ValueError:
            result = {"stderr": done.stderr.strip().splitlines()[-2:]}
        out = {"exit": done.returncode, "result": result}
    elif what == "listen":
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", int(rest[0])))
        sock.listen(1)
        print("READY", flush=True)
        sys.stdin.readline()
        sock.close()
        return
    elif what == "refusal":
        from neuroscifi.resources import idle
        port = int(rest[0])
        services = {"vllm": idle.Service("vllm", "the local model", "c", (), (),
                                         metrics=f"http://127.0.0.1:{port}/metrics")}
        key = rest[1] if len(rest) > 1 else None
        out = {"refusal": idle.listener_refusal(["127.0.0.1"], port, key_file=key, services=services, environ={}),
               "uid": os.getuid()}
    print(json.dumps(out, default=str))


# ---- the driver: root inside the namespace --------------------------------------------------------------------------

def sh(*argv, check=True, **kwargs):
    done = subprocess.run([str(a) for a in argv], capture_output=True, text=True, **kwargs)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, argv))}: exit {done.returncode}: {(done.stderr or done.stdout)[-600:]}")
    return done


class Stage:
    def __init__(self, python, repo):
        self.python, self.repo = python, repo
        self.checks = []

    def env(self, **more):
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONPATH": f"{MNT}/src:{MNT}/site",
               "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8", "NSF_NAMESPACE_STAGE": "actor"}
        return env | more

    def argv(self, who, *args):
        ident = ["--reuid", who, "--regid", who, "--init-groups"] if not str(who).isdigit() else \
            ["--reuid", who, "--regid", who, "--clear-groups"]
        return ["setpriv", *ident, self.python, f"{MNT}/check.py", *map(str, args)]

    def run(self, who, *args, **env):
        done = subprocess.run(self.argv(who, *args), capture_output=True, text=True, env=self.env(**env), cwd="/")
        if done.returncode != 0:
            return {"crashed": (done.stderr or done.stdout).strip().splitlines()[-4:]}
        try:
            return json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            return {"crashed": ["no result", *done.stdout.strip().splitlines()[-2:]]}

    def hold(self, who, *args):
        proc = subprocess.Popen(self.argv(who, *args), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                env=self.env(), cwd="/")
        line = proc.stdout.readline().strip()
        if line != "READY":
            raise RuntimeError(f"{args}: {line!r}")
        return proc

    @staticmethod
    def release(proc):
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(30)

    def check(self, name, ok, detail=None):
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)


def mode(path):
    try:
        st = os.stat(path)
    except OSError as error:
        return type(error).__name__
    return f"{st.st_uid}:{st.st_gid} {stat.S_IMODE(st.st_mode):o}"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StageUnusable(Exception):
    """The host cannot provide the stage (not a finding about the code under test)."""


def inside(repo, prefix, site, rel_python):
    sh("mount", "-t", "tmpfs", "-o", "mode=0755", "tmpfs", MNT)
    for name in ("etc", "varlib", "py", "site", "src"):
        os.mkdir(f"{MNT}/{name}")
    try:
        shutil.copytree("/etc", f"{MNT}/etc", symlinks=True, dirs_exist_ok=True, ignore_dangling_symlinks=True,
                        copy_function=lambda s, d: _copy(s, d))
    except shutil.Error:
        pass                             # root's own folders (keys, sudoers): not needed and not readable
    sh("mount", "--bind", f"{MNT}/etc", "/etc")
    sh("mount", "--bind", f"{MNT}/varlib", "/var/lib")
    sh("mount", "--bind", prefix, f"{MNT}/py")
    sh("mount", "--bind", site, f"{MNT}/site")
    sh("mount", "--bind", f"{repo}/src", f"{MNT}/src")
    shutil.copy(__file__, f"{MNT}/check.py")
    os.chmod(f"{MNT}/check.py", 0o644)
    Path(Host.FILE).write_text(json.dumps({"running": True, "calls": []}))
    os.chmod(Host.FILE, 0o666)
    stage = Stage(f"{MNT}/py/{rel_python}", repo)
    check, run = stage.check, stage.run
    installer = ["bash", f"{repo}/deploy/install.sh", "--idle-access", "--idle-user", OPS]

    # The accounts and the state folder, with the installer's own commands (deploy/install.sh, "service user").
    sh("useradd", "--system", "--user-group", "--no-create-home", "--home-dir", STATE, "--shell",
       "/usr/sbin/nologin", "--comment", "NeuroSciFi service", SVC)
    sh("install", "-d", "-m", "0700", "-o", SVC, "-g", SVC, STATE)
    sh("install", "-d", "-m", "0750", "-o", "root", "-g", SVC, "/etc/neuroscifi")
    # The stage itself must give an account the folder it owns: some hosts confine unprivileged user namespaces
    # (AppArmor's restriction on Ubuntu 24.04, as on GitHub's runners), and then an account cannot write even its
    # own folder there. That is the host, not the installer (which has not run yet): the check is skipped, saying so.
    probe = subprocess.run(["setpriv", f"--reuid={SVC}", f"--regid={SVC}", "--init-groups", "sh", "-c",
                            f"touch {STATE}/.stage-probe && rm {STATE}/.stage-probe"], capture_output=True, text=True)
    if probe.returncode != 0:
        raise StageUnusable("this host's user namespace does not let an account write the folder it owns "
                            f"({(probe.stderr or '').strip()[:160]}); the two-account check needs that")
    for name in (OPS, OTHER):
        sh("useradd", "--no-create-home", name)
    uid = {name: int(sh("id", "-u", name).stdout) for name in (SVC, OPS, OTHER)}
    gid = int(sh("id", "-g", SVC).stdout)

    # 1. One account, as before: private folder, private files.
    made = run(SVC, "init")
    check("the service account creates the individual privately",
          made.get("db", {}).get("mode") == "0o600" and made.get("resources", {}).get("mode") == "0o700"
          and made.get("marker", {}).get("mode") == "0o600", made)

    # 2. Before the grant the idle account cannot tell, and unloads nothing.
    before = run(OPS, "tasks")
    check("before the grant the idle account cannot tell whether a task runs", before.get("tasks") is None, before)

    # 3. The real installer. Twice: the second run must change nothing.
    first = sh(*installer, check=False)
    check("deploy/install.sh --idle-access runs as root", first.returncode == 0, (first.stderr or first.stdout)[-400:])
    snapshot = {p: mode(p) for p in (STATE, DB, f"{DB}.resources", WAKE, f"{STATE}/secret.txt",
                                     f"{DB}.resources/idle-used-asr")}
    check("the state folder lets the group pass, not list", snapshot[STATE] == f"{uid[SVC]}:{gid} 710", snapshot)
    check("the database is readable by the group and nothing more", snapshot[DB] == f"{uid[SVC]}:{gid} 640", snapshot)
    check("the resources folder is setgid and group-writable", snapshot[f"{DB}.resources"] == f"{uid[SVC]}:{gid} 2770",
          snapshot)
    check("a file that was in it is migrated", snapshot[f"{DB}.resources/idle-used-asr"] == f"{uid[SVC]}:{gid} 660",
          snapshot)
    check("the request folder is the idle account's, for the service group, sticky",
          snapshot[WAKE] == f"{uid[OPS]}:{gid} 3770", snapshot)
    check("another file of the service account keeps its mode", snapshot[f"{STATE}/secret.txt"].endswith(" 600"),
          snapshot)
    groups = sh("id", "-nG", OPS).stdout.split()
    check("the idle account is in the service group", SVC in groups, groups)
    second = sh(*installer, check=False)
    again = {p: mode(p) for p in snapshot}
    check("a second run changes nothing", second.returncode == 0 and again == snapshot, again)
    rule = Path("/etc/polkit-1/rules.d/49-neuroscifi-idle.rules")
    check("the polkit rule names the idle account and the model units only",
          rule.exists() and f'subject.user == "{OPS}"' in rule.read_text() and "neuroscifi-vllm.service" in
          rule.read_text() and "neuroscifi-console" not in rule.read_text(),
          "written" if rule.exists() else "no /etc/polkit-1 on this host: the installer says so and writes none")

    # 3b. The service account attacks the installer (review F1): it owns the state folder, so it can put anything
    # there, and the installer is run again by root after it has been running. Root-only targets outside it:
    victims = {name: f"{MNT}/victim-{name}" for name in ("db", "wal", "resources", "inner", "hard")}
    for name, path in victims.items():
        if name == "resources":
            os.mkdir(path, 0o755)
        else:
            Path(path).write_text("root's own\n")
            os.chmod(path, 0o600)
    planted = f"{STATE}/planted.sqlite"
    as_svc = ["setpriv", f"--reuid={SVC}", f"--regid={SVC}", "--init-groups"]
    sh(*as_svc, "ln", "-s", victims["db"], planted)
    sh(*as_svc, "ln", "-s", victims["wal"], f"{planted}-wal")
    sh(*as_svc, "ln", "-s", victims["resources"], f"{planted}.resources")
    sh(*as_svc, "ln", "-s", victims["inner"], f"{DB}.resources/planted.lock")   # for the step over the folder's files
    hard = sh(*as_svc, "ln", victims["hard"], f"{DB}.resources/hard.lock", check=False)
    before_attack = {p: mode(p) for p in victims.values()}
    attacked = sh("bash", f"{repo}/deploy/install.sh", "--idle-access", "--idle-user", OPS, "--db", planted,
                  check=False)
    again = sh(*installer, check=False)              # the real database, with the links planted in its folder
    after_attack = {p: mode(p) for p in victims.values()}
    said = attacked.stdout + attacked.stderr
    check("a symbolic link the service account planted makes the installer refuse, and changes nothing it points at",
          attacked.returncode != 0 and "symbolic link" in said and after_attack == before_attack,
          {"exit": attacked.returncode, "said": said.strip().splitlines()[-3:], "before": before_attack,
           "after": after_attack})
    check("the refusal says the earlier steps stay applied and how to run it again",
          "steps before it stay applied" in said and f"--idle-access --idle-user {OPS} --db {planted}" in said,
          said.strip().splitlines()[-2:])
    check("links inside the shared folder are passed over: the installer's run over its files changes no target",
          again.returncode == 0 and after_attack == before_attack,
          {"exit": again.returncode, "hard link made": hard.returncode == 0, "after": after_attack})
    read = run(SVC, "enter", victims["resources"])
    secret = subprocess.run([*as_svc, "cat", victims["db"]], capture_output=True, text=True)
    check("the service account still cannot read the root-only file or write the root folder",
          secret.returncode != 0 and not os.listdir(victims["resources"]) and read.get("ok") is True, secret.stderr)
    # What a race could make the installer do at worst: act on a link after its check. Every such step runs as the
    # service account, so it is the kernel that answers, as it would for anything that account tries itself.
    raced = [subprocess.run([*as_svc, *cmd], capture_output=True, text=True).returncode
             for cmd in (["chgrp", SVC, planted], ["chmod", "0640", planted], ["chmod", "2770", f"{planted}.resources"])]
    check("a step that a race slipped past the check is refused by the kernel (it runs as the service account)",
          all(rc != 0 for rc in raced) and {p: mode(p) for p in victims.values()} == before_attack, raced)
    for path in (planted, f"{planted}-wal", f"{planted}.resources", f"{DB}.resources/planted.lock",
                 f"{DB}.resources/hard.lock"):
        if os.path.lexists(path):
            os.unlink(path)

    # 3b'. No terminal of root's reaches the service account's steps (security re-review): run the installer on a
    # terminal (script(1) gives it one) with a wrapper in front of setpriv that records, as the step starts, its
    # controlling terminal (field 7 of /proc/PID/stat; 0: none), its session and its stdin.
    wrap = Path(f"{MNT}/wrap")
    wrap.mkdir(exist_ok=True)
    real_setpriv = shutil.which("setpriv", path="/usr/bin:/bin:/usr/sbin:/sbin")
    (wrap / "setpriv").write_text("#!/bin/bash\n"
                                 f"echo \"tty=$(cut -d' ' -f7 /proc/$$/stat) sid=$(cut -d' ' -f6 /proc/$$/stat) "
                                 f"installer_sid=$(cut -d' ' -f6 /proc/$PPID/stat) "
                                 f"stdin=$(readlink /proc/$$/fd/0)\" >> {wrap}/seen\n"
                                 f"exec {real_setpriv} \"$@\"\n")
    os.chmod(wrap / "setpriv", 0o755)
    on_tty = sh("script", "-qec", f"PATH={wrap}:/usr/sbin:/usr/bin:/sbin:/bin " + " ".join(installer), "/dev/null",
                check=False)
    seen = (wrap / "seen").read_text().split() if (wrap / "seen").exists() else []
    fields = dict(item.split("=", 1) for item in seen)
    check("the service account's steps run with no controlling terminal, in a session of their own, stdin /dev/null",
          on_tty.returncode == 0 and fields.get("tty") == "0" and fields.get("stdin") == "/dev/null"
          and fields.get("sid") != fields.get("installer_sid"), {"exit": on_tty.returncode, **fields})

    # 3b''. Files that come and go while the installer goes over the shared folder (a service writing there): any
    # failure says so plainly and how to run it again, never that the service account did something wrong.
    churn = subprocess.Popen([*as_svc, "bash", "-c", f"cd {DB}.resources && for i in $(seq 1 4000); do "
                              "touch churn-$i; rm -f churn-$((i-1)); done; rm -f churn-*"])
    outcomes = []
    for _ in range(6):
        run_ = sh(*installer, check=False)
        outcomes.append((run_.returncode, (run_.stdout + run_.stderr).strip().splitlines()[-1:]))
    churn.wait(60)
    wrong = [o for o in outcomes if o[0] != 0 and not ("changed while" in o[1][0] and "Run it again" in o[1][0])]
    check("files changing under the installer are reported as such, never as a refusal",
          not wrong and {p: mode(p) for p in victims.values()} == before_attack,
          {"runs": len(outcomes), "changed": sum(1 for o in outcomes if o[0] != 0), "wrong": wrong[:2]})

    # 3c. A plain run of the installer later (an upgrade, without --idle) keeps the arrangement (review F7).
    plain = sh("bash", f"{repo}/deploy/install.sh", "--dry-run", "--unit-dir", f"{MNT}/units", check=False)
    wake_rw = f"ReadWritePaths=%S/neuroscifi -{WAKE}"
    keeps = [line for line in plain.stdout.splitlines() if line.startswith("+ sed") and wake_rw in line]
    check("a plain run of the installer keeps the entrance and the units' request folder",
          plain.returncode == 0 and f"+ install -d -m 0710 -o {SVC} -g {SVC} {STATE}\n" in plain.stdout
          and "is kept" in plain.stdout and len(keeps) == 3,
          [line for line in plain.stdout.splitlines() if STATE in line or "kept" in line][:4] + [len(keeps)])

    # 4. What the idle account may and may not do with the individual.
    probe = run(OPS, "probe")
    check("the idle account cannot list the state folder", probe["list"] == {"ok": False, "error": "PermissionError"},
          probe["list"])
    check("the idle account cannot read another file in it", probe["secret"] == {"ok": False, "error":
                                                                                 "PermissionError"}, probe["secret"])
    check("the idle account cannot write the database", probe["write_db"]["ok"] is False, probe["write_db"])
    check("the idle account cannot create a file beside it", probe["create"]["ok"] is False, probe["create"])
    stranger = run(OTHER, "enter", STATE)
    check("a third account cannot enter the state folder", stranger == {"ok": False, "error": "PermissionError"},
          stranger)

    # 5. The one question the grant is for, with the database open (a task running) and closed.
    holder = stage.hold(SVC, "hold")
    try:
        running = run(OPS, "tasks")
        check("the idle account counts a running task while the service account has the database open",
              running.get("tasks") == 1 and running.get("wal") is True, running)
        run(OPS, "configure")
        blocked = run(OPS, "tick", 0)
        check("the idle pass unloads nothing while the task runs",
              blocked.get("decision", {}).get("action") == "none" and "research task" in " ".join(blocked["blockers"]),
              blocked)
    finally:
        stage.release(holder)
    closed = run(OPS, "tasks")
    check("the idle account reads a database nobody has open (no write-ahead log beside it)",
          closed.get("tasks") == 0 and closed.get("wal") is False, closed)

    # 5b. SearXNG's own command as the account that runs the containers (ENGINEERING_PLAN.md §6 item 2).
    before = run(OPS, "searxng-before", "start")
    check("the path SearXNG's command took before cannot start it as the idle account (it opens the database to "
          "write)", before.get("exit") not in (0, None) and before.get("calls") == [] and before.get("unchanged"),
          before)
    first = run(OPS, "searxng", "start")
    check("as the idle account a first start runs SearXNG, writes nothing, and names the owner's one command",
          first.get("exit") == 0 and first.get("calls") == [["start", 8888]] and first.get("unchanged") is True
          and first["result"].get("configured", "missing") is None
          and "nsf web config --db" in first["result"].get("note", ""), first)
    run(SVC, "searxng-url", "http://127.0.0.1:18888")
    holder = stage.hold(SVC, "hold")
    try:
        held = run(OPS, "searxng", "start")
        check("with searxng_url set by the service account and the database open, the idle account starts it on "
              "that port and writes nothing", held.get("exit") == 0 and held.get("calls") == [["start", 18888]]
              and held.get("unchanged") is True and "note" not in held.get("result", {}), held)
    finally:
        stage.release(holder)
    stopped = run(OPS, "searxng", "stop")
    status = run(OPS, "searxng", "status")
    check("the idle account's stop and status read the port and write nothing",
          stopped.get("exit") == 0 and stopped.get("calls") == [["stop", 18888]] and stopped.get("unchanged")
          and status.get("exit") == 0 and status["result"]["searxng"].get("url") == "http://127.0.0.1:18888"
          and status.get("unchanged"), {"stop": stopped, "status": status})
    probe_by_ops = run(OPS, "searxng", "health")
    check("SearXNG's health probe, which records what it finds, stays with the service account",
          probe_by_ops.get("exit") == 2 and "may only read" in str(probe_by_ops.get("result"))
          and probe_by_ops.get("unchanged"), probe_by_ops)
    after = run(OPS, "probe")
    check("the idle account still cannot write the database or create a file beside it",
          after["write_db"]["ok"] is False and after["create"]["ok"] is False, after)

    # 6. The folder both write, under UMask 0077.
    written = run(OPS, "configure")
    check("what the idle account writes is the group's to read and write",
          all(v.get("mode") == "0o660" and v.get("gid") == gid and v.get("uid") == uid[OPS] for v in written.values()),
          written)
    used = run(SVC, "use")
    check("the service account reads the policy's settings and events",
          used.get("timeouts", {}).get("vllm") == 60.0 and used.get("events", 0) >= 1, used)
    check("what the service account writes is the group's to read and write",
          all(used.get(k, {}).get("mode") == "0o660" and used[k].get("uid") == uid[SVC] for k in ("marker", "beacon"))
          and used.get("lock", {}).get("mode") == "0o660", used)
    check("a use is marked now", abs(used.get("marker_age", 99)) < 5, used.get("marker_age"))

    # 7. The pass, the request and the reload.
    early = run(OPS, "tick", 0)
    check("a service used a moment ago stays loaded", early.get("decision", {}).get("action") == "none"
          and "used" in early["decision"].get("why", ""), early)
    late = run(OPS, "tick", 3600)
    check("the idle pass unloads a service nobody used for longer than its timeout",
          late.get("decision", {}).get("action") == "unload", late)
    seen = run(SVC, "see")
    check("the service account sees that the policy unloaded it", seen.get("unloaded") is True, seen)
    asked = run(SVC, "ask")
    check("the service account leaves a wake request", asked.get("state") == "loading"
          and asked.get("request", {}).get("uid") == uid[SVC] and asked["entries"].get("value") == ["vllm.wake"], asked)
    run(OPS, "drop", f"{WAKE}/other.wake")
    stolen = run(SVC, "remove", f"{WAKE}/other.wake")
    check("the service account cannot remove another account's entry", stolen == {"ok": False, "error":
                                                                                  "PermissionError"}, stolen)
    outside = run(OTHER, "enter", WAKE)
    check("a third account cannot enter the request folder", outside == {"ok": False, "error": "PermissionError"},
          outside)
    served = run(OPS, "serve")
    loads = [item for item in served.get("served", []) if item.get("service") == "vllm"]
    check("the idle account serves the request and reloads the service",
          bool(loads) and loads[0]["action"] == "load" and served["host"]["running"] is True and served["left"] == [],
          served)
    back = run(SVC, "see")
    check("the service account sees it loaded again", back.get("unloaded") is False and back.get("state") == "loaded",
          back)
    # The units' own command lines, as the idle account (the real `docker` is asked and may not answer here: a
    # service that cannot be inspected is "not loaded"; what matters is that nothing needs more than was granted).
    run(SVC, "drop", f"{WAKE}/nothing.wake")
    tick = run(OPS, "cli", "tick", "--db", DB)
    check("`nsf resources tick` runs as the idle account and can tell whether a task runs",
          tick.get("exit") == 0 and tick["result"].get("enabled") is True
          and not any("cannot be told" in b for b in tick["result"].get("blockers", [])), tick)
    woke = run(OPS, "cli", "wake", "--db", DB, "--dir", WAKE)
    check("`nsf resources wake` runs as the idle account and clears the request folder",
          woke.get("exit") == 0 and woke["result"].get("served") == [
              {"request": "nothing", "action": "ignored", "why": "not a service of the idle policy"}]
          and os.listdir(WAKE) == [], woke)
    denied = run(SVC, "cli", "wake", "--db", DB, "--dir", WAKE)
    check("the same command run by the service account on the empty folder serves nothing and fails nothing",
          denied.get("exit") == 0 and denied["result"].get("served") == [], denied)

    # 8. Whose process holds a service's port.
    port = free_port()
    key = f"{MNT}/api_key"
    Path(key).write_text("check-key\n")
    os.chown(key, uid[OPS], gid)
    os.chmod(key, 0o640)
    for owner, keyed, want, why in ((OTHER, False, False, "a third account's listener is refused"),
                                    (SVC, False, True, "the service account's own listener is accepted"),
                                    ("root", False, True, "root's listener (Docker's port proxy) is accepted"),
                                    (OPS, False, False, "the idle account's listener is refused without its key file"),
                                    (OPS, True, True, "the key file owner's listener is accepted")):
        listener = stage.hold(owner, "listen", port) if owner != "root" else \
            subprocess.Popen([stage.python, f"{MNT}/check.py", "listen", str(port)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env=stage.env(), cwd="/")
        if owner == "root" and listener.stdout.readline().strip() != "READY":
            raise RuntimeError("root listener")
        try:
            time.sleep(1.1)                  # the listener table is cached for a second per process: a fresh process
            got = run(SVC, "refusal", port, *([key] if keyed else []))
        finally:
            stage.release(listener)
        accepted = got.get("refusal") is None
        check(why, accepted is want, got)

    # 9. No account, no HOME.
    bare = run("4242", "import")
    check("the code imports and reads its settings as a uid without an account and without HOME",
          bare.get("uid") == 4242 and bare.get("home") is None and "vllm" in bare.get("services", []), bare)
    return stage.checks


def _copy(src, dst):
    try:
        shutil.copy2(src, dst, follow_symlinks=False)
    except OSError:
        pass                             # root's own files (shadow, keys): not needed and not readable


def main(argv):
    if os.environ.get("NSF_NAMESPACE_STAGE") == "actor":
        return act(argv)
    if os.environ.get("NSF_NAMESPACE_STAGE") == "inside":
        try:
            checks = inside(*argv[:4])
        except StageUnusable as why:
            print(json.dumps({"skip": str(why)}))
            return 0
        except Exception as error:
            checks = [{"check": "the namespace was set up", "ok": False, "detail": f"{type(error).__name__}: {error}"}]
        print(json.dumps(checks))
        return 0
    out = None
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
    for tool in ("unshare", "setpriv", "useradd", "gpasswd", "mount"):
        if shutil.which(tool, path="/usr/sbin:/usr/bin:/sbin:/bin") is None:
            print(f"skipped: {tool} is not installed")
            return SKIP
    if not os.path.isdir(MNT):
        print(f"skipped: no {MNT} to mount the stage on")
        return SKIP
    wrap = ["unshare", "--user", "--map-root-user", "--map-auto", "--mount"]
    probe = subprocess.run([*wrap, "true"], capture_output=True, text=True)
    if probe.returncode != 0:
        print("skipped: this host does not allow a user namespace with further uids "
              f"(unshare: {(probe.stderr or '').strip()[:200]})")
        return SKIP
    repo = str(Path(__file__).resolve().parents[1])
    prefix = os.path.realpath(sys.base_prefix)
    python = os.path.realpath(getattr(sys, "_base_executable", sys.executable))
    rel = os.path.relpath(python, prefix)
    if rel.startswith(".."):
        print(f"skipped: the interpreter {python} is not under its prefix {prefix}")
        return SKIP
    site = sysconfig.get_paths()["purelib"]
    done = subprocess.run([*wrap, python, os.path.abspath(__file__), repo, prefix, site, rel], capture_output=True,
                          text=True, env=os.environ | {"NSF_NAMESPACE_STAGE": "inside",
                                                       "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    try:
        checks = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        print(f"the stage did not report: exit {done.returncode}\n{done.stdout[-800:]}\n{done.stderr[-800:]}")
        return 1
    if isinstance(checks, dict) and "skip" in checks:
        print(f"skipped: {checks['skip']}")
        return SKIP
    failed = [c for c in checks if not c["ok"]]
    for c in checks:
        print(("ok    " if c["ok"] else "FAILED") + " " + c["check"] + ("" if c["ok"] else f"\n       {c['detail']}"))
    print(f"{len(checks) - len(failed)} of {len(checks)} checks passed")
    if out:
        Path(out).write_text(json.dumps({"checks": checks, "passed": len(checks) - len(failed),
                                         "failed": len(failed)}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
