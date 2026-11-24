"""ADR-043 guided phone setup: the plain-HTTP setup page, invitation ids and their status over the bridge's control
socket, the console's Add a phone API, the record `nsf setup` writes, `nsf start` and `nsf add-phone`."""
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time

import pytest

from neuroscifi import cli
from neuroscifi.console import phones
from neuroscifi.pocket import server as bridge, setup_page, tls
from neuroscifi import start_cli

LAN = "192.0.2.10"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private config home and a short runtime folder (AF_UNIX paths are limited)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("NSF_SEARCH_KEY_DIR", str(tmp_path / "search"))
    monkeypatch.setenv("NSF_INFERENCE_KEY_FILE", str(tmp_path / "no-inference-key"))
    monkeypatch.delenv("NSF_DB", raising=False)
    monkeypatch.setattr(tls, "DEFAULT_CA_DIR", tmp_path / "ca")
    monkeypatch.setattr(tls, "lan_addresses", lambda: [LAN])
    monkeypatch.setattr(socket, "gethostname", lambda: "spark")
    run = Path(f"/tmp/nsf-test-{os.getpid()}-{time.monotonic_ns() % 10**6}")
    run.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    yield tmp_path
    for f in run.rglob("*"):
        f.unlink() if not f.is_dir() else None
    for d in sorted(run.rglob("*"), reverse=True):
        d.rmdir()
    run.rmdir()


def setup(home, capsys, *extra):
    code = cli.main(["setup", "--db", str(home / "ind.sqlite"), "--no-model", "--ip", "127.0.0.1", "--bind",
                     "127.0.0.1", *extra, "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0, report
    return report


class Running:
    """The bridge as `nsf start` runs it (from the setup record), in-process on free ports."""

    def __init__(self, record, port, setup_port):
        argv = next(s["argv"] for s in record["services"] if s["name"] == "pocket")[2:]
        argv[argv.index("--port") + 1] = str(port)
        argv[argv.index("--setup-port") + 1] = str(setup_port)
        self.servers = []
        self.thread = threading.Thread(target=bridge.main, args=(argv,), kwargs={"started": self.servers.append},
                                       daemon=True)
        self.port, self.setup_port, self.db = port, setup_port, record["pocket_db"]
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                bridge.invitation_status(self.db, "0" * 16)
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("the bridge did not open its control socket")

    def close(self):
        """Stop the bridge as a service stop does: main() then closes the setup page, the control socket and its
        own threads. Before this the bridge of every test lived on until the test process ended."""
        for server in self.servers:
            server.shutdown()
        self.thread.join(15)
        assert not self.thread.is_alive(), "the bridge did not stop"

    def get(self, path, host=None, port=None):
        c = http.client.HTTPConnection("127.0.0.1", port or self.setup_port, timeout=5)
        c.request("GET", path, headers={"Host": host or f"127.0.0.1:{port or self.setup_port}"})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, dict(r.getheaders()), body

    def post(self, route, data, token=None):
        c = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=5, context=ssl._create_unverified_context())
        headers = {"Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}",
                   **({"Authorization": f"Bearer {token}"} if token else {})}
        c.request("POST", f"/api/{route}", json.dumps(data), headers)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, (json.loads(body) if body else None)

    def pair(self, code, **extra):
        return self.post("pair", {"code": code, **extra})[0]


@pytest.fixture
def running(home, capsys):
    report = setup(home, capsys)
    record = json.loads(Path(report["record"]).read_text())
    bridge_ = Running(record, free_port(), free_port())
    yield bridge_
    bridge_.close()


# ---- nsf setup: the record ---------------------------------------------------------------------------------------
def test_setup_records_its_choices_privately_and_idempotently(home, capsys):
    report = setup(home, capsys)
    path = Path(report["record"])
    assert path == home / "config" / "neuroscifi" / "setup.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    record = json.loads(path.read_text())
    assert [s["name"] for s in record["services"]] == ["research", "console", "pocket"]
    console = next(s["argv"] for s in record["services"] if s["name"] == "console")
    pocket = next(s["argv"] for s in record["services"] if s["name"] == "pocket")
    assert console[console.index("--pocket-db") + 1] == record["pocket_db"]
    assert pocket[pocket.index("--setup-port") + 1] == "8764"                       # the Pocket port minus one
    assert pocket[pocket.index("--ca-file") + 1].endswith("neuroscifi-ca.crt")
    hosts = {pocket[i + 1] for i, arg in enumerate(pocket) if arg == "--allowed-host"}
    assert hosts == {"spark", "spark.local", "localhost", "127.0.0.1"}                 # the certificate's names
    parser = cli.build_parser()
    for service in record["services"]:
        parser.parse_args(service["argv"])                                         # every command is a real one
    before = path.stat().st_mtime_ns
    setup(home, capsys)
    assert path.stat().st_mtime_ns == before, "an unchanged record is not rewritten"
    assert "token" not in path.read_text()


def test_setup_dry_run_writes_no_record_and_setup_port_zero_turns_the_page_off(home, capsys):
    report = setup(home, capsys, "--dry-run")
    assert not Path(report["record"]).exists()
    report = setup(home, capsys, "--setup-port", "0")
    pocket = next(s["argv"] for s in json.loads(Path(report["record"]).read_text())["services"] if s["name"] == "pocket")
    assert "--setup-port" not in pocket and report["next"]["phone"]["setup_url"] is None


def test_setup_says_what_to_do_next_in_plain_words(home, capsys):
    cli.main(["setup", "--db", str(home / "ind.sqlite"), "--no-model"])
    out = capsys.readouterr().out
    assert "1. Start it:     nsf start" in out and 'choose "Add a phone"' in out and "nsf add-phone" in out
    assert "No typing" in out
    assert "install the CA first" not in out and "fingerprint" not in out.split("To run the services")[0]


# ---- the bridge: setup page, invitation ids, status ---------------------------------------------------------------
def test_the_setup_page_serves_its_files_and_the_certificate_only(running):
    assert json.loads(running.get("/setup.json")[2])["open"] is False, "closed until the owner asks for codes"
    assert running.get("/neuroscifi-ca.crt")[0] == 404
    bridge.request_invitation(running.db)
    status, headers, body = running.get("/")
    assert status == 200 and b"Set up Pocket on this phone" in body
    csp = headers["Content-Security-Policy"]
    assert f"connect-src 'self' https://127.0.0.1:{running.port}" in csp and "script-src 'self'" in csp
    assert "unsafe" not in csp and "Strict-Transport-Security" not in headers
    for path in ("/setup.css", "/setup.mjs", "/ui.css", "/theme-boot.js", "/icon.svg"):
        assert running.get(path)[0] == 200, path
    info = json.loads(running.get("/setup.json")[2])
    assert info == {"open": True, "https_port": running.port, "certificate": True}   # no fingerprint: unauthenticated
    status, headers, der = running.get("/neuroscifi-ca.crt")
    assert status == 200 and headers["Content-Type"] == "application/x-x509-ca-cert" and der[:1] == b"\x30"
    assert b"PRIVATE" not in der
    for path in ("/mobile.js", "/api/pair", "/manifest.webmanifest", "/sw.js", "/presence/ui.css", "/../setup.html"):
        assert running.get(path)[0] == 404, path
    assert running.get("/", host="evil.example:80")[0] == 421                      # another port in Host
    assert running.get("/", host="evil.example")[0] == 421                         # not on the certificate (rebinding)
    assert running.get("/", host="a b")[0] == 421
    c = http.client.HTTPConnection("127.0.0.1", running.setup_port, timeout=5)
    c.request("POST", "/", "{}", {"Host": f"127.0.0.1:{running.setup_port}"})
    r = c.getresponse()
    assert r.status == 501 and "default-src 'none'" in r.getheader("Content-Security-Policy") and r.read() == b""


def test_the_certificate_is_offered_only_in_the_window_after_an_invitation(running, monkeypatch):
    assert setup_page.SETUP_WINDOW == 15 * 60
    first = bridge.request_invitation(running.db)["setup_seconds"]
    assert 890 <= first <= 900
    time.sleep(1.1)
    renewed = bridge.request_invitation(running.db, renew=True)
    assert renewed["setup_seconds"] <= first - 1, "an automatic renewal does not reopen the window"
    assert bridge.request_invitation(running.db)["setup_seconds"] >= first - 1, "asking again reopens it"
    monkeypatch.setattr(setup_page, "setup_open", lambda bridge: False)            # 15 minutes later
    assert json.loads(running.get("/setup.json")[2])["open"] is False
    assert running.get("/neuroscifi-ca.crt")[0] == 404
    assert running.get("/")[0] == 200                                               # the page says how to reopen it


def test_an_invitation_has_an_id_two_urls_and_a_status_that_turns_used(running):
    reply = bridge.request_invitation(running.db)
    assert reply["pair_url"] == f"https://127.0.0.1:{running.port}/#pair={reply['code']}" or \
        reply["pair_url"].endswith(f":{running.port}/#pair={reply['code']}")
    assert reply["setup_url"].startswith("http://") and reply["setup_url"].endswith(f":{running.setup_port}/")
    assert reply["code"] not in reply["setup_url"] and reply["code"] not in reply["app_url"]
    assert len(reply["ca_sha256"]) == 64 and reply["setup_seconds"] > 800
    assert bridge.invitation_status(running.db, reply["id"]) == "pending"
    assert running.pair(reply["code"]) == 201
    assert bridge.invitation_status(running.db, reply["id"]) == "used"
    newer = bridge.request_invitation(running.db)
    assert bridge.invitation_status(running.db, reply["id"]) == "used"
    assert bridge.invitation_status(running.db, newer["id"]) == "pending"
    newest = bridge.request_invitation(running.db)
    assert bridge.invitation_status(running.db, newer["id"]) == "replaced"          # e.g. another console tab
    assert bridge.invitation_status(running.db, newest["id"]) == "pending"
    assert bridge.invitation_status(running.db, "f" * 16) == "unknown"
    assert bridge.invitation_status(running.db, "not-an-id") == "unknown"


def test_a_link_can_carry_only_the_sparks_own_invitation(running):
    """A device of another family must not be able to send someone a #pair= link with its handoff code: Pocket sends
    "via": "invitation" for a code from a link, and the bridge then accepts only its own invitation."""
    status, paired = running.post("pair", {"code": bridge.request_invitation(running.db)["code"]})
    assert status == 201
    status, link = running.post("link", {}, token=paired["key"])
    assert status == 201 and link["code"]
    assert running.pair(link["code"], via="invitation") == 403
    assert running.pair(link["code"], via="other") == 403
    assert running.pair(link["code"]) == 201                                          # typed by hand: a handoff
    assert running.pair(bridge.request_invitation(running.db)["code"], via="invitation") == 201


def test_qr_urls_use_a_lan_address_not_a_local_name():
    class S:
        server_address = ("0.0.0.0", 8765)
        certificate_names = ["spark", "spark.local", "localhost", "127.0.0.1", "192.168.1.20", "fe80::1"]
    assert bridge.public_host(S) == "192.168.1.20"
    S.server_address = ("192.168.7.7", 8765)
    assert bridge.public_host(S) == "192.168.7.7"
    S.server_address, S.certificate_names = ("127.0.0.1", 8765), ["spark.local", "localhost", "127.0.0.1"]
    assert bridge.public_host(S) == "spark.local"


def test_load_ca_reads_der_and_pem_and_refuses_other_files(tmp_path):
    out = tls.init(tmp_path / "tls", ["spark.local", "127.0.0.1"], ca_dir=tmp_path / "ca")
    pem = tmp_path / "ca.pem"
    tls.export_ca(pem, ca_dir=tmp_path / "ca")
    der = tmp_path / "ca.crt"
    tls.export_ca(der, ca_dir=tmp_path / "ca", der=True)
    assert setup_page.load_ca(pem) == setup_page.load_ca(der)
    assert setup_page.load_ca(der)[1] == out["ca_fingerprints"]["sha256"].replace(":", "")
    (tmp_path / "x").write_text("hello")
    with pytest.raises(ValueError):
        setup_page.load_ca(tmp_path / "x")


# ---- the console's Add a phone --------------------------------------------------------------------------------------
def test_console_invite_returns_both_codes_as_module_rows(running):
    class Console:
        db_path = Path(running.db).with_name("ind.sqlite")
        pocket_db = Path(running.db)
    r = phones.invite(Console)
    assert r["running"] and r["id"] and r["expires_in"] == bridge.INVITATION_SECONDS
    for which in ("pair", "setup"):
        rows = r["qr"][which]
        assert len(rows) >= 21 and all(len(row) == len(rows) and set(row) <= {"0", "1"} for row in rows)
        assert rows[0][:7] == "1111111"                                             # a finder pattern
    assert phones.status(Console, r["id"]) == {"state": "pending"}
    code = r["code"].replace(" ", "")
    assert r["pair_url"].endswith("#pair=" + code)
    assert running.pair(code) == 201
    assert phones.status(Console, r["id"]) == {"state": "used"}


def test_console_without_a_running_bridge_says_so(tmp_path):
    class Console:
        db_path = tmp_path / "ind.sqlite"
        pocket_db = None
    assert phones.pocket_db(Console) == tmp_path / "ind.pocket.sqlite"
    assert phones.invite(Console) == {"running": False, "pocket_db": "ind.pocket.sqlite"}
    assert phones.status(Console, "0" * 16) == {"state": "stopped"}


# ---- nsf add-phone and nsf start -------------------------------------------------------------------------------------
def test_add_phone_prints_both_codes_and_waits_until_the_phone_pairs(running, capsys, monkeypatch):
    monkeypatch.setattr(start_cli.time, "sleep", lambda s: None)
    states = iter(["pending", "pending", "used"])
    monkeypatch.setattr(bridge, "invitation_status", lambda db, ident: next(states))
    assert cli.main(["add-phone"]) == 0
    out = capsys.readouterr().out
    assert out.count("▀") > 50 and "1  Trust your Spark" in out and "2  Pair this phone" in out
    assert f":{running.setup_port}/" in out and "Paired." in out


def test_add_phone_without_a_bridge_or_setup_explains_the_next_step(home, capsys, tmp_path):
    assert cli.main(["add-phone"]) == 2
    assert "nsf setup" in capsys.readouterr().err
    assert cli.main(["add-phone", "--pocket-db", str(tmp_path / "none.sqlite")]) == 2
    assert "Pocket is not running. Start NeuroSciFi with `nsf start`" in capsys.readouterr().err


def test_terminal_qr_is_black_on_white_with_colour_and_keeps_two_rows_per_line():
    rows = phones.qr_rows("https://192.0.2.10:8765/#pair=" + "x" * 32)
    text = start_cli.qr_text(rows, color=True)
    lines = text.splitlines()
    assert all(line.startswith("\x1b[30;107m") and line.endswith("\x1b[0m") for line in lines)
    assert len(lines) == (len(rows) + 4 + 1) // 2
    plain = start_cli.qr_text(rows, color=False)
    assert "\x1b" not in plain and plain.splitlines()[0] == "█" * (len(rows) + 4)   # the quiet zone, light


def test_start_without_setup_and_dry_run(home, capsys):
    assert cli.main(["start"]) == 2
    assert "run `nsf setup --db PATH` once" in capsys.readouterr().err
    report = setup(home, capsys)
    record = Path(report["record"])
    record.chmod(0o666)
    assert cli.main(["start", "--dry-run"]) == 2                                     # others could change what runs
    assert "writable only by it" in capsys.readouterr().err
    record.chmod(0o600)
    assert cli.main(["start", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("research: nsf web work --db") and "console: nsf console" in out and "pocket: nsf pocket serve" in out


def test_start_runs_the_services_and_stops_them_all_on_sigterm(home, capsys):
    report = setup(home, capsys, "--port", str(free_port()))
    record_path = Path(report["record"])
    record = json.loads(record_path.read_text())
    pocket = next(s for s in record["services"] if s["name"] == "pocket")
    pocket["argv"][pocket["argv"].index("--setup-port") + 1] = str(free_port())
    record["services"] = [s for s in record["services"] if s["name"] != "research"]   # no network worker here
    next(s for s in record["services"] if s["name"] == "console")["argv"] += ["--port", "0"]
    record_path.write_text(json.dumps(record))
    env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1])}
    planted = home / "cwd"                                     # a folder someone else could write: never imported
    planted.mkdir()
    marker = home / "imported"
    (planted / "argparse.py").write_text(f"open({str(marker)!r}, 'w').close()\nraise SystemExit(9)\n")
    proc = subprocess.Popen([sys.executable, "-P", "-m", "neuroscifi.cli", "start", "--no-browser"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=planted)
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(iter(proc.stdout.readline, "")), daemon=True)
    reader.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not any("NeuroSciFi is running." in line for line in lines):
        assert proc.poll() is None, "".join(lines)
        time.sleep(0.1)
    text = "".join(lines)
    assert "NeuroSciFi is running." in text and '[pocket] Pairing: in the console choose "Add a phone"' in text
    assert "One-use pairing code" not in text                                       # the QR codes replace it
    url_file = Path(record["console_url_file"])
    assert url_file.exists() and stat.S_IMODE(url_file.stat().st_mode) == 0o600
    assert url_file.read_text().strip() not in text                                 # the token never in the terminal
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(30) == 0
    reader.join(5)
    assert not url_file.exists(), "the console was stopped and removed its URL file"
    with pytest.raises(OSError):
        bridge.invitation_status(record["pocket_db"], "0" * 16)                    # the bridge is gone too
    assert not marker.exists(), "a service imported a module from the current folder"
