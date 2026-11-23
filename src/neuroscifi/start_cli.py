"""Everyday use after `nsf setup` (ADR-043).

nsf start       run everything `nsf setup` configured (research worker, owner console, Pocket bridge with its phone
                setup page) in one terminal, open the console in the browser, and stop all of it with Ctrl-C
nsf add-phone   print the two QR codes of the console's "Add a phone" in the terminal and wait until the phone pairs

Both read the setup record `nsf setup` writes (~/.config/neuroscifi/setup.json); `--record PATH` names another.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser

SERVICES = ("research", "console", "pocket")
STOP_GRACE = 8.0
# The bridge's start-up code line: in this terminal the QR codes of Add a phone replace it.
_CODE_LINE = re.compile(r"^One-use pairing code \(5 minutes\): \S+$")


class StartError(Exception):
    pass


def load_record(path=None) -> dict:
    """The setup record. `nsf start` runs the commands in it, so it must be a file of this user that others cannot
    write."""
    import stat
    from .setup_cli import setup_record_path
    path = Path(path) if path else setup_record_path()
    try:
        info = os.stat(path)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise StartError(f"{path} must belong to this user and be writable only by it (chmod 600)")
        record = json.loads(path.read_text())
    except FileNotFoundError:
        raise StartError("NeuroSciFi is not set up yet: run `nsf setup --db PATH` once (about a minute), then "
                         "`nsf start`") from None
    except (OSError, ValueError) as error:
        raise StartError(f"cannot read the setup record {path}: {error}") from None
    if not isinstance(record, dict) or record.get("version") != 1 or not isinstance(record.get("services"), list):
        raise StartError(f"{path} is not a setup record of this version; run `nsf setup` again")
    return record


# ---- nsf start -------------------------------------------------------------------------------------------------
def _pump(name, stream, lock):
    for line in iter(stream.readline, ""):   # the pipe decodes with errors="replace": one odd byte never stalls it
        line = line.rstrip("\n")
        if _CODE_LINE.match(line):
            line = 'Pairing: in the console choose "Add a phone", or run: nsf add-phone'
        with lock:
            print(f"[{name}] {line}", flush=True)


def _graphical() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or sys.platform == "darwin"


def _opener(url: str, near: Path) -> Path:
    """A 0600 page in a new 0700 folder next to the console URL file, forwarding to the console: the browser gets a
    file path on its command line, never the session token (other local users can read command lines). Random names
    created exclusively, so nobody can prepare the file in advance."""
    import html
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix="nsf-console-", dir=near))
    fd, name = tempfile.mkstemp(dir=folder, suffix=".html")
    with os.fdopen(fd, "w") as out:
        out.write('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                  f'<meta http-equiv="refresh" content="0;url={html.escape(url, quote=True)}">'
                  '<title>NeuroSciFi console</title>\n')
    return Path(name)


def _wait_for_url(url_file: Path, procs, since: float, seconds=20.0) -> str | None:
    """The console URL once this run's console has written it: a regular file of this user, written after `since`
    (a stale file of a killed console holds a dead token)."""
    import stat
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            info = os.stat(url_file, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_mtime >= since - 1:
                text = url_file.read_text().strip()
                if re.fullmatch(r"http://127\.0\.0\.1:\d{1,5}/#token=[A-Za-z0-9_-]{16,128}", text):
                    return text
        except OSError:
            pass
        if any(p.poll() is not None for _, p in procs):
            return None
        time.sleep(0.2)
    return None


def _orphan_guard():
    """For each service (Linux): a new session (Ctrl-C reaches the supervisor only), and SIGTERM when `nsf start`
    itself dies, even by SIGKILL, so no bridge or setup page is left on the network without its supervisor. prctl is
    resolved here, before any fork; the child only calls it."""
    try:
        import ctypes
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        prctl = None

    def child():
        os.setsid()
        if prctl is not None:
            prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    return child


def _stop(procs):
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):   # a second Ctrl-C cannot cut it short
        try:
            signal.signal(sig, signal.SIG_IGN)
        except ValueError:
            pass
    for _, p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE
    for _, p in procs:
        try:
            p.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def _start(a):
    try:
        record = load_record(a.record)
    except StartError as error:
        print(f"nsf start: {error}", file=sys.stderr)
        return 2
    services = [s for s in record["services"] if s.get("name") in SERVICES and isinstance(s.get("argv"), list)]
    if a.dry_run:
        import shlex
        for s in services:
            print(f"{s['name']}: nsf {shlex.join(s['argv'])}")
        return 0
    url_file = Path(record["console_url_file"])
    lock = threading.Lock()
    procs, opener = [], None
    started = time.time()
    try:
        # a closed terminal, `kill` or a service manager's stop end the services as Ctrl-C does (no orphans)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, signal.default_int_handler)
    except ValueError:
        pass   # not the main thread (tests)
    try:
        guard = _orphan_guard()
        for s in services:
            # -P: never import from the current folder. Own session: Ctrl-C reaches this supervisor only, which then
            # stops every service in order; _orphan_guard ends a service if the supervisor dies.
            p = subprocess.Popen([sys.executable, "-P", "-m", "neuroscifi.cli", *s["argv"]], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, encoding="utf-8",
                                 errors="replace", bufsize=1, preexec_fn=guard)
            procs.append((s, p))
        for s, p in procs:   # reader threads only after every fork (preexec_fn and threads do not mix)
            threading.Thread(target=_pump, args=(s["name"], p.stdout, lock), daemon=True).start()
        url = _wait_for_url(url_file, procs, started) if any(s["name"] == "console" for s in services) else None
        with lock:
            print("\nNeuroSciFi is running.")
            if url:
                opened = False
                if not a.no_browser and _graphical():
                    opener = _opener(url, url_file.parent)
                    opened = webbrowser.open(opener.as_uri())
                print("  Owner console:  " + ("opened in your browser. " if opened else "")
                      + f"Its link (with the session token) is in {url_file}")
            print('  Add a phone:    in the console choose "Add a phone", or run: nsf add-phone')
            print("  Stop:           press Ctrl-C here\n", flush=True)
        while True:
            for s, p in procs:
                if p.poll() is not None:
                    print(f"nsf start: the {s['what']} stopped (exit {p.returncode}); stopping the others. Its "
                          f"messages are above, marked [{s['name']}].", file=sys.stderr)
                    return 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping NeuroSciFi…", flush=True)
        return 0
    finally:
        try:
            _stop(procs)
        finally:
            if opener is not None:
                for path in (opener, opener.parent):
                    try:
                        path.unlink() if path.is_file() else path.rmdir()
                    except OSError:
                        pass


# ---- nsf add-phone ---------------------------------------------------------------------------------------------
def qr_text(rows, color=True, border=2) -> str:
    """A QR code for a terminal: two module rows per text line (half blocks). With colour it is black on white
    whatever the terminal's theme; without, light modules are drawn as blocks (right on dark terminals)."""
    width = len(rows[0]) + 2 * border
    grid = ["0" * width] * border + ["0" * border + r + "0" * border for r in rows] + ["0" * width] * border
    if len(grid) % 2:
        grid.append("0" * width)
    on = "1" if color else "0"
    lines = []
    for y in range(0, len(grid), 2):
        line = "".join("█" if t == on and b == on else "▀" if t == on else "▄" if b == on else " "
                       for t, b in zip(grid[y], grid[y + 1]))
        lines.append(f"\x1b[30;107m{line}\x1b[0m" if color else line)
    return "\n".join(lines)


def _color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


def _add_phone(a):
    from .console.phones import grouped, qr_rows
    from .pocket import server as bridge
    from .pocket.setup_page import fingerprint_groups
    db = a.pocket_db
    if db is None:
        try:
            db = load_record(a.record)["pocket_db"]
        except StartError as error:
            print(f"nsf add-phone: {error} (or name the bridge's database with --pocket-db)", file=sys.stderr)
            return 2
    try:
        reply = bridge.request_invitation(db)
    except OSError:
        print("nsf add-phone: Pocket is not running. Start NeuroSciFi with `nsf start`, then run `nsf add-phone` "
              "again.", file=sys.stderr)
        return 2
    color = _color()
    print("Add a phone. Use the phone's camera; nothing to install from a store and nothing to type.\n")
    if reply.get("setup_url"):
        rows = qr_rows(reply["setup_url"])
        print("1  Trust your Spark (once per phone): scan, then follow the steps on the phone.")
        if rows:
            print(qr_text(rows, color))
        print(f"   No camera? Open {reply['setup_url']} on the phone.\n")
    rows = qr_rows(reply["pair_url"])
    print(("2  " if reply.get("setup_url") else "") + "Pair this phone: scan, then tap Pair on the phone.")
    if rows:
        print(qr_text(rows, color))
    print(f"   No camera? Open {reply['app_url']} and enter: {grouped(reply['code'])}")
    if reply.get("ca_sha256"):
        print(f"\nOptional check: the certificate on the phone shows this fingerprint: "
              f"{fingerprint_groups(reply['ca_sha256'])}")
    if a.no_wait:
        return 0
    print("\nThe codes work once, for 5 minutes. Waiting for your phone… (Ctrl-C to stop waiting)", flush=True)
    try:
        while True:
            time.sleep(2)
            try:
                state = bridge.invitation_status(db, reply.get("id", ""))
            except OSError:
                print("nsf add-phone: Pocket stopped.", file=sys.stderr)
                return 2
            if state == "used":
                print("Paired. Open Pocket on the phone to ask your first question.")
                return 0
            if state != "pending":
                print("These codes expired or were replaced. Run `nsf add-phone` again when the phone is ready.")
                return 1
    except KeyboardInterrupt:
        print()
        return 130


def register(sub):
    p = sub.add_parser("start", help="run NeuroSciFi as `nsf setup` configured it (research worker, console, Pocket) "
                                     "and open the console; Ctrl-C stops everything")
    p.add_argument("--record", type=Path, help="the setup record (default ~/.config/neuroscifi/setup.json)")
    p.add_argument("--no-browser", action="store_true", help="do not open the console in a browser")
    p.add_argument("--dry-run", action="store_true", help="print the services it would run, and exit")
    p.set_defaults(handler=_start)
    q = sub.add_parser("add-phone", help="show the two QR codes that set up and pair a phone (the console's Add a "
                                         "phone, in this terminal), and wait until it has paired")
    q.add_argument("--record", type=Path, help="the setup record (default ~/.config/neuroscifi/setup.json)")
    q.add_argument("--pocket-db", type=Path, help="the Pocket database the running bridge serves (default: from the "
                                                  "setup record)")
    q.add_argument("--no-wait", action="store_true", help="print the codes and exit")
    q.set_defaults(handler=_add_phone)
