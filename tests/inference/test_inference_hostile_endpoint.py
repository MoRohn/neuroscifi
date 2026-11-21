"""A hostile or broken model endpoint (UAT T5-M01..M04): a byte-dripping server cannot stretch the whole-call
deadline, malformed answer shapes are 'bad_response' (never an uncaught exception), a stream cut off before it
finished is not a success, and an endpoint that ignores max_tokens cannot hand back more than the cap."""
import json
import time

import pytest

from fake_openai import make_gateway
from neuroscifi.inference.gateway import InferenceError
from neuroscifi.presence import activity

MSG = [{"role": "user", "content": "x"}]


@pytest.mark.parametrize("stream", [False, True])
def test_a_byte_drip_cannot_stretch_the_deadline(fake, key_file, tmp_path, stream):
    """T5-M02: every single read arrived within the socket timeout, so a 1 s call ran for minutes."""
    gw = make_gateway(fake, key_file, tmp_path, read_timeout=1.0)
    fake.replies.append({"content": "a long dripped answer " * 4, "drip": 0.05})
    started = time.monotonic()
    with pytest.raises(InferenceError) as err:
        gw.chat(MSG, purpose="drip", stream=stream)
    assert err.value.code == "timeout" and time.monotonic() - started < 2.0


def test_dripped_headers_cannot_stretch_the_deadline(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, read_timeout=1.0)
    fake.replies.append({"content": "x", "drip": 0.1, "drip_headers": True})
    started = time.monotonic()
    with pytest.raises(InferenceError) as err:
        gw.chat(MSG, purpose="drip")
    assert err.value.code == "timeout" and time.monotonic() - started < 2.0


def test_presence_leaves_generating_when_a_dripping_stream_times_out(fake, key_file, tmp_path):
    gw = make_gateway(fake, key_file, tmp_path, read_timeout=1.0)
    fake.replies.append({"content": "dripping " * 10, "drip": 0.05})
    started = time.monotonic()
    with pytest.raises(InferenceError):
        gw.chat(MSG, purpose="drip_presence", stream=True)
    assert time.monotonic() - started < 2.0
    assert not [e for e in activity.snapshot() if e["purpose"] == "drip_presence"]


@pytest.mark.parametrize("body", [
    {"choices": [{"message": "hi"}]},
    {"choices": [{"message": {"content": {"x": 1}}}]},
    {"choices": [{"message": {"content": 42}}]},
    {"choices": [{"message": {"content": None, "tool_calls": 5}}]},
    {"choices": [{"message": {"content": None, "tool_calls": ["x"]}}]},
    {"choices": "nope"},
    [1, 2],
])
def test_malformed_answer_shapes_are_bad_response(gateway, fake, body):
    """T5-M01: these raised AttributeError/TypeError (CLI traceback, console 500)."""
    fake.replies.append({"body": body})
    with pytest.raises(InferenceError) as err:
        gateway.chat(MSG, purpose="shape")
    assert err.value.code == "bad_response"


def test_odd_usage_and_model_fields_are_ignored_not_fatal(gateway, fake):
    fake.replies.append({"body": {"model": 7, "usage": [1], "choices": [{"message": {"content": "hi"},
                                                                         "finish_reason": {"x": 1}}]}})
    result = gateway.chat(MSG, purpose="shape")
    assert result.text == "hi" and result.prompt_tokens is None and result.finish_reason is None
    assert result.model == "fake-model"


def sse(*objs):
    return [b"data: " + (o if isinstance(o, bytes) else json.dumps(o).encode()) + b"\n\n" for o in objs]


@pytest.mark.parametrize("lines", [
    sse(b"[1,2]", b"[DONE]"),
    sse({"choices": ["x"]}, b"[DONE]"),
    sse({"choices": [{"delta": "x"}]}, b"[DONE]"),
    sse({"choices": [{"delta": {"content": 5}}]}, b"[DONE]"),
    sse({"choices": [{"delta": {"tool_calls": [{"index": "a", "function": {"name": "x"}}]}}]}, b"[DONE]"),
    sse({"choices": {"x": 1}}, b"[DONE]"),
])
def test_malformed_stream_shapes_are_bad_response(gateway, fake, lines):
    fake.replies.append({"sse": lines})
    with pytest.raises(InferenceError) as err:
        gateway.chat(MSG, purpose="shape", stream=True)
    assert err.value.code == "bad_response"


def test_a_truncated_stream_is_not_a_success(gateway, fake):
    """T5-M04: the connection closed after one delta (no finish_reason, no [DONE]) and the fragment was returned."""
    fake.replies.append({"sse": sse({"choices": [{"delta": {"content": "partial ans"}}]})})
    with pytest.raises(InferenceError) as err:
        gateway.chat(MSG, purpose="trunc", stream=True)
    assert err.value.code == "bad_response" and "ended before" in str(err.value)


@pytest.mark.parametrize("stream", [False, True])
def test_an_endpoint_that_ignores_max_tokens_is_capped(fake, key_file, tmp_path, stream):
    """T5-M04: max_output_tokens was sent but a server ignoring it got 6 MiB printed."""
    gw = make_gateway(fake, key_file, tmp_path, max_output_tokens=16)
    fake.replies.append({"content": "B" * 5000})
    result = gw.chat(MSG, purpose="cap", stream=stream)
    assert len(result.text) <= 16 * 6 and result.finish_reason == "length"
