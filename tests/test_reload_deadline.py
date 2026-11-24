"""A call that waited for an idle reload ran with a second full time allowance (open-items round, lane D): the
reload and the request now share one."""
from contextlib import contextmanager
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


# ---- one allowance for a call and the reload it waits for ----------------------------------------------------------

def test_a_reload_comes_off_the_calls_own_time(tmp_path, monkeypatch):
    """A call that waited for an idle reload then ran with a second full allowance: up to twice its timeout."""
    sys.path.insert(0, str(ROOT / "tests" / "inference"))
    from fake_openai import FakeOpenAI, make_gateway
    from neuroscifi.inference.gateway import InferenceError
    server = FakeOpenAI().start()
    try:
        key = tmp_path / "api_key"
        key.write_text("test-key\n")
        key.chmod(0o600)
        gw = make_gateway(server, key, tmp_path)
        gw.db_path = tmp_path / "x.sqlite"
        now = [1000.0]
        gw.clock = lambda: now[0]

        @contextmanager
        def reload_takes(seconds, result=("load",)):
            def loaded(purpose, timeout=None):
                @contextmanager
                def wait():
                    first = not waited
                    if first:
                        waited.append(timeout)
                        now[0] += seconds
                    yield {"action": result[0]} if first and result[0] else None
                return wait()
            waited = []
            monkeypatch.setattr(gw, "_loaded", loaded)
            yield waited
        seen = []
        real = gw._call

        def call(method, path, payload=None, *, timeout=None, retries=None):
            seen.append(timeout)
            return real(method, path, payload, timeout=timeout, retries=retries)
        monkeypatch.setattr(gw, "_call", call)
        gw._model = "fake-model"
        server.replies.append({"content": "ready"})
        with reload_takes(41.0) as waited:
            assert gw.chat([{"role": "user", "content": "hello"}], purpose="t", timeout=60).text == "ready"
        assert waited == [60] and seen == [19.0]              # 60 s in all, not 60 for the reload and 60 again
        with reload_takes(60.0):
            with pytest.raises(InferenceError) as err:
                gw.chat([{"role": "user", "content": "hello"}], purpose="t", timeout=60)
        assert err.value.code == "timeout" and "took all of the call's 60 s" in str(err.value) and len(seen) == 1
        assert gw.ledger.recent(1)[0]["status"] == "timeout"
        with reload_takes(60.0, result=(None,)):                 # time went, but no reload: none is claimed
            with pytest.raises(InferenceError) as err:
                gw.chat([{"role": "user", "content": "hello"}], purpose="t", timeout=60)
        assert err.value.code == "timeout" and "started again" not in str(err.value)
        assert "nothing of the call's 60 s was left" in str(err.value)
    finally:
        server.stop()


def test_a_call_without_a_reload_keeps_its_whole_allowance(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tests" / "inference"))
    from fake_openai import FakeOpenAI, make_gateway
    server = FakeOpenAI().start()
    try:
        key = tmp_path / "api_key"
        key.write_text("test-key\n")
        key.chmod(0o600)
        gw = make_gateway(server, key, tmp_path)
        gw.clock = lambda: 1000.0                              # no time passes before the request
        seen = []
        real = gw._call

        def call(method, path, payload=None, *, timeout=None, retries=None):
            seen.append(timeout)
            gw.clock = __import__("time").monotonic            # the exchange itself runs on the real clock
            return real(method, path, payload, timeout=timeout, retries=retries)
        monkeypatch.setattr(gw, "_call", call)
        gw._model = "fake-model"
        for timeout, expected in ((60, 60.0), (None, gw.cfg.read_timeout), (10 ** 6, gw.cfg.read_timeout)):
            gw.clock = lambda: 1000.0
            server.replies.append({"content": "ok"})
            assert gw.chat([{"role": "user", "content": "x"}], purpose="t", timeout=timeout).text == "ok"
            assert seen[-1] == expected
    finally:
        server.stop()
