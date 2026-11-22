"""`nsf voice`: speech service health (a real synthesis -> transcription round trip), settings and transcripts."""
from __future__ import annotations

import json
import re
import time


def _db(args):
    from ..instance import store
    return store.open_existing(store.resolve_db(args))


def _words(text):
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("-", " ")).split()


def wer(ref: str, hyp: str) -> float:
    """Word error rate (Levenshtein over words) after lower-casing and dropping punctuation."""
    r, h = _words(ref), _words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / len(r)


HEALTH_SENTENCE = "Microphone capture is now off, and no raw audio will be stored on this device."


def health(engines) -> dict:
    """Engine health plus a round trip: Kokoro speaks a fixed sentence, Whisper transcribes it, WER is computed."""
    from . import engines as vengines
    out = engines.health(probe=True)
    trip = {"sentence": HEALTH_SENTENCE}
    try:
        spoken = engines.tts.synthesize(HEALTH_SENTENCE, "af_heart")
        pcm24 = spoken["wav"][44:]
        samples = [int.from_bytes(pcm24[i:i + 2], "little", signed=True) for i in range(0, len(pcm24) - 1, 2)]
        pcm16 = b"".join(int(samples[int(k * 1.5)]).to_bytes(2, "little", signed=True)
                         for k in range(int(len(samples) / 1.5)))           # 24 kHz -> 16 kHz (nearest sample)
        heard = engines.asr.transcribe(pcm16, 16000)
        trip.update(transcript=heard["text"], wer=round(wer(HEALTH_SENTENCE, heard["text"]), 4),
                    tts_ms=round(spoken["latency"] * 1000, 1), asr_ms=round(heard["latency"] * 1000, 1),
                    audio_seconds=round(spoken["audio_seconds"], 2))
        trip["ok"] = trip["wer"] <= 0.1
    except vengines.EngineError as err:
        trip.update(ok=False, error=f"{err.code}: {err}")
    out["round_trip"] = trip
    out["ready"] = out["asr"]["state"] == "ready" and out["tts"]["state"] == "ready" and trip["ok"]
    return out


def _health(args):
    from . import config, engines
    db = _db(args)
    try:
        cfg = config.get(db)
    finally:
        db.close()
    return health(engines.Engines(cfg))


def _settings(args):
    from . import config
    db = _db(args)
    try:
        changes = json.loads(args.set) if args.set else {}
        if not isinstance(changes, dict):
            from ..instance.errors import ValidationError
            raise ValidationError("--set takes a JSON object")
        cfg = config.update(db, changes) if changes else config.get(db)
        return config.public(cfg)
    finally:
        db.close()


def _transcripts(args):
    from . import transcripts
    db = _db(args)
    try:
        if args.search:
            return transcripts.search(db, args.search, limit=args.limit)
        return {"sessions": transcripts.sessions(db, limit=args.limit),
                "segments": transcripts.listing(db, session_id=args.session, limit=args.limit)}
    finally:
        db.close()


def _forget(args):
    from . import transcripts
    db = _db(args)
    try:
        return transcripts.forget_session(db, args.session)
    finally:
        db.close()


def _sweep(args):
    from . import transcripts
    db = _db(args)
    try:
        return {"deleted_audio": transcripts.sweep(db, time.time())}
    finally:
        db.close()


def _selftest(args):
    """Guided self-test on this computer's microphone and speaker; the report goes to --report (and next to the
    instance as voice-selftest/selftest-*.json). --fake-input WAV replaces the microphone with a scripted fake device
    (every step hears that file) and --yes answers every question yes: for dry runs and tests only."""
    import sys
    from pathlib import Path
    from . import config, engines, selftest
    from ..instance import store
    db_path = store.resolve_db(args)
    db = _db(args)
    try:
        cfg = config.get(db)
    finally:
        db.close()
    eng = engines.Engines(cfg)
    if args.fake_input:
        import wave
        with wave.open(args.fake_input) as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
                from ..instance.errors import ValidationError
                raise ValidationError("--fake-input must be a 16 kHz mono 16-bit WAV")
            pcm = w.readframes(w.getnframes())
        audio = selftest.ScriptedAudio({k: pcm for k in ["mic", "silence", "playback", "barge", "echo", "micoff",
                                                         *[f"read{i}" for i in range(len(selftest.SENTENCES))]]})
    else:
        audio = selftest.HostAudio()

    def say(text):
        print(text, file=sys.stderr, flush=True)

    def ask(question):
        if args.yes:
            say(question + " [yes: --yes]")
            return True
        print(question + " [y/n] ", end="", file=sys.stderr, flush=True)
        return sys.stdin.readline().strip().lower().startswith("y")
    evidence = selftest.run_terminal(eng, audio, ask, say, voice=cfg["voice"], speed=cfg["speed"])
    Path(args.report).write_text(json.dumps(evidence, indent=1) + "\n")
    local = selftest.write_evidence(Path(db_path).resolve().parent / "voice-selftest", evidence)
    return {"report": str(args.report), "saved": local.name, "verdict": evidence["verdict"],
            "audio_retained": False, "fake_device": audio.fake}


def register(sub):
    from ..instance import store
    from ..instance.cli import LIMIT, run
    p = sub.add_parser("voice", help="Voice Cortex: speech services, settings, transcripts")
    vs = p.add_subparsers(dest="voice_command", required=True, metavar="ACTION")

    def cmd(name, fn, text):
        q = vs.add_parser(name, help=text)
        store.add_db_argument(q)
        q.set_defaults(handler=run(lambda a: _jsonable(fn(a))))   # one-line errors, exit 2 (UAT T5-C01)
        return q
    cmd("health", _health, "engine health and a real TTS -> ASR round trip with word error rate")
    q = cmd("settings", _settings, "show or change voice settings")
    q.add_argument("--set", help='JSON object, e.g. {"retain_audio": false, "voice": "af_heart"}')
    q = cmd("transcripts", _transcripts, "list sessions and transcript segments, or search them")
    q.add_argument("--session")
    q.add_argument("--search")
    q.add_argument("--limit", type=LIMIT, default=50)
    q = cmd("forget", _forget, "delete one voice session, its transcript and retained audio")
    q.add_argument("session")
    cmd("sweep", _sweep, "delete retained audio past its TTL (all of it when retention is off)")
    q = cmd("selftest", _selftest, "guided microphone/speaker/barge-in/echo self-test; writes an evidence report")
    q.add_argument("--report", required=True, help="where to write the evidence JSON")
    q.add_argument("--fake-input", help="16 kHz mono WAV used instead of the microphone (fake device, dry runs)")
    q.add_argument("--yes", action="store_true", help="answer every question yes (dry runs)")


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))
