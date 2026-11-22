"""`nsf setup`: every one-time step, idempotent, with a dry run, the Brave key rules of `nsf web provider set-key`,
a skipped model, LAN detection, and the printed next commands parsed by the real `nsf` parser."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import shlex
import socket
import stat
import threading

import pytest

pytest.importorskip("cryptography")

from neuroscifi import cli  # noqa: E402
from neuroscifi.inference import config as iconfig  # noqa: E402
from neuroscifi.instance import store  # noqa: E402
from neuroscifi.pocket import cli as pocket_cli, tls  # noqa: E402
from neuroscifi.webcortex import state  # noqa: E402
from neuroscifi.webcortex.providers import keys  # noqa: E402

LAN = "192.0.2.10"
FAKE_KEY = "BSAfakeSetupTestKey0123456789"


def closed_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NSF_SEARCH_KEY_DIR", str(tmp_path / "search"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("NSF_INFERENCE_KEY_FILE", str(tmp_path / "no-inference-key"))
    monkeypatch.delenv("NSF_DB", raising=False)
    monkeypatch.setattr(tls, "DEFAULT_CA_DIR", tmp_path / "ca")
    monkeypatch.setattr(tls, "lan_addresses", lambda: [LAN])
    monkeypatch.setattr(socket, "gethostname", lambda: "spark")
    return tmp_path


def setup(capsys, home, *extra, model=None, json_out=True):
    model_args = [] if "--no-model" in extra else ["--model-url", model or closed_url()]
    argv = ["setup", "--db", str(home / "ind.sqlite"), *model_args, *extra]
    code = cli.main(argv + (["--json"] if json_out else []))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if json_out else out.out), out.err


def statuses(report):
    return {s["step"]: s["status"] for s in report["steps"]}


def tree(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


def test_a_fresh_run_does_every_step_and_names_every_lan_address(capsys, home):
    code, report, _ = setup(capsys, home)
    assert code == 0 and report["ok"]
    assert statuses(report) == {"individual": "done", "web": "done", "model": "skipped", "pocket_tls": "done",
                                "ca_export": "done", "brave": "skipped", "push": "skipped"}
    db = store.open_existing(home / "ind.sqlite")
    try:
        cfg = state.get_config(db)
        assert cfg.enabled and cfg.pocket_research and not iconfig.get_config(db).configured
    finally:
        db.close()
    pocket = home / "config" / "neuroscifi" / "pocket"
    seen = tls.status(pocket / "tls", ca_dir=home / "ca")
    assert set(seen["server"]["names"]) == {"spark", "spark.local", "localhost", "127.0.0.1", LAN}
    assert seen["server"]["verifies_against_ca"] is True
    assert stat.S_IMODE((pocket / "tls" / "privkey.pem").stat().st_mode) == 0o600
    assert (pocket / "neuroscifi-ca.crt").read_bytes()[:1] == b"\x30"          # DER for Android
    assert (pocket / "neuroscifi-ca.pem").read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    fingerprint = tls.export_ca(None, ca_dir=home / "ca")["fingerprints"]["sha256"]
    assert report["next"]["phone"]["ca_sha256"] == fingerprint
    assert report["next"]["phone"]["urls"] == ["https://spark.local:8765", f"https://{LAN}:8765"]


def test_a_second_run_changes_nothing_and_says_already_done(capsys, home):
    assert setup(capsys, home)[0] == 0
    before = tree(home)
    code, report, _ = setup(capsys, home)
    assert code == 0
    assert set(statuses(report).values()) <= {"already done", "skipped"}
    assert tree(home) == before                                  # the certificate was not issued again


def test_dry_run_changes_nothing_before_or_after_setup(capsys, home):
    code, report, _ = setup(capsys, home, "--dry-run")
    assert code == 0 and report["dry_run"]
    assert tree(home) == {}
    assert statuses(report)["individual"] == statuses(report)["pocket_tls"] == "would do"
    setup(capsys, home)
    before = tree(home)
    code, report, _ = setup(capsys, home, "--dry-run", "--push", "--brave-key-stdin", "--brave-daily-cap", "5")
    assert code == 0 and tree(home) == before
    assert statuses(report)["push"] == statuses(report)["brave"] == statuses(report)["brave_cap"] == "would do"


def test_without_a_database_it_stops_with_a_clear_message(capsys, home):
    assert cli.main(["setup", "--no-model"]) == 2
    err = capsys.readouterr().err
    assert "--db PATH, or export NSF_DB=PATH" in err and "encrypted volume" in err and "nsf doctor" in err


def test_brave_key_from_a_file_needs_0600_and_is_never_printed(capsys, home):
    source = home / "brave.key"
    source.write_text(FAKE_KEY + "\n")
    source.chmod(0o644)
    code, report, err = setup(capsys, home, "--brave-key-file", str(source))
    assert code == 2 and not report["ok"] and "mode 0644" in err and "chmod 600" in err
    assert keys.key_state("brave")["key"] == "missing"
    source.chmod(0o600)
    code, report, err = setup(capsys, home, "--brave-key-file", str(source), "--brave-daily-cap", "12")
    assert code == 0 and statuses(report)["brave"] == "done" and statuses(report)["brave_cap"] == "done"
    assert keys.key_state("brave")["key"] == "configured" and keys.read_key("brave") == FAKE_KEY
    assert stat.S_IMODE(keys.key_path("brave").stat().st_mode) == 0o600
    assert FAKE_KEY not in json.dumps(report) + err
    db = store.open_existing(home / "ind.sqlite")
    try:
        assert state.get_config(db).daily_provider_queries["brave"] == 12
    finally:
        db.close()
    code, report, _ = setup(capsys, home, "--brave-key-file", str(source), "--brave-daily-cap", "12")
    assert statuses(report)["brave"] == statuses(report)["brave_cap"] == "already done"


def test_brave_key_from_stdin_is_stored_and_never_in_output_or_logs(capsys, caplog, home, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("sys.stdin", io.StringIO(FAKE_KEY + "\n"))
    code, out, err = setup(capsys, home, "--brave-key-stdin", json_out=False)
    assert code == 0 and keys.read_key("brave") == FAKE_KEY
    assert "configured" in out
    assert FAKE_KEY not in out + err + caplog.text
    assert FAKE_KEY.encode() not in (home / "ind.sqlite").read_bytes()
    prompts = []

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("getpass.getpass", lambda prompt: prompts.append(prompt) or "BSAsecondKey987654321")
    code, report, err = setup(capsys, home, "--brave-key-stdin")
    assert code == 0 and prompts == ["Brave Search API key (not echoed): "]
    assert keys.read_key("brave") == "BSAsecondKey987654321" and "BSAsecondKey987654321" not in json.dumps(report)


class Models(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "test-model", "object": "model"}]}).encode()
        self.send_response(200 if self.path == "/v1/models" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_a_model_is_configured_only_when_one_answers(capsys, home):
    code, report, _ = setup(capsys, home)
    assert code == 0 and statuses(report)["model"] == "skipped" and "no model answered" in report["steps"][2]["detail"]
    assert statuses(setup(capsys, home, "--no-model", model=None)[1])["model"] == "skipped"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Models)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        code, report, _ = setup(capsys, home, model=url)
        assert code == 0 and statuses(report)["model"] == "done"
        db = store.open_existing(home / "ind.sqlite")
        try:
            cfg = iconfig.get_config(db)
            assert (cfg.base_url, cfg.model) == (url, "test-model")
        finally:
            db.close()
        assert statuses(setup(capsys, home, model=url)[1])["model"] == "already done"
    finally:
        server.shutdown()
        server.server_close()


def test_given_addresses_replace_detection_and_a_new_address_keeps_the_ca(capsys, home, monkeypatch):
    def no_detection():
        raise AssertionError("--ip replaces LAN detection")
    monkeypatch.setattr(tls, "lan_addresses", no_detection)
    code, report, _ = setup(capsys, home, "--ip", "198.51.100.7", "--name", "nsf-box")
    assert code == 0 and set(report["steps"][3]["names"]) == {"nsf-box", "localhost", "198.51.100.7", "127.0.0.1"}
    ca_key = (home / "ca" / "ca.key").read_bytes()
    code, report, _ = setup(capsys, home, "--ip", "198.51.100.7", "--ip", "198.51.100.8", "--name", "nsf-box")
    step = report["steps"][3]
    assert code == 0 and step["status"] == "already done" and "198.51.100.8" in step["warnings"][0]
    assert "--new-ca" in step["warnings"][0] and (home / "ca" / "ca.key").read_bytes() == ca_key


def test_the_printed_commands_parse_with_the_real_nsf_parser(capsys, home):
    code, report, _ = setup(capsys, home, "--push", "--port", "9443")
    assert code == 0 and statuses(report)["push"] == "done"
    paths = report["paths"]
    assert stat.S_IMODE((home / "config" / "neuroscifi" / "pocket" / "vapid.pem").stat().st_mode) == 0o600
    parser = cli.build_parser()
    work, console, serve = (parser.parse_args(shlex.split(c["command"])[1:]) for c in report["next"]["commands"])
    assert all(shlex.split(c["command"])[0] == "nsf" for c in report["next"]["commands"])
    assert (work.web_command, str(work.db)) == ("work", paths["db"])
    assert (console.command, str(console.db), str(console.url_file)) == ("console", paths["db"], paths["console_url"])
    assert not console.web_worker
    assert serve.handler is pocket_cli._serve
    assert (serve.bind, serve.port, serve.db, serve.instance_db, serve.push_key) == (
        LAN, 9443, paths["pocket_db"], paths["db"], paths["push_key"])
    assert (serve.cert, serve.key) == (paths["tls"] + "/fullchain.pem", paths["tls"] + "/privkey.pem")
    human = setup(capsys, home, "--push", "--port", "9443", json_out=False)[1]
    for item in report["next"]["commands"]:
        assert item["command"] in human
    assert "install the CA first" in human and report["next"]["phone"]["ca_sha256"] in human


def test_a_file_that_is_not_an_individual_is_refused_with_its_step(capsys, home):
    (home / "ind.sqlite").write_text("not a database")
    code, report, err = setup(capsys, home, "--no-model")
    assert code == 2 and not report["ok"] and err.startswith("nsf setup: individual:")
    assert (home / "ind.sqlite").read_text() == "not a database"
