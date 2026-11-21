"""The voice hub of one server process: sessions, streaming utterances, replies, speech and events.

Streaming model (docs/voice/STREAMING_AND_TRANSCRIPTS.md):
- the client detects speech (VAD) and uploads 16 kHz mono PCM16 chunks of the open utterance (final=0), then the
  last chunk with final=1 at end-of-utterance; the hub keeps the utterance in memory only (bounded), transcribes
  it provisionally at most every PROVISIONAL_EVERY seconds and finally once, and drops the audio afterwards (or
  keeps it with a TTL when the owner enabled retention);
- the final transcript goes to the Cognitive Voice Bridge in a worker thread; the reply is stored as an
  authoritative transcript segment and, in mode A, split into sentences that are synthesised one by one in
  order while the client already plays the first (sentence-level streaming);
- events (transcripts, replies, progress of long tasks, mode changes, confirmations) are delivered by long-poll;
- barge-in: the client stops playback itself (no round trip) and reports it; the hub cancels the rest of the turn
  and marks the segment interrupted at the sentence reached. Research tasks are never cancelled by barge-in.
Capture ownership is enforced here as well as in the client: audio is accepted only from the device that holds the
microphone lease, that recorded consent, in a mode with mic input.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import re
import secrets
import threading
import time

from ..instance import store
from ..instance.errors import Conflict, InstanceError, NotFound, ValidationError
from . import bridge as vbridge, config as vconfig, engines as vengines, state as vstate, transcripts as tx

RATE = 16000
MAX_CHUNK = 64 * 1024
MAX_UTTERANCE_SECONDS = 30
MAX_UTTERANCE = RATE * 2 * MAX_UTTERANCE_SECONDS
PROVISIONAL_EVERY = 1.0
MIN_PROVISIONAL_SECONDS = 0.6
MIN_FINAL_SECONDS = 0.25
MAX_SESSIONS = 16
MAX_EVENTS = 500
EVENT_WAIT_MAX = 20.0
HEALTH_TTL = 5.0
SWEEP_EVERY = 60.0
FOLLOW_POLL = 1.0
FOLLOW_MAX = 3 * 3600.0
PROGRESS_SPEAK_EVERY = 30.0
TERMINAL = ("completed", "partial", "failed", "cancelled")
STAGE_WORDS = {"discovering": "searching for sources", "reading": "reading sources", "evaluating":
               "checking the evidence", "synthesizing": "writing the answer", "queued": "waiting for the worker"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def sentences(texts) -> list[str]:
    out = []
    for t in texts:
        out += [s.strip() for s in _SENTENCE.split(t) if s.strip()]
    return out


class Turn:
    def __init__(self, turn_id, utterance, items, voice, speed):
        self.id, self.utterance, self.sentences = turn_id, utterance, items
        self.voice, self.speed = voice, speed
        self.audio: dict[int, bytes | None] = {}
        self.cond = threading.Condition()
        self.status = "speaking"
        self.spoken_until = 0
        self.created = time.monotonic()
        self.synth_ms: list[float] = []


class Session:
    def __init__(self, sid, principal, device, label, mode):
        self.id, self.principal, self.device, self.label, self.mode = sid, principal, device, label, mode
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.events: deque = deque(maxlen=MAX_EVENTS)
        self.seq = 0
        self.utterances: dict[str, dict] = {}
        self.turns: dict[str, Turn] = {}
        self.last_turn: Turn | None = None
        self.pending: dict | None = None
        self.reply_n = 0
        self.output_muted = False
        self.active = True
        self.seen = time.monotonic()


class VoiceHub:
    def __init__(self, db_path, *, clock=time.time, engines_factory=None):
        self.db_path, self.clock = db_path, clock
        self.sessions: dict[str, Session] = {}
        self.aliases: dict[str, str] = {}     # a forgotten conversation's id -> its fresh session (same client)
        self.lock = threading.Lock()
        self.bridge = vbridge.Bridge(clock)
        self._engines = None
        self._engines_factory = engines_factory or vengines.Engines
        self._health = (0.0, None)
        self._last_sweep = float("-inf")
        self.metrics = {"utterances": 0, "asr_ms": deque(maxlen=200), "reply_ms": deque(maxlen=200),
                        "first_sentence_ms": deque(maxlen=200), "barge_ins": 0, "overlaps": 0, "refused_audio": 0,
                        "stop_latency_ms": deque(maxlen=200)}

    # ---- plumbing ---------------------------------------------------------------------------------------------
    @contextmanager
    def db(self):
        conn = store.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def engines(self, cfg=None):
        if cfg is None:
            with self.db() as db:
                cfg = vconfig.get(db)
        fresh = self._engines_factory(cfg)
        if self._engines is None or getattr(self._engines, "signature", None) != getattr(fresh, "signature", 1):
            self._engines = fresh
        return self._engines

    def health(self, *, force=False):
        at, cached = self._health
        if not force and cached is not None and time.monotonic() - at < HEALTH_TTL:
            return cached
        result = self.engines().health()
        self._health = (time.monotonic(), result)
        return result

    def _sweep(self, db):
        if time.monotonic() - self._last_sweep >= SWEEP_EVERY:
            self._last_sweep = time.monotonic()
            tx.sweep(db, self.clock())

    def _emit(self, s: Session, etype, **data):
        with s.cond:
            s.seq += 1
            s.events.append({"seq": s.seq, "type": etype, "at": self.clock(), **data})
            s.cond.notify_all()

    def _state(self, s: Session, **changes):
        vstate.update(s.id, **changes)

    def session(self, sid, principal) -> Session:
        sid = self.aliases.get(sid, sid) if isinstance(sid, str) else None
        s = self.sessions.get(sid) if sid else None
        if s is None or s.principal.name != principal.name or not s.active:
            raise NotFound("voice session not found (start a new one)")
        s.seen = time.monotonic()
        return s

    # ---- sessions -----------------------------------------------------------------------------------------------
    def start(self, principal, device, label, mode=None) -> dict:
        if not isinstance(device, str) or not 4 <= len(device) <= 120:
            raise ValidationError("device must be an id of 4..120 characters")
        with self.db() as db:
            self._sweep(db)
            cfg = vconfig.get(db)
            mode = mode or cfg["mode"]
            if mode not in vconfig.MODES:
                raise ValidationError("mode must be one of " + ", ".join(vconfig.MODES))
            context = tx.last_context(db, principal.name)
            sid = tx.start_session(db, principal.name, device, mode, self.clock())
            if context:
                tx.update_session(db, sid, self.clock(), context={k: v for k, v in context.items()
                                                                  if k in ("last_task_id", "last_objective")})
        s = Session(sid, principal, device, label, mode)
        with self.lock:
            stale = sorted((x for x in self.sessions.values()), key=lambda x: x.seen)
            while len(self.sessions) >= MAX_SESSIONS and stale:
                self._end(stale.pop(0), "evicted")
            self.sessions[sid] = s
        self._state(s, mode=mode, session_active=True, capture_device=None, mic_capturing=False, speaking=False,
                    user_speaking=False, interrupted_at=None)
        return self.public(s)

    def stop(self, sid, principal) -> dict:
        s = self.session(sid, principal)
        self._end(s, "stopped")
        return {"session": sid, "status": "ended"}

    def _end(self, s: Session, why):
        with s.lock:
            s.active = False
            s.utterances.clear()
            for t in s.turns.values():
                self._cancel_turn(t, "session_ended")
        with self.db() as db:
            try:
                if tx.holds(db, s.device, self.clock()):
                    tx.release(db, s.device)
                tx.update_session(db, s.id, self.clock(), status="ended")
            except (NotFound, InstanceError):
                pass
        self._emit(s, "session", status="ended", reason=why)
        vstate.end(s.id)
        with self.lock:
            self.sessions.pop(s.id, None)

    def public(self, s: Session) -> dict:
        with self.db() as db:
            row = tx.session(db, s.id)
            cfg = vconfig.get(db)
            capture = tx.holder(db, self.clock())
        return {"session": s.id, "mode": s.mode, "device": s.device, "context": row["context"],
                "consented": s.device in cfg["consent"], "capture": self._capture_public(capture, s),
                "pending": None if s.pending is None else {k: s.pending[k] for k in ("id", "summary", "expires_at")},
                "events_seq": s.seq}

    def _capture_public(self, holder, s=None):
        if holder is None:
            return {"held": False, "this_device": False}
        return {"held": True, "this_device": bool(s and holder["device"] == s.device), "label": holder["label"],
                "expires_in": round(holder["expires_at"] - self.clock(), 1)}

    def status(self, principal, sid=None, *, probe=False) -> dict:
        with self.db() as db:
            self._sweep(db)
            cfg = vconfig.get(db)
            capture = tx.holder(db, self.clock())
            retained = tx.audio_count(db)
        s = self.sessions.get(sid) if sid else None
        if s is not None and s.principal.name != principal.name:
            s = None
        health = self.health(force=probe)
        m = self.metrics
        avg = (lambda xs: round(sum(xs) / len(xs), 1) if xs else None)
        return {"settings": vconfig.public(cfg), "voices": list(vconfig.VOICES), "modes": list(vconfig.MODES),
                "engines": health, "capture": self._capture_public(capture, s), "retained_audio_segments": retained,
                "session": self.public(s) if s else None, "snapshot": vstate.snapshot(),
                "available": {"voice_in": health["asr"]["state"] == "ready",
                              "voice_out": health["tts"]["state"] == "ready", "text": True},
                "metrics": {"utterances": m["utterances"], "asr_ms_avg": avg(m["asr_ms"]),
                            "reply_ms_avg": avg(m["reply_ms"]), "first_sentence_ms_avg": avg(m["first_sentence_ms"]),
                            "barge_ins": m["barge_ins"], "overlaps": m["overlaps"],
                            "refused_audio": m["refused_audio"], "stop_latency_ms_avg": avg(m["stop_latency_ms"])},
                "principal": {"owner": principal.owner, "research": principal.research, "label": principal.label}}

    # ---- settings, consent, mode ------------------------------------------------------------------------------
    def settings(self, principal, changes) -> dict:
        with self.db() as db:
            cfg = vconfig.update(db, changes, owner=principal.owner)
            if not cfg["retain_audio"]:
                tx.sweep(db, self.clock())
        self._health = (0.0, None)
        return vconfig.public(cfg)

    def consent(self, sid, principal, accept: bool) -> dict:
        s = self.session(sid, principal)
        with self.db() as db:
            vconfig.record_consent(db, s.device, self.clock(), bool(accept))
            if not accept and tx.holds(db, s.device, self.clock()):
                tx.release(db, s.device)
        if not accept:
            self._state(s, mic_capturing=False, capture_device=None)
        return {"consented": bool(accept)}

    def set_mode(self, sid, principal, mode) -> dict:
        s = self.session(sid, principal)
        if mode not in vconfig.MODES:
            raise ValidationError("mode must be one of " + ", ".join(vconfig.MODES))
        self._apply_mode(s, mode)
        return self.public(s)

    def _apply_mode(self, s: Session, mode):
        with s.lock:
            s.mode = mode
            if mode != "voice_text":
                for t in s.turns.values():
                    self._cancel_turn(t, "mode")
            if mode == "text_only":
                s.utterances.clear()
        with self.db() as db:
            tx.update_session(db, s.id, self.clock(), mode=mode)
            vconfig.update(db, {"mode": mode}, owner=True)   # the preference, not a permission
            if mode == "text_only" and tx.holds(db, s.device, self.clock()):
                tx.release(db, s.device)
        changes = {"mode": mode}
        if mode == "text_only":
            changes.update(mic_capturing=False, user_speaking=False, capture_device=None, speaking=False)
        elif mode == "voice_in_text_out":
            changes.update(speaking=False)
        self._state(s, **changes)
        self._emit(s, "mode", mode=mode)

    # ---- capture ownership --------------------------------------------------------------------------------------
    def capture(self, sid, principal, action, *, handoff_confirm=None) -> dict:
        s = self.session(sid, principal)
        now = self.clock()
        with self.db() as db:
            if action in ("acquire", "handoff"):
                if s.mode == "text_only":
                    raise Conflict("text-only mode: the microphone is off (switch mode first)")
                if s.device not in vconfig.get(db)["consent"]:
                    raise Conflict("consent required before the first capture on this device")
                handoff = False
                if action == "handoff":
                    if not (s.pending and s.pending["action"] == "handoff" and s.pending["id"] == handoff_confirm
                            and s.pending["expires_at"] > now):
                        raise Conflict("hand-off needs a confirmation (ask for it, then confirm)")
                    s.pending = None
                    handoff = True
                out = tx.acquire(db, s.device, s.label, principal.name, s.id, now, handoff=handoff)
                if out["granted"]:
                    self._state(s, capture_device=s.label)
                return out
            if action == "renew":
                ok = tx.renew(db, s.device, now)
                if not ok:
                    self._state(s, mic_capturing=False, capture_device=None)
                    with s.lock:
                        s.utterances.clear()
                return {"granted": ok, "holder": self._capture_public(tx.holder(db, now), s)}
            if action == "request_handoff":
                pending = self.bridge._confirm("handoff", "handoff", {}, "Move the microphone to this device? The "
                                                                         "other device stops listening.").confirm
                s.pending = pending
                self._emit(s, "confirm", confirm={k: pending[k] for k in ("id", "action", "summary", "expires_at")})
                return {"confirm": {k: pending[k] for k in ("id", "action", "summary", "expires_at")}}
            if action == "release":
                released = tx.release(db, s.device)
                with s.lock:
                    s.utterances.clear()
                self._state(s, mic_capturing=False, user_speaking=False, capture_device=None)
                return {"released": released}
        raise ValidationError("action must be acquire, renew, release, request_handoff or handoff")

    # ---- audio in -------------------------------------------------------------------------------------------------
    def audio(self, sid, principal, utterance, seq, final, pcm: bytes, *, started_at=None) -> dict:
        s = self.session(sid, principal)
        now = self.clock()
        if not isinstance(utterance, str) or not ID.match(utterance):
            raise ValidationError("utterance id must match [A-Za-z0-9_-]{4,64}")
        if len(pcm) % 2 or len(pcm) > MAX_CHUNK:
            raise ValidationError(f"audio chunk must be 16-bit PCM of at most {MAX_CHUNK} bytes")
        if s.mode == "text_only":
            self.metrics["refused_audio"] += 1
            raise Conflict("text-only mode: audio is not accepted")
        with self.db() as db:
            if s.device not in vconfig.get(db)["consent"]:
                self.metrics["refused_audio"] += 1
                raise Conflict("consent required before capture")
            if not tx.holds(db, s.device, now):
                self.metrics["refused_audio"] += 1
                with s.lock:
                    s.utterances.clear()
                raise Conflict("this device does not hold the microphone (another device has it, or it was "
                               "released)")
            tx.renew(db, s.device, now)
        with s.lock:
            u = s.utterances.get(utterance)
            if u is None:
                if len(s.utterances) >= 2:       # at most one open utterance plus one being finalised
                    s.utterances.pop(next(iter(s.utterances)))
                u = s.utterances[utterance] = {"pcm": bytearray(), "seq": -1, "started": started_at or now,
                                               "last_prov": 0.0, "text": ""}
            if seq != u["seq"] + 1:
                raise Conflict(f"expected chunk {u['seq'] + 1}, got {seq}")
            if len(u["pcm"]) + len(pcm) > MAX_UTTERANCE:
                final = True                     # bounded: an utterance longer than 30 s is closed here
                pcm = pcm[:max(0, MAX_UTTERANCE - len(u["pcm"]))]
            u["pcm"] += pcm
            u["seq"] = seq
            buf = bytes(u["pcm"])
            due = (time.monotonic() - u["last_prov"] >= PROVISIONAL_EVERY and
                   len(buf) >= MIN_PROVISIONAL_SECONDS * RATE * 2)
            if final:
                s.utterances.pop(utterance, None)
            elif due:
                u["last_prov"] = time.monotonic()
        if not final:
            if not due:
                return {"status": "buffered", "seconds": round(len(buf) / RATE / 2, 2)}
            self._state(s, user_speaking=True)
            return self._provisional(s, utterance, buf, u["started"])
        return self._final(s, utterance, buf, u["started"])

    def _transcribe(self, buf):
        result = self.engines().asr.transcribe(buf, RATE)
        self.metrics["asr_ms"].append(result["latency"] * 1000)
        return result

    def _provisional(self, s, utterance, buf, started):
        try:
            result = self._transcribe(buf)
        except vengines.EngineError as err:
            return {"status": "provisional_failed", "error": err.code}
        if not result["text"]:
            return {"status": "buffered", "seconds": round(len(buf) / RATE / 2, 2)}
        with self.db() as db:
            seg = tx.upsert(db, s.id, utterance, speaker="user", source="asr", status="provisional",
                            text=result["text"], now=self.clock(), started_at=started)
        self._emit(s, "transcript", segment=_seg(seg))
        return {"status": "provisional", "text": result["text"], "segment": seg["id"]}

    def _final(self, s, utterance, buf, started):
        received = time.monotonic()
        self._state(s, user_speaking=False)
        if len(buf) < MIN_FINAL_SECONDS * RATE * 2:
            return {"status": "too_short"}
        try:
            result = self._transcribe(buf)
        except vengines.EngineError as err:
            self._emit(s, "error", what="asr", code=err.code, message="Speech recognition is unavailable; type "
                                                                       "instead (text works without voice services).")
            return {"status": "asr_failed", "error": err.code}
        text = result["text"]
        if not text or _noise(text):
            with self.db() as db:
                db.execute("DELETE FROM voice_segments WHERE session_id=? AND utterance=?", (s.id, utterance))
            self._emit(s, "transcript_dropped", utterance=utterance)
            return {"status": "empty"}
        now = self.clock()
        with self.db() as db:
            seg = tx.upsert(db, s.id, utterance, speaker="user", source="asr", status="final", text=text, now=now,
                            started_at=started, ended_at=now,
                            detail={"asr_ms": round(result["latency"] * 1000, 1),
                                    "audio_seconds": round(result["audio_seconds"], 2)})
            cfg = vconfig.get(db)
            if cfg["retain_audio"]:
                tx.retain_audio(db, seg["id"], buf, RATE, now, cfg["audio_ttl_hours"])
        self.metrics["utterances"] += 1
        self._emit(s, "transcript", segment=_seg(seg))
        threading.Thread(target=self._respond, args=(s, utterance, text, received), daemon=True,
                         name="nsf-voice-bridge").start()
        return {"status": "final", "text": text, "segment": seg["id"], "asr_ms": round(result["latency"] * 1000, 1)}

    def text(self, sid, principal, text) -> dict:
        s = self.session(sid, principal)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValidationError("text must be 1..2000 characters")
        utterance = "t" + secrets.token_hex(6)
        with self.db() as db:
            seg = tx.upsert(db, s.id, utterance, speaker="user", source="typed", status="final",
                            text=" ".join(text.split()), now=self.clock(), ended_at=self.clock())
        self._emit(s, "transcript", segment=_seg(seg))
        threading.Thread(target=self._respond, args=(s, utterance, text, time.monotonic()), daemon=True,
                         name="nsf-voice-bridge").start()
        return {"status": "final", "segment": seg["id"]}

    # ---- replies ----------------------------------------------------------------------------------------------------
    def _respond(self, s: Session, utterance, text, received):
        try:
            with self.db() as db:
                ctx = tx.session(db, s.id)["context"]
                pending = s.pending if s.pending and s.pending["expires_at"] > self.clock() else None
                reply = self.bridge.handle(db, s.principal, ctx, text, pending)
        except Exception as exc:   # the owner hears that something failed, never silence
            reply = vbridge.Reply("error", speech=["Sorry, something went wrong handling that."],
                                  text=f"Error: {type(exc).__name__}")
        self.metrics["reply_ms"].append((time.monotonic() - received) * 1000)
        self._deliver(s, reply, responding_to=utterance, received=received)

    def _deliver(self, s: Session, reply, *, responding_to=None, received=None):
        with s.lock:
            s.reply_n += 1
            rid = f"r{s.reply_n}-{secrets.token_hex(3)}"
        actions_after = []
        for action in reply.actions:
            kind = action["kind"]
            if kind == "context":
                with self.db() as db:
                    try:
                        tx.update_session(db, s.id, self.clock(), context={k: v for k, v in action.items()
                                                                           if k != "kind"})
                    except NotFound:
                        pass
            elif kind == "clear_confirm":
                s.pending = None
            elif kind == "stop_speaking":
                self._stop_turns(s, "stop_command")
            elif kind == "mode":
                actions_after.append(action)
            elif kind == "handoff":
                with self.db() as db:
                    out = tx.acquire(db, s.device, s.label, s.principal.name, s.id, self.clock(), handoff=True)
                if out["granted"]:
                    self._state(s, capture_device=s.label)
            elif kind in ("forget_conversation", "resume_speaking"):
                actions_after.append(action)
        if reply.confirm:
            s.pending = reply.confirm
        speak = bool(reply.speech) and s.mode == "voice_text" and not s.output_muted and s.active
        seg = None
        if s.active or reply.task_id:
            with self.db() as db:
                try:
                    seg = tx.upsert(db, s.id, rid, speaker="neuroscifi", source=reply.source, status="final",
                                    text=reply.text or " ".join(reply.speech), now=self.clock(),
                                    ended_at=self.clock(), task_id=reply.task_id,
                                    detail={"label": reply.label, "intent": reply.intent, "speech": reply.speech,
                                            "sources": reply.sources, "responding_to": responding_to})
                except (NotFound, InstanceError):
                    seg = None   # the conversation was forgotten meanwhile
        turn = None
        if speak:
            with self.db() as db:
                cfg = vconfig.get(db)
            turn = Turn(rid, rid, sentences(reply.speech), cfg["voice"], cfg["speed"])
            with s.lock:
                s.turns[rid] = turn
                s.last_turn = turn
                for old in list(s.turns.values()):
                    if old is not turn and old.status == "speaking":
                        self._cancel_turn(old, "superseded")
                while len(s.turns) > 8:
                    s.turns.pop(next(iter(s.turns)))
            threading.Thread(target=self._synthesize, args=(s, turn, received), daemon=True,
                             name="nsf-voice-tts").start()
        self._emit(s, "reply", reply=reply.public(), segment=_seg(seg) if seg else None,
                   turn={"id": turn.id, "sentences": len(turn.sentences)} if turn else None)
        for action in actions_after:
            if action["kind"] == "mode":
                self._apply_mode(s, action["mode"])
            elif action["kind"] == "forget_conversation":
                with self.db() as db:
                    tx.forget_session(db, s.id)
                    sid = tx.start_session(db, s.principal.name, s.device, s.mode, self.clock())
                self._emit(s, "forgotten", new_session=sid)
                with self.lock:
                    self.sessions.pop(s.id, None)
                    vstate.end(s.id)
                    self.aliases[s.id] = sid
                    s.id = sid
                    self.sessions[sid] = s
                self._state(s, mode=s.mode, session_active=True)
            elif action["kind"] == "resume_speaking":
                rest = self.resume(s)
                if rest is not None:
                    self._deliver(s, rest)
                else:
                    self._emit(s, "notice", text="There is nothing interrupted to continue.")
        if reply.follow and s.principal.research:
            threading.Thread(target=self._follow, args=(s, reply.follow), daemon=True,
                             name="nsf-voice-follow").start()

    def _synthesize(self, s: Session, turn: Turn, received):
        tts = self.engines().tts
        for i, sentence in enumerate(turn.sentences):
            if turn.status != "speaking":
                break
            try:
                out = tts.synthesize(sentence, turn.voice, turn.speed)
                data = out["wav"]
                turn.synth_ms.append(out["latency"] * 1000)
                if i == 0 and received is not None:
                    self.metrics["first_sentence_ms"].append((time.monotonic() - received) * 1000)
            except vengines.EngineError as err:
                data = None
                self._emit(s, "error", what="tts", code=err.code, message="Speech output is unavailable; the reply "
                                                                           "is shown as text.")
            with turn.cond:
                turn.audio[i] = data
                turn.cond.notify_all()
            if data is None:
                break
        with turn.cond:
            for j in range(len(turn.sentences)):
                turn.audio.setdefault(j, None)
            turn.cond.notify_all()

    def speech(self, sid, principal, turn_id, n, *, wait=15.0) -> bytes:
        s = self.session(sid, principal)
        turn = s.turns.get(turn_id)
        if turn is None or not isinstance(n, int) or not 0 <= n < len(turn.sentences):
            raise NotFound("no such sentence")
        if s.mode != "voice_text":
            raise Conflict("speech output is off in this mode")
        deadline = time.monotonic() + wait
        with turn.cond:
            while n not in turn.audio and turn.status == "speaking":
                left = deadline - time.monotonic()
                if left <= 0:
                    raise Conflict("speech not ready")
                turn.cond.wait(left)
            data = turn.audio.get(n)
        if turn.status in ("interrupted", "cancelled"):
            raise Conflict("this reply was interrupted")
        if data is None:
            raise Conflict("speech synthesis failed; the reply is shown as text")
        self._state(s, speaking=True)
        return data

    def _cancel_turn(self, turn: Turn, why):
        with turn.cond:
            if turn.status == "speaking":
                turn.status = "interrupted" if why in ("barge_in", "stop_command", "stop") else "cancelled"
            turn.cond.notify_all()

    def _stop_turns(self, s: Session, why):
        with s.lock:
            for t in s.turns.values():
                self._cancel_turn(t, why)
        self._state(s, speaking=False, interrupted_at=self.clock())
        self._emit(s, "stop_speaking", reason=why)

    def interrupt(self, sid, principal, turn_id, sentence, kind, *, detect_ms=None, stop_ms=None) -> dict:
        """The client stopped playback (barge-in or stop) or heard a brief overlap. Never cancels research."""
        s = self.session(sid, principal)
        if kind not in ("barge_in", "stop", "overlap", "finished"):
            raise ValidationError("kind must be barge_in, stop, overlap or finished")
        if kind == "overlap":
            self.metrics["overlaps"] += 1
            return {"overlap": True}
        turn = s.turns.get(turn_id)
        if turn is None:
            raise NotFound("no such reply")
        if kind == "finished":
            with turn.cond:
                turn.status, turn.spoken_until = "done", len(turn.sentences)
            self._state(s, speaking=False)
            return {"finished": True}
        if not isinstance(sentence, int) or not 0 <= sentence <= len(turn.sentences):
            raise ValidationError("sentence out of range")
        self._cancel_turn(turn, "barge_in" if kind == "barge_in" else "stop")
        turn.spoken_until = sentence
        if kind == "barge_in":
            self.metrics["barge_ins"] += 1
        if isinstance(stop_ms, (int, float)) and not isinstance(stop_ms, bool) and 0 <= stop_ms < 60000:
            self.metrics["stop_latency_ms"].append(float(stop_ms))
        now = self.clock()
        with self.db() as db:
            seg = tx.mark_interrupted(db, s.id, turn.utterance, sentence, now)
        self._state(s, speaking=False, interrupted_at=now)
        self._emit(s, "interrupted", turn=turn.id, sentence=sentence, kind=kind, segment=_seg(seg) if seg else None)
        return {"interrupted": True, "spoken_until": sentence}

    def resume(self, s: Session):
        turn = s.last_turn
        if turn is None or turn.status not in ("interrupted",) or turn.spoken_until >= len(turn.sentences):
            return None
        rest = turn.sentences[turn.spoken_until:]
        return vbridge.Reply("resume_speaking", speech=rest, text=" ".join(rest), source="template",
                             label="Rest of the interrupted reply")

    # ---- confirmations, client state -------------------------------------------------------------------------------
    def confirm(self, sid, principal, confirm_id, accept) -> dict:
        s = self.session(sid, principal)
        pending = s.pending
        if not pending or pending["id"] != confirm_id or pending["expires_at"] <= self.clock():
            s.pending = None if pending and pending["expires_at"] <= self.clock() else s.pending
            raise Conflict("no such confirmation (it may have expired)")
        if not accept:
            s.pending = None
            self._deliver(s, vbridge.Reply("deny", speech=["Okay, I won't do that."],
                                           text=f"Not done: {pending['summary']}"))
            return {"done": False}
        with self.db() as db:
            ctx = tx.session(db, s.id)["context"]
            reply = self.bridge.execute(db, principal, ctx, pending)
        s.pending = None
        self._deliver(s, reply)
        return {"done": True, "intent": reply.intent}

    def client_status(self, sid, principal, **fields) -> dict:
        s = self.session(sid, principal)
        changes = {}
        for key in ("mic_capturing", "speaking", "user_speaking", "output_muted"):
            if key in fields:
                if not isinstance(fields[key], bool):
                    raise ValidationError(f"{key} must be true or false")
                changes[key] = fields[key]
        if changes.get("mic_capturing"):
            with self.db() as db:
                if s.mode == "text_only" or not tx.holds(db, s.device, self.clock()):
                    raise Conflict("this device does not hold the microphone")
        if "output_muted" in changes:
            s.output_muted = changes["output_muted"]
            if s.output_muted:
                changes["speaking"] = False
        if changes.get("speaking") and (s.mode != "voice_text" or s.output_muted):
            changes["speaking"] = False
        return {k: v for k, v in self._apply_status(s, changes).items()}

    def _apply_status(self, s, changes):
        return vstate.update(s.id, **changes)

    def events(self, sid, principal, since, *, wait=EVENT_WAIT_MAX) -> dict:
        s = self.session(sid, principal)
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise ValidationError("since must be a non-negative integer")
        deadline = time.monotonic() + max(0.0, min(wait, EVENT_WAIT_MAX))
        with s.cond:
            while s.seq <= since and s.active:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                s.cond.wait(left)
            items = [e for e in s.events if e["seq"] > since]
        return {"events": items, "seq": s.seq, "active": s.active}

    # ---- long tasks ---------------------------------------------------------------------------------------------------
    def _follow(self, s: Session, task_id):
        """Report truthful progress of a research task and deliver its answer. The task belongs to the research
        system: it keeps running when the voice session ends (only 'cancel the research' stops it); its answer
        is still written to the transcript."""
        from ..webcortex import orchestrator
        started = time.monotonic()
        last_stage, last_spoken = None, time.monotonic()
        while time.monotonic() - started < FOLLOW_MAX:
            try:
                with self.db() as db:
                    task = orchestrator.get(db, task_id, family=s.principal.family)
            except InstanceError:
                return
            if task["status"] in TERMINAL:
                reply = vbridge.compose_answer(task)
                reply.actions.append({"kind": "context", "last_task_id": task_id})
                self._emit(s, "task_done", task_id=task_id, status=task["status"])
                self._deliver(s, reply)
                return
            stage = task.get("stage") or task["status"]
            if stage != last_stage:
                last_stage = stage
                words = STAGE_WORDS.get(stage, stage)
                self._emit(s, "progress", task_id=task_id, stage=stage, text=f"Research: {words}",
                           elapsed=round(time.monotonic() - started, 1))
                if s.active and time.monotonic() - last_spoken >= PROGRESS_SPEAK_EVERY and s.mode == "voice_text" \
                        and not any(t.status == "speaking" for t in s.turns.values()):
                    last_spoken = time.monotonic()
                    self._deliver(s, vbridge.Reply("progress", speech=[f"Still working: {words}."],
                                                   text=f"Still working: {words}.", task_id=None))
            time.sleep(FOLLOW_POLL)

    # ---- transcripts -------------------------------------------------------------------------------------------------
    def transcripts(self, principal, *, session_id=None, query=None, limit=200) -> dict:
        scope = None if principal.owner else principal.name
        with self.db() as db:
            if query:
                items = tx.search(db, query, principal=scope, limit=limit)
            else:
                items = tx.listing(db, session_id=session_id, principal=scope, limit=limit)
            sess = tx.sessions(db, scope, limit=20)
        return {"segments": [_seg(x) for x in items], "sessions": sess}

    def correct(self, principal, seg_id, text) -> dict:
        with self.db() as db:
            return _seg(tx.correct(db, seg_id, text, self.clock(), principal=None if principal.owner
                                   else principal.name))

    def forget(self, principal, session_id) -> dict:
        with self.db() as db:
            out = tx.forget_session(db, session_id, principal=None if principal.owner else principal.name)
        s = self.sessions.get(session_id)
        if s is not None:
            self._end(s, "forgotten")
        return out


_NOISE = {"you", "thank you", "thanks for watching", "bye", "uh", "um", "hmm", "mm"}


def _noise(text):
    """Whisper's well-known outputs for silence or noise; not an utterance."""
    norm = re.sub(r"[^a-z ]+", "", text.lower()).strip()
    return not norm or norm in _NOISE


def _seg(seg):
    if not seg:
        return None
    return {k: seg.get(k) for k in ("id", "session_id", "utterance", "seq", "speaker", "source", "status", "authority",
                                    "text", "corrected_text", "display_text", "label", "task_id", "started_at",
                                    "ended_at", "created_at", "updated_at", "detail")}
