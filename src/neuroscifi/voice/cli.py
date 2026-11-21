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
            raise SystemExit("--set takes a JSON object")
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


def register(sub):
    from ..instance import store
    p = sub.add_parser("voice", help="Voice Cortex: speech services, settings, transcripts")
    vs = p.add_subparsers(dest="voice_command", required=True, metavar="ACTION")

    def cmd(name, fn, text):
        q = vs.add_parser(name, help=text)
        store.add_db_argument(q)
        q.set_defaults(handler=lambda a: _print(fn(a)))
        return q
    cmd("health", _health, "engine health and a real TTS -> ASR round trip with word error rate")
    q = cmd("settings", _settings, "show or change voice settings")
    q.add_argument("--set", help='JSON object, e.g. {"retain_audio": false, "voice": "af_heart"}')
    q = cmd("transcripts", _transcripts, "list sessions and transcript segments, or search them")
    q.add_argument("--session")
    q.add_argument("--search")
    q.add_argument("--limit", type=int, default=50)
    q = cmd("forget", _forget, "delete one voice session, its transcript and retained audio")
    q.add_argument("session")
    cmd("sweep", _sweep, "delete retained audio past its TTL (all of it when retention is off)")


def _print(value):
    print(json.dumps(value, indent=2, default=str))
    return 0
