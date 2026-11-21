"""Inference gateway over a fake OpenAI-compatible server: health states, discovery, streaming, retries, timeouts,
structured output repair, context budget, local-first policy and token accounting."""
import socket

import pytest

from fake_openai import make_gateway
from neuroscifi.inference import config as iconfig
from neuroscifi.inference.gateway import Gateway, InferenceError, parse_json, strip_thinking
from neuroscifi.inference.ledger import Ledger
from neuroscifi.instance import store
from neuroscifi.instance.errors import ValidationError

T0 = 1_795_200_000.0
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"],
          "properties": {"answer": {"type": "string"}}}


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def remote_resolver(host, *args):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


# ---- health states --------------------------------------------------------------------------------------------

def test_health_not_configured():
    assert Gateway(iconfig.InferenceConfig()).health()["state"] == "not_configured"


def test_health_ready_lists_models_and_sends_the_key(gateway, fake):
    h = gateway.health()
    assert h["state"] == "ready" and h["locality"] == "loopback" and h["model"] == "fake-model"
    assert [m["id"] for m in h["models"]] == ["fake-model"]
    assert ("GET", "/v1/models") in fake.paths


def test_health_probe_generates(gateway, fake):
    fake.replies.append({"content": "ready"})
    h = gateway.health(probe=True)
    assert h["state"] == "ready" and h["probe"]["reply"] == "ready"


def test_health_unauthorised_with_a_wrong_key(fake, tmp_path):
    bad = tmp_path / "bad"
    bad.write_text("wrong")
    bad.chmod(0o600)
    gw = make_gateway(fake, bad, tmp_path)
    assert gw.health()["state"] == "unauthorised"


def test_health_unavailable_when_nothing_listens(tmp_path, key_file):
    cfg = iconfig.InferenceConfig(base_url=f"http://127.0.0.1:{closed_port()}/v1", key_file=str(key_file))
    h = Gateway(cfg, key="k", sleep=lambda s: None).health()
    assert h["state"] == "unavailable" and "connect" in h["reason"]


def test_health_degraded_when_the_configured_model_is_not_served(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path)
    gw.cfg.model = "other-model"
    h = gw.health()
    assert h["state"] == "degraded" and "other-model" in h["reason"]


def test_health_degraded_when_the_probe_fails(gateway, fake):
    fake.replies.append({"status": 400, "body": {"error": {"message": "bad request"}}})
    h = gateway.health(probe=True)
    assert h["state"] == "degraded" and "probe" in h["reason"]


# ---- chat, streaming, accounting -----------------------------------------------------------------------------

def test_chat_records_tokens_and_latency(gateway, fake, tmp_path):
    fake.replies.append({"content": "<think>hidden</think>The answer is four."})
    result = gateway.chat([{"role": "user", "content": "2+2?"}], purpose="test")
    assert result.text == "The answer is four." and result.prompt_tokens == 11
    row = Ledger(tmp_path / "x.inference.sqlite").recent(1)[0]
    assert row["status"] == "ok" and row["purpose"] == "test" and row["completion_tokens"] == result.completion_tokens
    assert row["latency_ms"] is not None and "text" not in row


def test_streaming_delivers_deltas_and_first_token_latency(gateway, fake, tmp_path):
    fake.replies.append({"content": "Streaming works in small pieces."})
    seen = []
    result = gateway.chat([{"role": "user", "content": "stream"}], purpose="s", stream=True, on_delta=seen.append)
    assert len(seen) > 2 and "".join(seen) == result.text == "Streaming works in small pieces."
    assert result.first_token_ms is not None and result.completion_tokens == 5 and result.stream
    assert fake.requests[-1]["stream"] is True and fake.requests[-1]["stream_options"] == {"include_usage": True}
    assert Ledger(tmp_path / "x.inference.sqlite").recent(1)[0]["stream"] == 1


def test_a_running_stream_is_visible_to_presence_with_its_token_count(gateway, fake):
    """The ledger row exists only after the call; while it streams, presence.activity lists it (no text)."""
    from neuroscifi.presence import activity
    fake.replies.append({"content": "Streaming works in small pieces."})
    seen = []
    gateway.chat([{"role": "user", "content": "stream"}], purpose="web_answer", stream=True,
                 on_delta=lambda text: seen.append([e for e in activity.snapshot() if e["purpose"] == "web_answer"]))
    assert seen and all(len(entries) == 1 for entries in seen)
    assert [entries[0]["tokens"] for entries in seen] == list(range(1, len(seen) + 1))
    assert seen[0][0]["activity"] == "generating" and seen[0][0]["subsystem"] == "reasoning"
    assert not [e for e in activity.snapshot() if e["purpose"] == "web_answer"]      # gone once the call ends
    fake.replies.append({"content": "Not streamed."})
    gateway.chat([{"role": "user", "content": "x"}], purpose="web_answer")
    assert not [e for e in activity.snapshot() if e["purpose"] == "web_answer"]


def test_streaming_tool_call_fragments_are_assembled(gateway, fake):
    fake.replies.append({"tool_calls": [{"name": "knowledge_query", "arguments": '{"topic": "rivers"}'}]})
    result = gateway.chat([{"role": "user", "content": "x"}], purpose="s", stream=True,
                          tools=[{"type": "function", "function": {"name": "knowledge_query", "parameters": {}}}])
    assert result.tool_calls[0]["function"] == {"name": "knowledge_query", "arguments": '{"topic": "rivers"}'}


def test_retries_503_with_backoff_then_succeeds(gateway, fake):
    fake.replies += [{"status": 503}, {"status": 502}, {"content": "fine"}]
    result = gateway.chat([{"role": "user", "content": "x"}], purpose="r")
    assert result.text == "fine" and result.attempts == 3 and gateway.sleeps == [0.01, 0.02]


def test_retry_after_is_honoured_and_bounded(gateway, fake):
    fake.replies += [{"status": 429, "headers": {"Retry-After": "2"}}, {"content": "ok"}]
    gateway.chat([{"role": "user", "content": "x"}], purpose="r")
    assert gateway.sleeps == [2.0]


def test_retries_are_bounded(gateway, fake, tmp_path):
    fake.replies += [{"status": 503}] * 5
    with pytest.raises(InferenceError) as err:
        gateway.chat([{"role": "user", "content": "x"}], purpose="r")
    assert err.value.code == "unavailable" and len(fake.requests) == 3   # first try + max_retries 2
    assert Ledger(tmp_path / "x.inference.sqlite").recent(1)[0]["status"] == "unavailable"


def test_client_errors_are_not_retried(gateway, fake):
    fake.replies.append({"status": 400, "body": {"error": {"message": "nope"}}})
    with pytest.raises(InferenceError) as err:
        gateway.chat([{"role": "user", "content": "x"}], purpose="r")
    assert err.value.code == "http_error" and len(fake.requests) == 1


def test_timeout_is_not_retried(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, read_timeout=1.0)
    fake.replies.append({"delay": 2.0, "content": "late"})
    with pytest.raises(InferenceError) as err:
        gw.chat([{"role": "user", "content": "x"}], purpose="t")
    assert err.value.code == "timeout" and len(fake.requests) == 1


def test_stream_timeout(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, read_timeout=1.0)
    fake.replies.append({"delay": 2.0, "content": "late"})
    with pytest.raises(InferenceError) as err:
        gw.chat([{"role": "user", "content": "x"}], purpose="t", stream=True)
    assert err.value.code == "timeout"


def test_context_budget_refuses_before_sending(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, max_context_tokens=600, max_output_tokens=100)
    with pytest.raises(InferenceError) as err:
        gw.chat([{"role": "user", "content": "word " * 2000}], purpose="big")
    assert err.value.code == "context_overflow" and fake.requests == []


def test_server_context_error_is_reported_as_overflow(gateway, fake):
    fake.replies.append({"status": 400, "body": {"error": {"message": "This model's maximum context length is 32768"}}})
    with pytest.raises(InferenceError) as err:
        gateway.chat([{"role": "user", "content": "x"}], purpose="r")
    assert err.value.code == "context_overflow"


def test_thinking_switch_reaches_the_chat_template(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, thinking="off")
    gw.chat([{"role": "user", "content": "x"}], purpose="r")
    assert fake.requests[-1]["chat_template_kwargs"] == {"enable_thinking": False}


# ---- structured output --------------------------------------------------------------------------------------

def test_structured_output_valid_first_time(gateway, fake):
    fake.replies.append({"content": '```json\n{"answer": "yes"}\n```'})
    value, results = gateway.structured([{"role": "user", "content": "q"}], SCHEMA, name="t", purpose="s")
    assert value == {"answer": "yes"} and len(results) == 1
    assert fake.requests[-1]["response_format"]["json_schema"]["schema"] == SCHEMA


def test_structured_output_one_repair(gateway, fake):
    fake.replies += [{"content": '{"answer": 3, "extra": 1}'}, {"content": '{"answer": "three"}'}]
    value, results = gateway.structured([{"role": "user", "content": "q"}], SCHEMA, name="t", purpose="s")
    assert value == {"answer": "three"} and len(results) == 2
    repair = fake.requests[-1]["messages"][-1]["content"]
    assert "does not match" in repair and "$.answer" in repair


def test_structured_output_gives_up_after_one_repair(gateway, fake):
    fake.replies += [{"content": "not json"}, {"content": '{"nope": 1}'}, {"content": '{"answer": "late"}'}]
    with pytest.raises(InferenceError) as err:
        gateway.structured([{"role": "user", "content": "q"}], SCHEMA, name="t", purpose="s")
    assert err.value.code == "schema_invalid" and len(fake.requests) == 2


def test_structured_timeout_is_one_deadline_for_the_answer_and_its_repair(gateway, fake):
    """Lane G #4: the repair call got a fresh `timeout`, so a task deadline could be overrun by up to twice it."""
    import time
    fake.replies += [{"content": "not json", "delay": 0.7}, {"content": '{"answer": "late"}', "delay": 2.0}]
    started = time.monotonic()
    with pytest.raises(InferenceError) as err:
        gateway.structured([{"role": "user", "content": "q"}], SCHEMA, name="t", purpose="s", timeout=1.0)
    assert err.value.code == "timeout" and time.monotonic() - started < 1.5


def test_parse_json_and_thinking():
    assert parse_json('Sure: {"a": 1} done') == {"a": 1}
    assert strip_thinking("<think>x\ny</think>\nanswer") == "answer"


@pytest.mark.parametrize("text", ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', 'x {"a": [1, NaN]} y'])
def test_parse_json_rejects_non_finite_numbers(text):
    with pytest.raises(ValueError):
        parse_json(text)


def test_structured_output_with_nan_is_invalid(gateway, fake):
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "number", "minimum": 0}}}
    fake.replies += [{"content": '{"a": NaN}'}, {"content": '{"a": Infinity}'}]
    with pytest.raises(InferenceError) as err:
        gateway.structured([{"role": "user", "content": "q"}], schema, name="t", purpose="s")
    assert err.value.code == "schema_invalid"


def test_jsonschema_number_must_be_finite():
    from neuroscifi.inference.jsonschema import validate
    assert validate(float("nan"), {"type": "number"}) and validate(float("inf"), {"type": "number"})
    assert validate(float("nan"), {"minimum": 0, "maximum": 1})


# ---- local-first policy ----------------------------------------------------------------------------------------

def test_locality():
    assert iconfig.locality("http://127.0.0.1:8811/v1")[0] == "loopback"
    assert iconfig.locality("http://[::1]:8811/v1")[0] == "loopback"
    assert iconfig.locality("http://192.168.1.20:8000/v1")[0] == "private"
    assert iconfig.locality("http://100.101.1.2:8000/v1")[0] == "private"   # CGNAT / tailnet
    assert iconfig.locality("https://api.example.com/v1", resolver=remote_resolver)[0] == "remote"


def remote_gateway(**changes):
    cfg = iconfig.InferenceConfig(base_url="https://api.example.com/v1", model="m", **changes)
    return Gateway(cfg, key="k", resolver=remote_resolver)


def test_remote_endpoint_refused_without_owner_authorisation():
    gw = remote_gateway()
    assert gw.health()["state"] == "not_configured"
    with pytest.raises(InferenceError) as err:
        gw.chat([{"role": "user", "content": "hello"}], purpose="x")
    assert err.value.code == "remote_not_authorised"


def test_remote_endpoint_must_use_https():
    cfg = iconfig.InferenceConfig(base_url="http://api.example.com/v1", model="m", remote_allowed=True)
    with pytest.raises(InferenceError) as err:
        Gateway(cfg, resolver=remote_resolver).guard()
    assert err.value.code == "remote_not_authorised"


def test_remote_privacy_screen_and_private_memory():
    gw = remote_gateway(remote_allowed=True)
    with pytest.raises(InferenceError) as err:
        gw.screen([{"role": "user", "content": "mail me at owner@example.org"}])
    assert err.value.code == "private_blocked"
    with pytest.raises(InferenceError) as err:
        gw.screen([{"role": "user", "content": "what I remember", "private": True}])
    assert err.value.code == "private_blocked"
    gw.screen([{"role": "user", "content": "mail me at owner@example.org"}], authorization="owner-approved-disclosure")
    gw.cfg.remote_private_memory = True
    gw.screen([{"role": "user", "content": "what I remember", "private": True}])


def test_local_endpoint_is_not_privacy_screened(gateway, fake):
    gateway.chat([{"role": "user", "content": "mail me at owner@example.org", "private": True}], purpose="x")
    assert "private" not in fake.requests[-1]["messages"][0]


# ---- configuration ----------------------------------------------------------------------------------------------

def test_config_validation_and_key_file(tmp_path):
    db = store.create(tmp_path / "i.sqlite", T0)
    try:
        assert not iconfig.get_config(db).configured
        with pytest.raises(ValidationError):
            iconfig.set_config(db, {"base_url": "ftp://x"}, T0)
        with pytest.raises(ValidationError):
            iconfig.set_config(db, {"api_key": "secret"}, T0)
        with pytest.raises(ValidationError):
            iconfig.set_config(db, {"key_file": "relative/key"}, T0)
        cfg = iconfig.set_config(db, {"base_url": "http://127.0.0.1:8811/v1/", "max_retries": 1}, T0)
        assert cfg.base_url == "http://127.0.0.1:8811/v1" and cfg.max_retries == 1
        assert "secret" not in str(db.execute("SELECT value FROM meta WHERE key='inference'").fetchone())
    finally:
        db.close()
    loose = tmp_path / "loose"
    loose.write_text("k")
    loose.chmod(0o644)
    with pytest.raises(ValidationError):
        iconfig.read_key(iconfig.InferenceConfig(base_url="http://127.0.0.1:1/v1", key_file=str(loose)))
