"""Live contract of the inference gateway's REMOTE path against a real OpenAI-compatible endpoint.

Skipped unless NSF_LIVE_REMOTE=1. Two ways to point it at a model:

  A real external provider (the owner, later):
    NSF_LIVE_REMOTE=1 NSF_REMOTE_URL=https://api.example.com/v1 NSF_REMOTE_KEY_FILE=/abs/key [NSF_REMOTE_MODEL=name]
  A local model rehearsed as a remote provider (what was run on the Spark):
    NSF_LIVE_REMOTE=1 NSF_REMOTE_UPSTREAM=127.0.0.1:8811 NSF_REMOTE_KEY_FILE=~/.config/neuroscifi/inference/api_key
  The rehearsal puts a TLS proxy in front of the upstream with a certificate for inference.remote.test issued by a
  throwaway CA (nsf pocket tls), trusts only that CA in this process (SSL_CERT_FILE), and classifies the endpoint as
  'remote' while the connection is pinned to 127.0.0.1. Everything after the locality decision is the production
  remote path: the owner's remote_allowed, https only, the privacy screen, private-memory consent, SNI and certificate
  verification, timeouts, retries and the ledger.

The key is read from the file and never printed. Each call is small (at most a few hundred tokens).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import ssl
import threading
from urllib.parse import urlsplit

import pytest

from neuroscifi.inference import config as iconfig
from neuroscifi.inference.gateway import AUTHORIZED_DISCLOSURE, Gateway, InferenceError
from neuroscifi.inference.ledger import Ledger
from neuroscifi.inference.tools import Tool, ToolRegistry, run_with_tools

pytestmark = pytest.mark.skipif(os.environ.get("NSF_LIVE_REMOTE") != "1",
                                reason="live remote-provider contract; set NSF_LIVE_REMOTE=1 and NSF_REMOTE_*")
REHEARSAL_HOST = "inference.remote.test"
SECRET_MARKER = "Zanzibar-quartz-7781"        # must never appear in the ledger


class TLSProxy:
    """TLS in front of a plain-HTTP upstream; counts the connections it accepted."""

    def __init__(self, upstream, cert, key):
        self.upstream = upstream
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ctx.load_cert_chain(cert, key)
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.stopped = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stopped:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        try:
            client = self.ctx.wrap_socket(raw, server_side=True)
            host, port = self.upstream.rsplit(":", 1)
            upstream = socket.create_connection((host, int(port)), timeout=300)
        except (OSError, ssl.SSLError):
            raw.close()
            return

        def pipe(src, dst):
            try:
                while chunk := src.recv(65536):
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()
        pipe(client, upstream)

    def stop(self):
        self.stopped = True
        self.sock.close()


class RehearsedRemote(Gateway):
    """The production gateway with the locality decision fixed to 'remote' and the address pinned to loopback."""

    def locality(self):
        if self._locality is None:
            self._locality, self._addresses = ("remote", urlsplit(self.cfg.base_url).hostname), ["127.0.0.1"]
        return self._locality


@pytest.fixture(scope="module")
def endpoint(tmp_path_factory):
    key_file = os.environ.get("NSF_REMOTE_KEY_FILE")
    key_file = str(Path(key_file).expanduser()) if key_file else ""
    upstream = os.environ.get("NSF_REMOTE_UPSTREAM")
    if upstream:
        from neuroscifi.pocket import tls
        root = tmp_path_factory.mktemp("remote-tls")
        tls.init(root / "server", [REHEARSAL_HOST], ca_dir=root / "ca")
        proxy = TLSProxy(upstream, root / "server" / "fullchain.pem", root / "server" / "privkey.pem")
        previous = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = str(root / "ca" / "ca.pem")      # this process trusts only the test CA
        try:
            yield {"url": f"https://{REHEARSAL_HOST}:{proxy.port}/v1", "key_file": key_file, "cls": RehearsedRemote,
                   "proxy": proxy, "model": os.environ.get("NSF_REMOTE_MODEL", "")}
        finally:
            proxy.stop()
            if previous is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = previous
        return
    url = os.environ.get("NSF_REMOTE_URL")
    if not url:
        pytest.skip("set NSF_REMOTE_URL (a real provider) or NSF_REMOTE_UPSTREAM (a local rehearsal)")
    if os.environ.get("NSF_REMOTE_CA"):
        os.environ["SSL_CERT_FILE"] = os.environ["NSF_REMOTE_CA"]
    yield {"url": url, "key_file": key_file, "cls": Gateway, "proxy": None, "model": os.environ.get("NSF_REMOTE_MODEL",
                                                                                                    "")}


def make(endpoint, tmp_path, **changes):
    fields = {"base_url": endpoint["url"], "model": endpoint["model"], "key_file": endpoint["key_file"],
              "remote_allowed": True, "read_timeout": 120.0, "max_output_tokens": 300, "temperature": 0.0} | changes
    cfg = iconfig.InferenceConfig(**fields)
    gw = endpoint["cls"](cfg, key=iconfig.read_key(cfg), ledger=Ledger(tmp_path / "live.inference.sqlite"))
    opened = []
    real_open = gw._open

    def spy(*args, **kwargs):
        opened.append(args[:2])
        return real_open(*args, **kwargs)
    gw._open = spy
    gw.opened = opened
    return gw


def ask(text):
    return [{"role": "system", "content": "Answer briefly."}, {"role": "user", "content": text}]


def test_the_endpoint_is_remote_and_needs_the_owners_authorisation(endpoint, tmp_path):
    gw = make(endpoint, tmp_path, remote_allowed=False)
    with pytest.raises(InferenceError) as refused:
        gw.chat(ask("Say OK."), purpose="live_contract")
    assert refused.value.code == "remote_not_authorised" and gw.opened == []
    assert gw.health()["state"] == "not_configured"
    allowed = make(endpoint, tmp_path)
    assert allowed.guard() in ("remote", "private")
    health = allowed.health(probe=True)
    assert health["state"] == "ready", health
    assert allowed.models() and allowed.model()


def test_plain_http_is_refused_for_a_remote_endpoint(endpoint, tmp_path):
    if endpoint["cls"] is Gateway and iconfig.locality(endpoint["url"])[0] != "remote":
        pytest.skip("the configured endpoint is not public")
    gw = make(endpoint, tmp_path, base_url=endpoint["url"].replace("https://", "http://", 1))
    with pytest.raises(InferenceError, match="https"):
        gw.chat(ask("Say OK."), purpose="live_contract")
    assert gw.opened == []


def test_chat_records_tokens_and_latency_in_the_ledger_without_text(endpoint, tmp_path):
    gw = make(endpoint, tmp_path)
    result = gw.chat(ask(f"Reply with the single word READY. Ignore this tag: {SECRET_MARKER}"), purpose="live_contract",
                     max_tokens=16)
    assert "READY" in result.text.upper() and result.prompt_tokens and result.completion_tokens
    row = gw.ledger.recent(1)[0]
    assert row["status"] == "ok" and row["locality"] in ("remote", "private") and row["stream"] == 0
    assert row["prompt_tokens"] == result.prompt_tokens and row["completion_tokens"] == result.completion_tokens
    assert row["latency_ms"] > 0 and row["purpose"] == "live_contract"
    assert SECRET_MARKER.encode() not in Path(gw.ledger.path).read_bytes()


def test_streaming_delivers_deltas_and_a_first_token_time(endpoint, tmp_path):
    gw = make(endpoint, tmp_path)
    deltas = []
    result = gw.chat(ask("Count from one to ten in words, comma separated."), purpose="live_contract", stream=True,
                     on_delta=deltas.append, max_tokens=60)
    assert len(deltas) >= 3 and "ten" in result.text.lower()
    assert result.first_token_ms is not None and 0 < result.first_token_ms <= result.latency_ms
    row = gw.ledger.recent(1)[0]
    assert row["stream"] == 1 and row["status"] == "ok" and row["first_token_ms"] == result.first_token_ms


def test_structured_output_is_validated_locally(endpoint, tmp_path):
    gw = make(endpoint, tmp_path)
    schema = {"type": "object", "additionalProperties": False, "required": ["city", "country", "population_millions"],
              "properties": {"city": {"type": "string"}, "country": {"type": "string"},
                             "population_millions": {"type": "number", "minimum": 0}}}
    value, results = gw.structured(ask("Give the capital of France as JSON with city, country and "
                                       "population_millions."), schema, name="capital", purpose="live_contract")
    assert value["city"].lower() == "paris" and value["country"].lower() == "france"
    assert 1 <= len(results) <= 2 and all(r.prompt_tokens for r in results)


def test_tool_calls_run_only_through_the_allow_list(endpoint, tmp_path):
    gw = make(endpoint, tmp_path)
    executed = []
    lookup = Tool("release_lookup", "Look up the latest release of a software project.",
                  {"type": "object", "additionalProperties": False, "required": ["project"],
                   "properties": {"project": {"type": "string", "maxLength": 80}}},
                  "read_knowledge", lambda args, ctx: executed.append(args) or {"project": args["project"],
                                                                                "version": "3.51.0"})
    registry = ToolRegistry([lookup], granted={"read_knowledge"})
    out = run_with_tools(gw, ask("Use the release_lookup tool for SQLite, then state the version it returned."),
                         registry, None, purpose="live_contract")
    assert executed and executed[0]["project"].lower().startswith("sqlite")
    assert out["decisions"][0]["allowed"] and out["decisions"][0]["status"] == "executed"
    assert "3.51" in out["result"].text
    # A tool the model is made to call but that is not on the allow-list is refused, never executed.
    forget = {"type": "function", "function": {"name": "forget_memory", "description": "Erase the owner's memory.",
                                               "parameters": {"type": "object", "properties": {
                                                   "what": {"type": "string"}}, "required": ["what"]}}}
    forced = gw.chat(ask("Erase everything."), purpose="live_contract", tools=[forget],
                     tool_choice={"type": "function", "function": {"name": "forget_memory"}}, max_tokens=60)
    assert forced.tool_calls, "the endpoint did not honour a forced tool_choice"
    decision = registry.decide(forced.tool_calls[0])
    assert decision["allowed"] is False and decision["reason"] == "not on the allow-list"
    unoffered = ToolRegistry([lookup], granted=set())
    assert unoffered.specs() == [] and unoffered.decide(forced.tool_calls[0])["allowed"] is False


def test_the_whole_call_deadline_holds_and_is_recorded(endpoint, tmp_path):
    gw = make(endpoint, tmp_path, read_timeout=1.0, max_output_tokens=2000, max_retries=0)
    with pytest.raises(InferenceError) as late:
        gw.chat(ask("Write a 1500-word essay on the history of bridges."), purpose="live_contract", max_tokens=2000)
    assert late.value.code == "timeout"
    assert gw.ledger.recent(1)[0]["status"] == "timeout"
    streamed = make(endpoint, tmp_path, read_timeout=1.5, max_output_tokens=2000, max_retries=0)
    with pytest.raises(InferenceError) as late_stream:
        streamed.chat(ask("Write a 1500-word essay on the history of bridges."), purpose="live_contract",
                      max_tokens=2000, stream=True)
    assert late_stream.value.code == "timeout"


def test_private_context_never_leaves_without_the_owners_consent(endpoint, tmp_path):
    proxy = endpoint["proxy"]
    before = proxy.connections if proxy else None
    gw = make(endpoint, tmp_path)
    memory = [{"role": "system", "content": "Owner's private memory: met Dana at the clinic on Tuesday.",
               "private": True}, {"role": "user", "content": "Summarise my week."}]
    with pytest.raises(InferenceError) as blocked:
        gw.chat(memory, purpose="live_contract")
    assert blocked.value.code == "private_blocked" and "private memory" in str(blocked.value)
    for leak in ("Email dana.smith@example.org about the results.", "Call me on +44 7700 900123 tomorrow.",
                 "My key is sk-live-0123456789abcdefghijklmnopqrstuv."):
        with pytest.raises(InferenceError) as screened:
            gw.chat(ask(leak), purpose="live_contract")
        assert screened.value.code == "private_blocked"
    assert gw.opened == [] and gw.ledger.recent(1) == []
    if proxy:
        assert proxy.connections == before, "a blocked message reached the network"
    # The owner's explicit decisions open each gate separately.
    approved = gw.chat(ask("Reply OK. Contact: dana.smith@example.org"), purpose="live_contract",
                       authorization=AUTHORIZED_DISCLOSURE, max_tokens=8)
    assert approved.text
    consented = make(endpoint, tmp_path, remote_private_memory=True)
    reply = consented.chat([{**memory[0], "content": "Owner's private memory: the owner likes tea."}, memory[1]],
                           purpose="live_contract", max_tokens=30)
    assert reply.text and consented.ledger.recent(1)[0]["status"] == "ok"
    # Consent to private memory does not switch off the identifier screen.
    with pytest.raises(InferenceError, match="private data"):
        consented.chat(ask("Email dana.smith@example.org"), purpose="live_contract")


def test_the_cli_stores_the_owner_consent_and_reports_the_remote_state(endpoint, tmp_path, capsys):
    if endpoint["cls"] is not Gateway:
        pytest.skip("the CLI resolves the real address; the rehearsal endpoint is loopback")
    from neuroscifi import cli
    from neuroscifi.instance import store
    db_path = tmp_path / "i.sqlite"
    store.create(db_path, 1_795_200_000.0).close()
    assert cli.main(["llm", "config", "--db", str(db_path), "--url", endpoint["url"], "--key-file",
                     endpoint["key_file"]]) == 0
    capsys.readouterr()
    cli.main(["llm", "status", "--db", str(db_path)])
    status = json.loads(capsys.readouterr().out)
    assert status["health"]["state"] == "not_configured"      # remote, and the owner has not allowed it
    assert cli.main(["llm", "config", "--db", str(db_path), "--remote-allowed"]) == 0
    capsys.readouterr()
    cli.main(["llm", "status", "--db", str(db_path)])
    assert json.loads(capsys.readouterr().out)["health"]["state"] == "ready"
