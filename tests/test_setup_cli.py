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
                                "ca_export": "done", "brave": "skipped", "push": "skipped", "memory": "skipped"}
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
    owned_by = None

    def do_GET(self):
        item = {"id": "test-model", "object": "model"} | ({"owned_by": self.owned_by} if self.owned_by else {})
        body = json.dumps({"data": [item]}).encode()
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
            assert cfg.background_priority == 0       # a server that is not vLLM is never sent a `priority`
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
    assert "nsf start" in human and 'choose "Add a phone"' in human and "nsf add-phone" in human   # ADR-043
    assert (console.pocket_db is not None and str(console.pocket_db) == paths["pocket_db"])
    assert (serve.setup_port, serve.ca_file) == (9442, paths["ca_der"])


def test_a_file_that_is_not_an_individual_is_refused_with_its_step(capsys, home):
    (home / "ind.sqlite").write_text("not a database")
    code, report, err = setup(capsys, home, "--no-model")
    assert code == 2 and not report["ok"] and err.startswith("nsf setup: individual:")
    assert (home / "ind.sqlite").read_text() == "not a database"


def test_a_vllm_endpoint_gets_the_background_priority_and_setup_says_so(capsys, home):
    """deploy/vllm runs the model with priority scheduling: setup sends background research behind the owner's
    questions when the endpoint names itself vLLM (owned_by in /models), and tells the owner."""
    handler = type("VllmModels", (Models,), {"owned_by": "vllm"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        code, report, _ = setup(capsys, home, model=url)
        assert code == 0 and "priority 10, behind your questions" in report["steps"][2]["detail"]
        db = store.open_existing(home / "ind.sqlite")
        try:
            assert iconfig.get_config(db).background_priority == 10
        finally:
            db.close()
    finally:
        server.shutdown()
        server.server_close()


# ---- step 7: memory by meaning, when the memory models of deploy/memory answer -------------------------------

MEMORY_KEY = "nsfm-test-memory-key"


class MemoryModel(BaseHTTPRequestHandler):
    """A stand-in for one memory model of deploy/memory: /v1/models, and /v1/embeddings or /v1/rerank. It answers
    only the memory key, and records what it was sent."""
    served = "qwen3-embedding-0.6b"
    kind = "embed"
    broken = False            # answers /models but fails the real request
    seen = None               # [(path, authorization, body)]

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, body=None):
        self.seen.append((self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != f"Bearer {MEMORY_KEY}":
            return self._reply(401, {"error": "unauthorized"})
        if self.path == "/v1/models":
            return self._reply(200, {"data": [{"id": self.served, "object": "model", "owned_by": "vllm"}]})
        if self.broken:
            return self._reply(500, {"error": "model failed to load"})
        if self.path == "/v1/embeddings" and self.kind == "embed":
            return self._reply(200, {"data": [{"index": i, "embedding": [0.1, 0.2, 0.3]}
                                              for i, _ in enumerate(body["input"])]})
        if self.path == "/v1/rerank" and self.kind == "rerank":
            return self._reply(200, {"results": [{"index": i, "relevance_score": 0.5}
                                                 for i, _ in enumerate(body["documents"])]})
        return self._reply(404, {"error": "no such route"})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))))

    def log_message(self, *args):
        pass


@pytest.fixture
def memory_models(home):
    """Both memory models answering on free loopback ports, and their key file (0600) where deploy/memory puts it.
    Yields {"embed": server, "rerank": server, "args": [...], "key_file": path}; a server can be stopped."""
    folder = home / "memory"
    folder.mkdir(mode=0o700)
    key_file = folder / "api_key"
    key_file.write_text(MEMORY_KEY + "\n")
    key_file.chmod(0o600)
    out = {"key_file": key_file, "args": ["--memory-key-file", str(key_file)], "seen": []}
    for kind, served in (("embed", "qwen3-embedding-0.6b"), ("rerank", "qwen3-reranker-0.6b")):
        handler = type(f"Memory{kind}", (MemoryModel,), {"kind": kind, "served": served, "seen": out["seen"]})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        out[kind] = server
        out["args"] += [f"--memory-{kind}-url", f"http://127.0.0.1:{server.server_address[1]}/v1"]
    yield out
    for kind in ("embed", "rerank"):
        out[kind].shutdown()
        out[kind].server_close()


def memory_of(home):
    """(semantic settings, embed config, rerank config) of the individual."""
    from neuroscifi.webcortex import semantic
    db = store.open_existing(home / "ind.sqlite")
    try:
        return semantic.get_config(db), iconfig.get_config(db, "embed"), iconfig.get_config(db, "rerank")
    finally:
        db.close()


def memory_step(report):
    return next(s for s in report["steps"] if s["step"] == "memory")


def test_without_the_memory_models_setup_says_how_to_add_them_and_contacts_nothing(capsys, home, monkeypatch):
    """No key file of deploy/memory on this computer: memory stays lexical, one line says how to add it, and no
    endpoint is contacted (the suite points NSF_MEMORY_DIR at an empty folder for the same reason)."""
    from neuroscifi import setup_cli

    def no_probe(*args):
        raise AssertionError("nothing may be contacted without the memory key file")
    monkeypatch.setattr(setup_cli, "_memory_probe", no_probe)
    code, report, _ = setup(capsys, home)
    step = memory_step(report)
    assert code == 0 and step["status"] == "skipped" and report["steps"][-1] is step
    assert "memory is ranked by its words" in step["detail"] and "bash deploy/memory/run.sh start" in step["detail"]
    settings, embed, rerank = memory_of(home)
    assert not settings.enabled and not embed.configured and not rerank.configured
    assert memory_step(setup(capsys, home, "--no-memory")[1])["detail"] == "--no-memory"


def test_both_memory_models_answering_are_configured_with_their_own_key_and_memory_is_switched_on(
        capsys, home, memory_models):
    code, report, _ = setup(capsys, home, *memory_models["args"])
    step = memory_step(report)
    assert code == 0 and step["status"] == "done"
    assert step["detail"] == ("memory now finds a fact asked in other words, and leaves out what is not about the "
                              "question (embedding model and reranker on this computer)")
    assert step["models"] == {"embed": "qwen3-embedding-0.6b", "rerank": "qwen3-reranker-0.6b"}
    settings, embed, rerank = memory_of(home)
    assert settings.enabled and settings.rerank_template == "qwen3" and settings.floor == 0.02
    port = {kind: memory_models[kind].server_address[1] for kind in ("embed", "rerank")}
    assert (embed.base_url, embed.model, embed.key_file) == \
        (f"http://127.0.0.1:{port['embed']}/v1", "qwen3-embedding-0.6b", str(memory_models["key_file"]))
    assert (rerank.base_url, rerank.model, rerank.key_file) == \
        (f"http://127.0.0.1:{port['rerank']}/v1", "qwen3-reranker-0.6b", str(memory_models["key_file"]))
    # each model passed a real request of its kind, with the memory key, and was sent nothing of the individual
    paths = {path for path, _, _ in memory_models["seen"]}
    assert {"/v1/models", "/v1/embeddings", "/v1/rerank"} <= paths
    assert {auth for _, auth, _ in memory_models["seen"]} == {f"Bearer {MEMORY_KEY}"}
    sent = {path: body for path, _, body in memory_models["seen"] if body}
    assert sent["/v1/embeddings"]["input"] == ["memory check"]
    assert (sent["/v1/rerank"]["query"], sent["/v1/rerank"]["documents"]) == ("memory check", ["memory check"])
    assert MEMORY_KEY not in json.dumps(report)
    db = store.open_existing(home / "ind.sqlite")
    try:
        assert not iconfig.get_config(db).configured          # the answer model is not this step's business
    finally:
        db.close()


def test_the_memory_step_is_idempotent_and_a_dry_run_changes_nothing(capsys, home, memory_models):
    code, report, _ = setup(capsys, home, "--dry-run", *memory_models["args"])
    step = memory_step(report)
    assert code == 0 and step["status"] == "would do" and step["detail"].startswith("let memory find a fact")
    assert not (home / "ind.sqlite").exists()
    setup(capsys, home, *memory_models["args"])
    before = (home / "ind.sqlite").read_bytes()
    seen = len(memory_models["seen"])
    code, report, _ = setup(capsys, home, *memory_models["args"])
    step = memory_step(report)
    assert code == 0 and step["status"] == "already done" and "embedding model and reranker" in step["detail"]
    assert len(memory_models["seen"]) == seen             # nothing probed again
    assert (home / "ind.sqlite").read_bytes() == before
    assert memory_step(setup(capsys, home, "--dry-run", *memory_models["args"])[1])["status"] == "already done"


def test_with_the_embedding_model_alone_memory_is_on_without_the_cut_and_the_reranker_is_added_later(
        capsys, home, memory_models):
    memory_models["rerank"].RequestHandlerClass.broken = True          # it lists a model but cannot score
    code, report, _ = setup(capsys, home, *memory_models["args"])
    step = memory_step(report)
    assert code == 0 and step["status"] == "done" and step["models"] == {"embed": "qwen3-embedding-0.6b"}
    assert "embedding model on this computer" in step["detail"] and "The reranker did not answer" in step["detail"]
    assert "bash deploy/memory/run.sh start rerank, then nsf setup again" in step["detail"]
    settings, embed, rerank = memory_of(home)
    assert settings.enabled and embed.configured and not rerank.configured
    again = memory_step(setup(capsys, home, *memory_models["args"])[1])
    assert again["status"] == "already done" and "ranked by the embedding model alone" in again["detail"]
    memory_models["rerank"].RequestHandlerClass.broken = False         # the reranker is started
    later = memory_step(setup(capsys, home, *memory_models["args"])[1])
    assert later["status"] == "done" and later["models"] == {"rerank": "qwen3-reranker-0.6b"}
    settings, embed, rerank = memory_of(home)
    assert settings.enabled and embed.configured and rerank.model == "qwen3-reranker-0.6b"
    assert memory_step(setup(capsys, home, *memory_models["args"])[1])["status"] == "already done"


def test_without_the_embedding_model_nothing_is_configured_even_when_the_reranker_answers(
        capsys, home, memory_models):
    memory_models["embed"].RequestHandlerClass.broken = True
    step = memory_step(setup(capsys, home, *memory_models["args"])[1])
    assert step["status"] == "skipped" and "no embedding model answered" in step["detail"]
    assert "the reranker answers, but it needs the embedding model" in step["detail"]
    settings, embed, rerank = memory_of(home)
    assert not settings.enabled and not embed.configured and not rerank.configured


def test_a_memory_setting_the_owner_made_by_hand_is_never_changed(capsys, home, memory_models):
    """An endpoint the owner set by hand before setup ever configured memory is kept, and memory is not switched
    on behind it."""
    assert setup(capsys, home, "--no-memory")[0] == 0
    db = store.open_existing(home / "ind.sqlite")
    try:
        iconfig.set_config(db, {"base_url": "http://127.0.0.1:9/v1", "model": "my-embedder",
                                "key_file": str(memory_models["key_file"])}, 1.0, provider="embed")
    finally:
        db.close()
    seen = len(memory_models["seen"])
    step = memory_step(setup(capsys, home, *memory_models["args"])[1])
    assert step["status"] == "already done" and "kept as you set it (memory by meaning is off)" in step["detail"]
    settings, embed, rerank = memory_of(home)
    assert not settings.enabled and embed.model == "my-embedder" and not rerank.configured
    assert len(memory_models["seen"]) == seen             # and nothing was probed


def test_memory_the_owner_switched_off_after_setup_stays_off(capsys, home, memory_models):
    from neuroscifi.webcortex import semantic
    assert memory_step(setup(capsys, home, *memory_models["args"])[1])["status"] == "done"
    db = store.open_existing(home / "ind.sqlite")
    try:
        semantic.set_config(db, {"enabled": False, "floor": 0.1}, 2.0)
    finally:
        db.close()
    step = memory_step(setup(capsys, home, *memory_models["args"])[1])
    assert step["status"] == "already done" and "memory by meaning is off" in step["detail"]
    settings, embed, rerank = memory_of(home)
    assert not settings.enabled and settings.floor == 0.1 and embed.configured and rerank.configured


def test_a_memory_key_file_everyone_can_read_and_a_wrong_key_are_refused_with_the_reason(capsys, home, memory_models):
    memory_models["key_file"].chmod(0o644)
    step = memory_step(setup(capsys, home, *memory_models["args"])[1])
    assert step["status"] == "skipped" and "readable by every user" in step["detail"]
    memory_models["key_file"].chmod(0o600)
    memory_models["key_file"].write_text("nsfm-another-key\n")
    step = memory_step(setup(capsys, home, *memory_models["args"])[1])
    assert step["status"] == "skipped" and "(unauthorised)" in step["detail"]
    assert not memory_of(home)[1].configured


def test_a_reranker_that_is_not_qwen3_is_given_the_plain_prompt_form(capsys, home, memory_models):
    memory_models["rerank"].RequestHandlerClass.served = "bge-reranker-v2-m3"
    assert memory_step(setup(capsys, home, *memory_models["args"])[1])["status"] == "done"
    settings, _, rerank = memory_of(home)
    assert settings.rerank_template == "plain" and rerank.model == "bge-reranker-v2-m3"


def test_the_human_report_names_the_memory_step_in_plain_words(capsys, home, memory_models):
    code, text, _ = setup(capsys, home, *memory_models["args"], json_out=False)
    import re
    assert code == 0 and re.search(r"^  8\. memory +done +memory now finds a fact asked in other words", text, re.M)
    assert MEMORY_KEY not in text
