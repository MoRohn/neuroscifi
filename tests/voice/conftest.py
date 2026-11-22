"""Voice Cortex test fixtures: a temporary individual, a hub with scripted speech engines (no services needed)."""
from contextlib import closing
import struct
import time

import pytest

from neuroscifi.instance import store
from neuroscifi.voice import state as vstate
from neuroscifi.voice.bridge import Principal
from neuroscifi.voice.hub import VoiceHub
from neuroscifi.webcortex import state as wstate


def tiny_wav(seconds=0.2, rate=24000):
    pcm = b"\x00\x01" * int(seconds * rate)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate,
                                                                                   rate * 2, 2, 16) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


class FakeASR:
    def __init__(self):
        self.script, self.calls, self.fail = [], 0, None

    def transcribe(self, pcm, rate=16000):
        from neuroscifi.voice.engines import EngineError
        self.calls += 1
        if self.fail:
            raise EngineError(self.fail, "scripted failure")
        text = self.script[0] if self.script else "hello there friend"
        return {"text": text, "latency": 0.01, "audio_seconds": len(pcm) / 2 / rate}


class FakeTTS:
    def __init__(self):
        self.spoken = []

    def synthesize(self, text, voice, speed=1.0):
        self.spoken.append(text)
        return {"wav": tiny_wav(), "latency": 0.005, "audio_seconds": 0.2, "voice": voice}


class FakeEngines:
    instance = None

    def __init__(self, cfg):
        if FakeEngines.instance is None:
            FakeEngines.instance = self
            self.asr, self.tts = FakeASR(), FakeTTS()
            self.signature = "fake"
        else:
            self.__dict__ = FakeEngines.instance.__dict__

    def health(self, probe=False):
        ready = {"state": "ready", "reason": None}
        return {"asr": dict(ready), "tts": dict(ready), "duplex": {"state": "disabled", "reason": "off"}}


OWNER = Principal(name="owner", owner=True, research=True, origin="voice", label="Owner console",
                  device_prefix="console:")


def pocket_principal(research=True, family="fam-digest", node_device="d" * 64):
    return Principal(name="pocket:abcd1234", owner=False, research=research, origin="pocket-voice", family=family,
                     label="Pocket device (abcd1234)", device_prefix="pocket:abcd1234:", node_device=node_device)


@pytest.fixture
def instance(tmp_path):
    path = tmp_path / "individual.sqlite"
    with closing(store.create(path, time.time())) as db:
        wstate.set_config(db, {"enabled": True, "min_domain_interval": 0.0}, time.time())
    return path


@pytest.fixture
def hub(instance):
    FakeEngines.instance = None
    vstate.reset()
    h = VoiceHub(instance, engines_factory=FakeEngines)
    h.fake = FakeEngines({})
    yield h
    for s in list(h.sessions.values()):
        h._end(s, "test")
    vstate.reset()


def pcm(seconds=1.0, rate=16000, amplitude=3000):
    return struct.pack("<h", amplitude) * int(seconds * rate)


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met in time")
