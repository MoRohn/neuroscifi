"""The adaptive inference policy and the memory-model calls of the gateway, against the fake OpenAI-compatible
server: scheduling priority for background calls, the critique switch for spoken answers, prefix-cache accounting,
per-provider settings, and /embeddings and /rerank with their shape checks and the local-first rule."""
import json
import socket
import sqlite3
from urllib.parse import urlsplit

import pytest

from fake_openai import make_gateway
from neuroscifi.inference import config as iconfig
from neuroscifi.inference.gateway import Gateway, InferenceError
from neuroscifi.inference.ledger import Ledger
from neuroscifi.instance import store
from neuroscifi.instance.errors import ValidationError
from neuroscifi.webcortex import reasoning
from test_inference_reasoning import Q, T0, answer, fine, report

MESSAGES = [{"role": "user", "content": "hello"}]
TWO = ("Zephyr 4.2.0 was released on 17 November 2026.", [1, 2], ["C1"])


def remote_resolver(host, *args):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


# ---- scheduling priority ---------------------------------------------------------------------------------------

def test_a_call_carries_no_priority_field_unless_one_is_set(gateway, fake):
    """An endpoint that does not know `priority` must never see it: the default request is what 0.7.0 sent."""
    gateway.chat(MESSAGES, purpose="x")
    gateway.chat(MESSAGES, purpose="x", priority=0)
    gateway.chat(MESSAGES, purpose="x", priority=True)       # a bool is not a priority
    assert all("priority" not in r for r in fake.requests)
    gateway.chat(MESSAGES, purpose="x", priority=10)
    assert fake.requests[-1]["priority"] == 10
    rows = gateway.ledger.recent(5)
    assert rows[0]["priority"] == 10 and rows[1]["priority"] is None and rows[0]["provider"] == "chat"


def test_background_reasoning_sends_the_configured_priority_on_both_calls(fake, key_file, tmp_path):
    """An autonomous task's answer and critique wait behind the owner's calls on a priority-scheduling server."""
    gw = make_gateway(fake, key_file, tmp_path, background_priority=10)
    fake.replies += [answer(TWO), fine()]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw, background=True)
    assert out["status"] == "model" and [r.get("priority") for r in fake.requests] == [10, 10]


def test_the_owners_reasoning_never_carries_the_background_priority(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, background_priority=10)
    fake.replies += [answer(TWO), fine()]
    reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw)
    assert all("priority" not in r for r in fake.requests) and len(fake.requests) == 2


def test_background_reasoning_sends_nothing_when_no_priority_is_configured(gateway, fake):
    fake.replies += [answer(TWO), fine()]
    reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gateway, background=True)
    assert all("priority" not in r for r in fake.requests)


# ---- the critique pass and spoken answers ----------------------------------------------------------------------

def test_a_spoken_answer_keeps_the_critique_pass_by_default(gateway, fake):
    fake.replies += [answer(TWO), fine()]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gateway, spoken=True)
    assert len(fake.requests) == 2 and out["critique"] == {"status": "ok", "removed": 0, "flagged": 0,
                                                           "judged": 1, "restatements": 0}


def test_with_critique_voice_off_a_spoken_answer_makes_one_model_call_and_says_why(fake, key_file, tmp_path):
    """Measured: the critique call is 42 % of a task's time and its marks are never spoken. The same sentences
    are kept (the guard ran); the skip is recorded, not silent; a shown answer is still critiqued."""
    gw = make_gateway(fake, key_file, tmp_path, critique_voice=False)
    fake.replies += [answer(TWO)]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw, spoken=True)
    assert len(fake.requests) == 1 and out["status"] == "model"
    assert [s["text"] for s in out["answer"]] == [TWO[0]]
    assert out["critique"]["status"] == "skipped" and "spoken answer" in out["critique"]["reason"]
    fake.replies += [answer(TWO), fine()]
    out = reasoning.apply(None, report(), None, question=Q, now=T0, gateway=gw)
    assert len(fake.requests) == 3 and out["critique"]["status"] == "ok"


# ---- prefix-cache accounting -----------------------------------------------------------------------------------

def test_cached_prompt_tokens_are_recorded_when_the_server_reports_them(gateway, fake):
    usage = {"prompt_tokens": 1200, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 900}}
    reply = {"model": "fake-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                                 "finish_reason": "stop"}], "usage": usage}
    fake.replies.append({"body": reply})
    result = gateway.chat(MESSAGES, purpose="x")
    assert result.cached_tokens == 900 and gateway.ledger.recent(1)[0]["cached_tokens"] == 900
    gateway.chat(MESSAGES, purpose="x")                       # a server that says nothing: unknown, not zero
    assert gateway.ledger.recent(1)[0]["cached_tokens"] is None
    summary = gateway.ledger.summary()
    assert summary["cached_prompt_tokens"] == 900 and summary["cached_share"] == 0.75
    assert summary["by_purpose"]["x"]["calls"] == 2


@pytest.mark.parametrize("details", [{"cached_tokens": -1}, {"cached_tokens": 5000}, {"cached_tokens": True},
                                     {"cached_tokens": "900"}, "many", None])
def test_an_implausible_cached_token_count_is_unknown(gateway, fake, details):
    usage = {"prompt_tokens": 1200, "completion_tokens": 5, "prompt_tokens_details": details}
    fake.replies.append({"body": {"model": "fake-model", "usage": usage, "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}})
    assert gateway.chat(MESSAGES, purpose="x").cached_tokens is None


def test_a_ledger_written_by_0_7_0_gains_the_new_columns_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.inference.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY, at REAL NOT NULL, purpose TEXT NOT NULL, model TEXT, "
               "locality TEXT, stream INTEGER, status TEXT NOT NULL, error TEXT, prompt_tokens INTEGER, "
               "completion_tokens INTEGER, latency_ms REAL, first_token_ms REAL, attempts INTEGER, "
               "tokens_per_second REAL, task_id TEXT)")
    db.execute("INSERT INTO calls(at,purpose,status,prompt_tokens) VALUES(1.0,'research_answer','ok',10)")
    db.commit()
    db.close()
    ledger = Ledger(path)
    ledger.record({"at": 2.0, "purpose": "embed", "status": "ok", "provider": "embed", "prompt_tokens": 4})
    new, old = ledger.recent(2)
    assert old["purpose"] == "research_answer" and old["provider"] is None and old["cached_tokens"] is None
    assert new["provider"] == "embed" and ledger.summary()["calls"] == 2
    Ledger(path)                                               # opening again changes nothing


# ---- providers -------------------------------------------------------------------------------------------------

def test_each_provider_has_its_own_settings_and_chat_keeps_the_0_7_0_row(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    iconfig.set_config(db, {"base_url": "http://127.0.0.1:8811/v1", "model": "answer"}, T0)
    iconfig.set_config(db, {"base_url": "http://127.0.0.1:8813/v1", "model": "embedder"}, T0, provider="embed")
    assert iconfig.get_config(db).model == "answer" and iconfig.get_config(db, "embed").model == "embedder"
    assert not iconfig.get_config(db, "rerank").configured
    keys = {row[0] for row in db.execute("SELECT key FROM meta WHERE key LIKE 'inference%'")}
    assert keys == {"inference", "inference:embed"}
    with pytest.raises(ValidationError):
        iconfig.get_config(db, "elsewhere")
    gw = Gateway.from_db(db, tmp_path / "i.sqlite", provider="embed")
    assert gw.provider == "embed" and gw.cfg.model == "embedder" and gw.db_path is None and gw.ledger is not None
    assert Gateway.from_db(db, tmp_path / "i.sqlite").db_path == tmp_path / "i.sqlite"


def test_a_memory_model_is_never_sent_the_answer_models_key(tmp_path, monkeypatch):
    """Review: an endpoint configured without a key file fell back to the answer model's key for every provider,
    so whoever listened on a memory model's port received it. Only `chat` falls back."""
    key = tmp_path / "chat_key"
    key.write_text("chat-secret\n")
    key.chmod(0o600)
    monkeypatch.setenv("NSF_INFERENCE_KEY_FILE", str(key))
    cfg = iconfig.InferenceConfig(base_url="http://127.0.0.1:18441/v1", model="m")
    assert iconfig.read_key(cfg) == "chat-secret" and iconfig.read_key(cfg, "chat") == "chat-secret"
    assert iconfig.read_key(cfg, "embed") is None and iconfig.read_key(cfg, "rerank") is None
    assert iconfig.public(cfg, "embed")["key_present"] is False
    db = store.create(tmp_path / "k.sqlite", T0)
    iconfig.set_config(db, {"base_url": "http://127.0.0.1:18441/v1", "model": "m"}, T0, provider="embed")
    assert Gateway.from_db(db, tmp_path / "k.sqlite", provider="embed").key is None
    own = tmp_path / "memory_key"
    own.write_text("memory-secret\n")
    own.chmod(0o600)
    iconfig.set_config(db, {"key_file": str(own)}, T0, provider="embed")
    assert Gateway.from_db(db, tmp_path / "k.sqlite", provider="embed").key == "memory-secret"


def test_a_locked_ledger_is_an_error_at_open_not_a_ledger_without_its_columns(tmp_path, monkeypatch):
    """Review: every OperationalError of the column upgrade was ignored, "database is locked" included; the
    columns then stayed missing and every later record failed in silence."""
    import neuroscifi.inference.ledger as ledger_mod
    path = tmp_path / "old.inference.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY, at REAL NOT NULL, purpose TEXT NOT NULL, model TEXT, "
               "locality TEXT, stream INTEGER, status TEXT NOT NULL, error TEXT, prompt_tokens INTEGER, "
               "completion_tokens INTEGER, latency_ms REAL, first_token_ms REAL, attempts INTEGER, "
               "tokens_per_second REAL, task_id TEXT)")
    db.commit()
    db.execute("BEGIN IMMEDIATE")                          # another process holds the write lock
    monkeypatch.setattr(ledger_mod.Ledger, "_connect", lambda self: sqlite3.connect(self.path, timeout=0.05))
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        Ledger(path)
    db.rollback()
    db.close()
    assert Ledger(path).recent(1) == []                    # and the next open upgrades it


# ---- embeddings ------------------------------------------------------------------------------------------------

def vectors(*rows, order=None):
    items = [{"object": "embedding", "index": i, "embedding": list(v)} for i, v in enumerate(rows)]
    if order:
        items = [items[i] for i in order]
    return {"body": {"object": "list", "data": items, "usage": {"prompt_tokens": 7, "total_tokens": 7}}}


def test_embed_returns_one_vector_per_text_in_the_order_asked(gateway, fake):
    fake.replies.append(vectors([1, 0], [0, 1], [0.5, 0.5], order=[2, 0, 1]))
    out = gateway.embed(["a", "b", "c"], purpose="memory_embed")
    assert out == [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
    assert fake.paths[-1] == ("POST", "/v1/embeddings")
    assert fake.requests[-1] == {"model": "fake-model", "input": ["a", "b", "c"], "encoding_format": "float"}
    row = gateway.ledger.recent(1)[0]
    assert row["purpose"] == "memory_embed" and row["status"] == "ok" and row["prompt_tokens"] == 7
    assert row["provider"] == "chat" and row["error"] is None     # this fixture's gateway is the default provider


def test_the_ledger_row_of_a_memory_call_holds_no_text(gateway, fake):
    fake.replies.append(vectors([1.0]))
    gateway.embed(["a private statement about Tolvane"], purpose="memory_embed")
    assert "Tolvane" not in json.dumps(gateway.ledger.recent(1)[0])


def test_embed_sends_input_type_only_when_asked(gateway, fake):
    fake.replies += [vectors([1.0]), vectors([1.0])]
    gateway.embed(["a"], purpose="x")
    gateway.embed(["a"], purpose="x", input_type="query")
    assert "input_type" not in fake.requests[0] and fake.requests[1]["input_type"] == "query"


@pytest.mark.parametrize("body", [
    {"data": [{"index": 0, "embedding": [1.0]}]},                                   # one item for two texts
    {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}]},  # the same index twice
    {"data": [{"index": 0, "embedding": [1.0]}, {"index": 5, "embedding": [1.0]}]},  # an index out of range
    {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}]},   # two lengths
    {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": ["x"]}]},
    {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": []}]},
    {"data": "none"}, ["not", "an", "object"]])
def test_embed_refuses_a_malformed_answer(gateway, fake, body):
    fake.replies.append({"body": body})
    with pytest.raises(InferenceError) as err:
        gateway.embed(["a", "b"], purpose="x")
    assert err.value.code == "bad_response" and gateway.ledger.recent(1)[0]["status"] == "bad_response"


def test_embed_refuses_a_vector_that_holds_infinity(gateway, fake):
    """Python's json writes and reads Infinity; a vector holding it must never become a similarity score."""
    fake.replies.append({"body": {"data": [{"index": 0, "embedding": [float("inf")]}]}})
    with pytest.raises(InferenceError) as err:
        gateway.embed(["a"], purpose="x")
    assert err.value.code == "bad_response"


def test_embed_and_rerank_refuse_empty_input_without_calling(gateway, fake):
    for call in (lambda: gateway.embed([], purpose="x"), lambda: gateway.embed(["a", ""], purpose="x"),
                 lambda: gateway.rerank("", ["a"], purpose="x"), lambda: gateway.rerank("q", [], purpose="x")):
        with pytest.raises(ValueError):
            call()
    assert fake.requests == []


# ---- rerank ----------------------------------------------------------------------------------------------------

def test_rerank_returns_the_scores_in_document_order(gateway, fake):
    fake.replies.append({"body": {"results": [{"index": 1, "relevance_score": 0.9},
                                              {"index": 0, "relevance_score": 0.1}]}})
    assert gateway.rerank("how tall?", ["built in 1874", "186 metres tall"], purpose="memory_rerank") == [0.1, 0.9]
    assert fake.paths[-1] == ("POST", "/v1/rerank")
    assert fake.requests[-1] == {"model": "fake-model", "query": "how tall?",
                                 "documents": ["built in 1874", "186 metres tall"]}
    assert gateway.ledger.recent(1)[0]["purpose"] == "memory_rerank"


@pytest.mark.parametrize("body", [
    {"results": [{"index": 0, "relevance_score": 0.5}]},
    {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.5}]},
    {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 1, "relevance_score": "high"}]},
    {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 1}]},
    {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 1, "relevance_score": True}]}, {}])
def test_rerank_refuses_a_malformed_answer(gateway, fake, body):
    fake.replies.append({"body": body})
    with pytest.raises(InferenceError) as err:
        gateway.rerank("q", ["a", "b"], purpose="x")
    assert err.value.code == "bad_response"


# ---- local-first: a memory statement is private ----------------------------------------------------------------

def test_memory_text_is_never_sent_off_this_host_without_the_private_memory_authorisation():
    """embed and rerank carry the owner's memory. An endpoint off this host needs remote_allowed AND
    remote_private_memory, exactly as a chat message marked private does; nothing is sent before the refusal."""
    cfg = iconfig.InferenceConfig(base_url="https://api.example.com/v1", model="m")
    gw = Gateway(cfg, key="k", resolver=remote_resolver)
    with pytest.raises(InferenceError) as err:
        gw.embed(["the owner's fact"], purpose="x")
    assert err.value.code == "remote_not_authorised"
    gw = Gateway(iconfig.InferenceConfig(base_url="https://api.example.com/v1", model="m", remote_allowed=True),
                 key="k", resolver=remote_resolver)
    for call in (lambda: gw.embed(["the owner's fact"], purpose="x"),
                 lambda: gw.rerank("a question", ["the owner's fact"], purpose="x")):
        with pytest.raises(InferenceError) as err:
            call()
        assert err.value.code == "private_blocked"


def test_a_memory_model_error_is_recorded_and_raised(gateway, fake):
    fake.replies.append({"status": 400, "body": {"error": {"message": "bad input"}}})
    with pytest.raises(InferenceError) as err:
        gateway.embed(["a"], purpose="memory_embed")
    assert err.value.code == "http_error"
    row = gateway.ledger.recent(1)[0]
    assert row["status"] == "http_error" and row["purpose"] == "memory_embed"



# ---- whose process holds the loopback port (review L2; S1: only the socket that would accept the connection, only
# ---- on this deployment's own service ports) ---------------------------------------------------------------------

@pytest.fixture
def managed(gateway, fake, monkeypatch):
    """The fake server's port is the answer model's own (deploy/vllm), as resources.idle sees it."""
    from neuroscifi.resources import idle
    port = urlsplit(fake.url).port
    monkeypatch.setattr(idle, "managed_service", lambda p, services=None: "vllm" if int(p) == port else None)
    return port


def test_nothing_is_sent_to_a_service_port_held_by_another_accounts_process(gateway, fake, managed):
    """A loopback port belongs to whoever binds it first; while the model's container is down that can be another
    account. Such a listener gets neither the key nor the request."""
    import os
    stranger = os.getuid() + 4242
    gateway.listeners = lambda port: [("127.0.0.1", stranger)]
    with pytest.raises(InferenceError) as err:
        gateway.chat(MESSAGES, purpose="x")
    text = str(err.value)
    assert err.value.code == "unavailable" and "another account" in text and f"uid {stranger}" in text
    assert f"127.0.0.1:{managed}" in text and "NSF_LISTENER_CHECK=0" in text     # where, and the way out
    assert fake.requests == [] and fake.paths == []          # not even a connection's first line
    assert gateway.ledger.recent(1)[0]["status"] != "ok"
    gateway.listeners = lambda port: [("0.0.0.0", stranger)]                    # a wildcard socket would take it too
    assert "another account" in gateway.stranger()
    gateway.listeners = lambda port: [("::", stranger)]                         # and a dual-stack one
    assert "another account" in gateway.stranger()


def test_a_listener_on_another_address_of_this_computer_is_not_a_reason_to_refuse(gateway, fake, managed):
    """Review S1: only the port was compared, so any account could turn the model off by binding the model's port
    on another address (the kernel allows that while the service holds 127.0.0.1), and a resolver on 127.0.0.53:53
    made 127.0.0.1:53 look taken."""
    import os
    stranger = os.getuid() + 4242
    for where in ("127.0.0.53", "127.0.0.2", "172.17.0.1", "192.168.1.20", "::1", "fe80::1"):
        gateway.listeners = lambda port, where=where: [(where, stranger)]
        assert gateway.stranger() is None, where
        gateway.chat(MESSAGES, purpose="x")
    # the service on the exact address takes the connection before a stranger's wildcard socket
    gateway.listeners = lambda port: [("127.0.0.1", 0), ("0.0.0.0", stranger), ("::", stranger)]
    assert gateway.stranger() is None
    gateway.listeners = lambda port: [("0.0.0.0", os.getuid()), ("::", stranger)]      # IPv4 wildcard before IPv6's
    assert gateway.stranger() is None
    assert len(fake.requests) == 6


def test_the_services_own_listeners_are_trusted(gateway, fake, key_file, managed):
    """Root (Docker's port proxy), this user and the owner of the key file are the service."""
    import os
    for owners in ([0], [os.getuid()], [0, os.getuid()], [os.stat(key_file).st_uid], [], None):
        table = None if owners is None else [("127.0.0.1", uid) for uid in owners]
        gateway.listeners = lambda port, table=table: table
        assert gateway.stranger() is None
        gateway.chat(MESSAGES, purpose="x")
    assert len(fake.requests) == 6


def test_an_account_that_cannot_be_identified_is_neither_trusted_nor_refused(gateway, fake, managed, monkeypatch):
    """Review S1 (c): under PrivateUsers=yes every other account reads as uid 65534, in /proc and in os.stat alike.
    It must not be refused (a model server under its own account would never be reachable), and a key file that
    reads as owned by 65534 must not make every stranger trusted."""
    import os
    gateway.listeners = lambda port: [("127.0.0.1", 65534)]
    assert gateway.stranger() is None
    gateway.chat(MESSAGES, purpose="x")
    real_stat = os.stat

    class Unmapped:
        st_uid = 65534
    monkeypatch.setattr(os, "stat", lambda path, *a, **k: Unmapped() if str(path).endswith("api_key")
                        else real_stat(path, *a, **k))
    gateway.listeners = lambda port: [("127.0.0.1", 65534), ("127.0.0.1", os.getuid() + 4242)]
    assert f"uid {os.getuid() + 4242}" in gateway.stranger()          # the identifiable stranger is still refused
    assert "65534" not in gateway.stranger()


def test_a_server_the_owner_runs_on_its_own_port_and_account_is_used_as_before(gateway, fake):
    """Review S1 (b): Ollama, a unit with its own User=, rootless Docker. Not one of this deployment's service
    ports, so nobody is asked who listens there."""
    import os
    gateway.listeners = lambda port: pytest.fail("an endpoint that is not our own service is not inspected")
    assert gateway.stranger() is None
    gateway.chat(MESSAGES, purpose="x")
    assert len(fake.requests) == 1
    assert os.getuid() >= 0


def test_the_owner_can_switch_the_listener_check_off(gateway, fake, managed, monkeypatch):
    import os
    gateway.listeners = lambda port: [("127.0.0.1", os.getuid() + 4242)]
    assert gateway.stranger() is not None
    monkeypatch.setenv("NSF_LISTENER_CHECK", "0")          # that account runs the service on purpose
    assert gateway.stranger() is None
    gateway.chat(MESSAGES, purpose="x")
    assert len(fake.requests) == 1


def test_the_listener_check_reads_the_endpoints_own_port_and_never_fails_a_call(gateway, fake, managed):
    asked = []
    gateway.listeners = lambda port: asked.append(port) or []
    gateway.chat(MESSAGES, purpose="x")
    assert asked == [managed]

    def broken(port):
        raise OSError("no /proc here")
    for table in (broken, lambda port: [("not an address", 5)], lambda port: [("127.0.0.1",)], lambda port: "x"):
        gateway.listeners = table
        gateway.chat(MESSAGES, purpose="x")                  # the check must never be why a call fails
    assert len(fake.requests) == 5


def test_an_endpoint_off_this_host_is_not_asked_about_its_listener():
    cfg = iconfig.InferenceConfig(base_url="https://models.example/v1", model="m", remote_allowed=True)
    gw = Gateway(cfg, resolver=remote_resolver)
    gw.listeners = lambda port: pytest.fail("a remote port is not this computer's to inspect")
    assert gw.stranger() is None


def test_the_real_socket_table_names_this_process_as_the_fake_servers_owner(fake, gateway, managed):
    """No injected table: /proc/net/tcp as the kernel prints it, for a socket this process really listens on."""
    import os
    from neuroscifi.resources import monitor
    entries = monitor.listeners(managed)
    assert entries is None or ("127.0.0.1", os.getuid()) in entries
    assert gateway.stranger() is None
    gateway.chat(MESSAGES, purpose="x")
    assert len(fake.requests) == 1


def test_a_refused_connection_to_a_service_port_leaves_a_wake_request_without_any_policy_state(
        key_file, tmp_path, monkeypatch):
    """Review S2: the console and the worker may be unable to read the idle policy's files (another account's), so
    they cannot know a service was unloaded. A refused connection is the signal; the request file is all they
    write."""
    from neuroscifi.resources import idle
    with socket.socket() as probe:                       # a loopback port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    wake = tmp_path / "wake"
    wake.mkdir()
    monkeypatch.setenv("NSF_WAKE_DIR", str(wake))
    monkeypatch.setattr(idle, "managed_service", lambda p, services=None: "vllm" if int(p) == port else None)
    monkeypatch.setattr(idle, "_ASKED", {})
    cfg = iconfig.InferenceConfig(base_url=f"http://127.0.0.1:{port}/v1", model="m", key_file=str(key_file),
                                  max_retries=0, backoff=0.01)
    gw = Gateway(cfg, key="test-key")                    # no db_path: nothing of the policy is read or written
    with pytest.raises(InferenceError) as err:
        gw.chat(MESSAGES, purpose="x")
    assert err.value.code == "unavailable" and "cannot connect" in str(err.value) and "wake request" in str(err.value)
    assert [p.name for p in wake.iterdir()] == ["vllm.wake"] and (wake / "vllm.wake").read_bytes() == b""
    (wake / "vllm.wake").unlink()                        # served; a second failure moments later does not ask again
    with pytest.raises(InferenceError) as err:
        gw.chat(MESSAGES, purpose="x")
    assert "asked for a moment ago" in str(err.value) and list(wake.iterdir()) == []
    monkeypatch.setattr(idle, "managed_service", lambda p, services=None: None)       # not our service: nothing asked
    monkeypatch.setattr(idle, "_ASKED", {})
    with pytest.raises(InferenceError) as err:
        gw.chat(MESSAGES, purpose="x")
    assert "wake request" not in str(err.value) and list(wake.iterdir()) == []
    monkeypatch.delenv("NSF_WAKE_DIR")                   # one account runs everything: nothing to ask either
    monkeypatch.setattr(idle, "managed_service", lambda p, services=None: "vllm")
    with pytest.raises(InferenceError) as err:
        gw.chat(MESSAGES, purpose="x")
    assert "wake request" not in str(err.value)
