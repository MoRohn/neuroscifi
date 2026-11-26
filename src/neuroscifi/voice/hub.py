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
microphone lease, that recorded consent, in a mode with mic input. A device takes the lease from another device of
its own principal (or the owner from anyone) on its own confirmation; a Pocket device asks, and only the holder's
session grants it (transcripts.request_handoff / answer_handoff).
Principals are kept apart: at most MAX_SESSIONS_PER_PRINCIPAL sessions each, a Pocket family never evicts the owner's
or another family's sessions, a Pocket principal's mode and voice preferences are its own (config.update_pref), and its status
shows only its own session (no owner snapshot, metrics or holder label).
Audio requests do no database writes: chunks are buffered in memory, consent and the lease are read, and the
provisional and final segments (with raw audio when retention is on, in the same transaction) are written by one
writer thread, so an upload never waits behind the research worker's write transaction. Reply and research-follow
threads are bounded (MAX_REPLY_WORKERS, MAX_FOLLOWERS); beyond that the hub answers busy.
Research follow-ups (R8, review1 A9/A10/S9): the answer to a spoken research question is delivered at most once, to
the person's session that is live when it is ready (looked up then, not the session that asked). With the owner's
resume_follow on, the awaited task is kept in the session's context with the hub that follows it (follow_by), and
the delivery is claimed in the database (transcripts.clear_follow), which takes it off every session of that person:
two processes following the same task cannot both deliver it, and a later session does not replay it. A follower
that gives up (FOLLOW_MAX) takes it off as well. A new session picks a follow up again only when no hub of this
process follows it, never for a task that finished before the previous session ended while its follower lived, and
never RESUME_MAX_AGE after the task finished; it says "before the restart" only when the hub that followed it is gone.
When the person is live only on another process when the answer is ready, that process's follower (if it has one)
gets ELSEWHERE_GRACE seconds to claim it; if none did, the answer goes into the asking session's transcript (review1b
R3: it used to be left undelivered, and stayed awaited on the ended session).
A claim that cannot be made (the database stays locked past its wait, an I/O error) is not a claim (Wave 2 lane X,
review2 M1): nothing is delivered, the follow stays on the sessions, and the follower asks again after each pause
of CLAIM_WAITS (until FOLLOW_MAX), logging the task id and the error's type; it delivers only on a real count above
0, after reading the task again (a task forgotten meanwhile is never given). The answer is then late, never given
twice and never given after a forget.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import hashlib
import logging
import os
import queue
import re
import secrets
import threading
import time

from ..instance import store
from ..instance.errors import Conflict, InstanceError, NotFound, ValidationError
from ..resources import idle
from . import bridge as vbridge, commands as vcommands, config as vconfig, engines as vengines, selftest as vselftest, \
    state as vstate, transcripts as tx

log = logging.getLogger(__name__)

RATE = 16000
MAX_CHUNK = 64 * 1024
MAX_UTTERANCE_SECONDS = 30
MAX_UTTERANCE = RATE * 2 * MAX_UTTERANCE_SECONDS
PROVISIONAL_EVERY = 1.0
MIN_PROVISIONAL_SECONDS = 0.6
MIN_FINAL_SECONDS = 0.25
MAX_SESSIONS = 16
MAX_SESSIONS_PER_PRINCIPAL = 4
SESSION_IDLE = 120.0         # a session whose client stopped polling (closed tab, lost phone) ends after this
MAX_REPLY_WORKERS = 8        # concurrent bridge replies (each may run a local model call)
MAX_FOLLOWERS = 16           # research tasks followed at once
WRITE_QUEUE = 256            # pending transcript writes
BUSY = "NeuroSciFi is still answering earlier requests; try again in a moment."
MAX_EVENTS = 500
EVENT_WAIT_MAX = 20.0
HEALTH_TTL = 5.0
SWEEP_EVERY = 60.0
FOLLOW_POLL = 1.0            # longest wait between two checks of a followed research task
FOLLOW_FIRST = 0.1           # the first wait; it doubles up to FOLLOW_POLL (follow_waits)
FOLLOW_MAX = 3 * 3600.0
RESUME_MAX_AGE = 24 * 3600.0  # a task that finished longer ago than this is not picked up by a new session
ELSEWHERE_GRACE = 3 * FOLLOW_POLL  # how long another process's follower may claim an answer before this one does
# Wave 2 lane X (review2 M1): the pauses between attempts at a delivery claim that could not be made; the last one
# repeats until FOLLOW_MAX, so a database that stays unwritable costs one log line per pause, never a busy loop
CLAIM_WAITS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0)
_LIVE_HUBS: set[str] = set()  # the follow_by tokens of this process's hubs that are not closed
PROGRESS_SPEAK_EVERY = 30.0
TERMINAL = ("completed", "partial", "failed", "cancelled")
STAGE_WORDS = {"discovering": "searching for sources", "reading": "reading sources", "evaluating":
               "checking the evidence", "synthesizing": "writing the answer", "queued": "waiting for the worker"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def follow_waits():
    """The waits between checks of a followed research task: FOLLOW_FIRST, doubling up to FOLLOW_POLL. A task
    answered from knowledge or a quick lookup is reported within a fraction of a second instead of a whole poll;
    a long task is checked once per FOLLOW_POLL as before (about five extra reads in its first two seconds)."""
    wait = min(FOLLOW_FIRST, FOLLOW_POLL)
    while True:
        yield wait
        wait = min(FOLLOW_POLL, wait * 2)


def claim_waits():
    """The pauses before asking a delivery claim again (CLAIM_WAITS, read when the follower starts); the last one
    repeats."""
    waits = tuple(CLAIM_WAITS)
    yield from waits
    while True:
        yield waits[-1]


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
        self.asked = 0          # the session's user turns when this reply started speaking (Session.floor)


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
        self.pending: dict | None = None        # guarded by self.lock (request, reply and follow threads)
        self.finalised: deque = deque(maxlen=8)  # utterances whose final segment is queued (no provisional after)
        self.closed: deque = deque(maxlen=8)     # (utterance, seq) of final chunks received: a retry is a repeat
        self.reply_n = 0
        # Reply generations (guarded by self.lock): every user turn (a final utterance, a typed message) takes the
        # next `generation`, and its reply carries it. Replies run concurrently and finish in any order, so a reply
        # speaks only if its generation is at least `floor`, which rises to each delivered reply's generation and,
        # when the owner interrupts a reply, past every turn asked before that reply started speaking: an older,
        # slower answer is shown as text but never spoken over a newer one or after the owner cut it short.
        self.generation = 0
        self.floor = 0
        self.output_muted = False
        self.active = True
        self.seen = time.monotonic()


_STOP = object()             # queued behind the transcript writes by close(): the writer thread ends there


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
        self._replies = threading.BoundedSemaphore(MAX_REPLY_WORKERS)
        self._followers = threading.BoundedSemaphore(MAX_FOLLOWERS)
        self._following: dict[str, int] = {}  # task id -> followers of this hub watching it now (self.lock)
        # who follows a task (kept with it in the session context): this process, its start time, this hub
        from ..presence.activity import process_started
        self.token = f"{os.getpid()}:{process_started(os.getpid())}:{secrets.token_hex(6)}"
        _LIVE_HUBS.add(self.token)
        self._writes: queue.Queue = queue.Queue(WRITE_QUEUE)
        self._writer = None
        self._closed = threading.Event()      # close(): this hub's own threads end, and it starts none again
        self._inline = threading.Lock()       # after close: transcript writes made in the caller's thread, one at a time
        from pathlib import Path
        self.selftests = vselftest.SelfTests(Path(db_path).resolve().parent / "voice-selftest", clock)
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
        vstate.set_engines(result)     # what the owner's Presence reports as the voice condition (R2-08)
        return result

    def _sweep(self, db):
        if time.monotonic() - self._last_sweep >= SWEEP_EVERY:
            self._last_sweep = time.monotonic()
            tx.sweep(db, self.clock())

    def _write_later(self, job) -> bool:
        """Queue a transcript write for the writer thread (FIFO, so a session's writes keep their order). After
        close() the write is made at once in the calling thread: a reply that finishes after its server closed
        still has its transcript written, and no writer is started that nothing would stop (review F2).

        Whether the hub is closed, the start of a writer and the queueing of the job happen under one lock, the
        same lock close() takes to mark the hub closed: so every job queued before close() is ahead of the stop
        it sends, and no job can be queued behind it or start a second writer."""
        with self.lock:
            if not self._closed.is_set():
                if self._writer is None or not self._writer.is_alive():
                    self._writer = threading.Thread(target=self._write_loop, daemon=True, name="nsf-voice-writer")
                    self._writer.start()
                try:
                    self._writes.put_nowait(job)
                    return True
                except queue.Full:
                    return False
        with self._inline:                   # closed: one inline write at a time, as the writer did them
            try:
                job()
            except Exception:   # a forgotten or ended session: the write has nowhere to go
                pass
        return True

    def _write_loop(self):
        while True:
            job = self._writes.get()
            try:
                if job is _STOP:
                    return
                job()
            except Exception:   # a forgotten or ended session: the write has nowhere to go
                pass
            finally:
                self._writes.task_done()

    def drain(self):
        """Wait until every queued transcript write is done (tests, shutdown)."""
        self._writes.join()

    def close(self, timeout=5.0) -> None:
        """End this hub's own threads; called when the server that owns the hub closes. The transcript writer
        finishes what is queued and ends, and the threads that follow a research task stop looking: the task
        itself goes on (it belongs to the research system) and its answer is in the task. Before this a hub's
        threads could only end with the process: one writer, and up to MAX_FOLLOWERS followers that each looked for
        up to FOLLOW_MAX. Closing is final: a write that comes later is made in the thread that asks for it
        (_write_later), so nothing can start a writer again."""
        _LIVE_HUBS.discard(self.token)       # its followers stop: a task they awaited is "before the restart"
        with self.lock:                      # the lock of _write_later: nothing is queued behind the stop below
            self._closed.set()
            writer, self._writer = self._writer, None
        if writer is not None and writer.is_alive():
            try:
                self._writes.put(_STOP, timeout=timeout)      # behind everything already queued
            except queue.Full:
                return
            writer.join(timeout)

    def _emit(self, s: Session, etype, **data):
        with s.cond:
            s.seq += 1
            s.events.append({"seq": s.seq, "type": etype, "at": self.clock(), **data})
            s.cond.notify_all()

    def _state(self, s: Session, **changes):
        vstate.update(s.id, owner=s.principal.owner, **changes)

    def _used(self, sid, principal) -> Session:
        """session(), for a call that uses the session (audio, a typed turn, a capture or mode change, a reply
        fetched, an interruption, a confirmation): the resource policy counts an open session as in use only while
        such calls keep coming (voice.state.last_used). Polling events and status reports are not uses."""
        s = self.session(sid, principal)
        vstate.touch(s.id)
        return s

    def session(self, sid, principal) -> Session:
        sid = self.aliases.get(sid, sid) if isinstance(sid, str) else None
        s = self.sessions.get(sid) if sid else None
        # A Pocket voice session belongs to the device that started it, not to its whole family: its replies run as
        # that device (the bridge's research rules depend on which device it is, voice/bridge.py).
        if s is None or s.principal.name != principal.name or s.principal.node_device != principal.node_device \
                or not s.active:
            raise NotFound("voice session not found (start a new one)")
        s.seen = time.monotonic()
        return s

    # ---- sessions -----------------------------------------------------------------------------------------------
    def start(self, principal, device, label, mode=None) -> dict:
        if not isinstance(device, str) or not 4 <= len(device) <= 120:
            raise ValidationError("device must be an id of 4..120 characters")
        with self.db() as db:
            self._sweep(db)
            cfg = vconfig.effective(db, principal.name, principal.owner)
            mode = mode or cfg["mode"]
            if mode not in vconfig.MODES:
                raise ValidationError("mode must be one of " + ", ".join(vconfig.MODES))
            previous = tx.last_session(db, principal.name)
            sid = tx.start_session(db, principal.name, device, mode, self.clock())
            # R8: the task a spoken question's answer was still awaited for when the last session's process ended
            carried = ("last_task_id", "last_objective") + (("follow_task_id", "follow_by")
                                                            if cfg.get("resume_follow") else ())
            context = {k: v for k, v in ((previous or {}).get("context") or {}).items()
                       if k in carried and v is not None}
            if context:
                tx.update_session(db, sid, self.clock(), context=context)
        s = Session(sid, principal, device, label, mode)
        self._expire_idle()
        try:
            victims = self._admit(s)
        except Conflict:
            with self.db() as db:
                tx.update_session(db, sid, self.clock(), status="ended")
            raise
        for v in victims:
            self._end(v, "evicted")
        self._state(s, mode=mode, session_active=True, capture_device=None, mic_capturing=False, speaking=False,
                    user_speaking=False, interrupted_at=None)
        self._wake(s, mode)
        try:            # Wave 2 lane U: a failing pick-up of an awaited answer never stops a session from starting
            if self._resume_follow(s, context.get("follow_task_id"), context.get("follow_by"), previous) is not None:
                raise TypeError("the pick-up returned a value; it returns nothing")
        except Exception as exc:          # noqa: BLE001 - logged; the session starts without the pick-up
            log.warning("voice follow-up: picking up task %s failed (%s); the session starts without it",
                        context.get("follow_task_id"), type(exc).__name__)
        return self.public(s)

    def _resume_follow(self, s: Session, task_id, by=None, previous=None):
        """R8: follow again a research task whose spoken answer the principal's previous session was still waiting
        for (nothing in this hub follows it): its answer is delivered to the person's live session. Not for a task
        that finished before the previous session ended while the hub that followed it lived (it had its chance),
        nor RESUME_MAX_AGE after it finished; "before the restart" only when that hub is gone (review1 A9, S9)."""
        from ..webcortex import orchestrator
        if not task_id or not s.principal.research:
            return
        if not self._watch(task_id, unless_followed=True):
            return                   # this hub still follows it (a session that merely ended, not a restart)
        slot = started = False
        try:        # Wave 2 lane X (review2 L6): a failure part-way gives back the count and the slot taken here
            alive = _hub_alive(by)
            try:
                with self.db() as db:
                    task = orchestrator.get(db, task_id, family=s.principal.family)
            except InstanceError:
                task = None
            finished = task.get("finished_at") if task else None
            ended = (previous or {}).get("ended_at")
            stale = task is None or (task["status"] in TERMINAL and finished is not None and (
                self.clock() - finished > RESUME_MAX_AGE or (alive is not False and ended is not None
                                                              and finished < ended)))
            slot = not stale and self._followers.acquire(blocking=False)
            if not slot:                         # the count goes back below (finally)
                if stale:
                    with self.db() as db:
                        tx.clear_follow(db, s.principal.name, task_id)
                return
            self._emit(s, "notice", text="Picking up the research you asked about before the restart."
                       if alive is False else "Picking up the research you asked about earlier.")
            threading.Thread(target=self._follow, args=(s, task_id, True, True), daemon=True,
                             name="nsf-voice-follow").start()
            started = True
        finally:
            if not started:
                with self.lock:
                    left = self._following.get(task_id, 1) - 1
                    if left > 0:
                        self._following[task_id] = left
                    else:
                        self._following.pop(task_id, None)
                if slot:
                    self._followers.release()

    def _wake(self, s: Session, mode):
        """Load again the speech services this mode needs if the idle policy unloaded them; the client is told
        ("Starting speech recognition, about 17 s") while they start (resources/idle.py)."""
        needed = {"voice_text": ("asr", "tts"), "voice_in_text_out": ("asr",)}.get(mode, ())
        idle.wake(self.db_path, needed, purpose="voice session",
                  notify=lambda text: self._emit(s, "notice", text=text + "."))

    def _expire_idle(self):
        """End sessions whose client has not called for SESSION_IDLE seconds (every open client long-polls events
        at least every 20 s, so only closed tabs and vanished devices go)."""
        cutoff = time.monotonic() - SESSION_IDLE
        with self.lock:
            idle = [x for x in self.sessions.values() if x.seen < cutoff]
        for x in idle:
            self._end(x, "expired")

    def _admit(self, s: Session) -> list:
        """Register a new session; returns the sessions it displaces. A principal displaces its own oldest sessions
        beyond MAX_SESSIONS_PER_PRINCIPAL. When the hub is full, the owner displaces the oldest Pocket session (then
        its own); a Pocket family displaces only its own oldest session, never another family's or the owner's
        (with none of its own, it is refused until a session ends or expires)."""
        by_age = (lambda xs: sorted(xs, key=lambda x: x.seen))
        with self.lock:
            live = list(self.sessions.values())
            victims = by_age(x for x in live if x.principal.name == s.principal.name)
            victims = victims[:max(0, len(victims) - MAX_SESSIONS_PER_PRINCIPAL + 1)]
            rest = [x for x in live if x not in victims]
            over = len(rest) - MAX_SESSIONS + 1
            if over > 0:
                if s.principal.owner:
                    pool = by_age(x for x in rest if not x.principal.owner) + by_age(x for x in rest
                                                                                    if x.principal.owner)
                else:
                    pool = by_age(x for x in rest if x.principal.name == s.principal.name)
                if len(pool) < over:
                    raise Conflict("too many voice sessions are open; end one first")
                victims += pool[:over]
            for v in victims:
                self.sessions.pop(v.id, None)
            self.sessions[s.id] = s
        return victims

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
        idle.mark_used(self.db_path, ("asr", "tts"))      # the idle timeout counts from the session's end
        vstate.end(s.id)
        with self.lock:
            self.sessions.pop(s.id, None)

    def public(self, s: Session) -> dict:
        with self.db() as db:
            row = tx.session(db, s.id)
            cfg = vconfig.get(db)
            capture = tx.holder(db, self.clock())
            request = tx.handoff_request(db, self.clock(), holder_device=s.device)
        with s.lock:
            pending = s.pending
        return {"session": s.id, "mode": s.mode, "device": s.device, "context": row["context"],
                "consented": s.device in cfg["consent"], "capture": self._capture_public(capture, s),
                "pending": None if pending is None else {k: pending[k] for k in ("id", "summary", "expires_at")},
                "handoff_request": _request_public(request), "events_seq": s.seq}

    def _capture_public(self, holder, s=None, principal=None):
        """The lease as one principal may see it: another principal's holder stays anonymous to a Pocket device."""
        if holder is None:
            return {"held": False, "this_device": False}
        principal = principal or (s.principal if s else None)
        if principal is not None and not principal.owner and holder["principal"] != principal.name:
            return {"held": True, "this_device": False, "label": "another device"}
        return {"held": True, "this_device": bool(s and holder["device"] == s.device), "label": holder["label"],
                "expires_in": round(holder["expires_at"] - self.clock(), 1)}

    def status(self, principal, sid=None, *, probe=False) -> dict:
        """Settings, engines and the lease as `principal` may see them. A Pocket principal gets its own session's
        state only: no owner snapshot, no hub metrics, no retained-audio count, no other principal's holder label."""
        self._expire_idle()
        with self.db() as db:
            self._sweep(db)
            cfg = vconfig.effective(db, principal.name, principal.owner)
            capture = tx.holder(db, self.clock())
            retained = tx.audio_count(db) if principal.owner else None
        s = self.sessions.get(self.aliases.get(sid, sid)) if isinstance(sid, str) else None
        if s is not None and s.principal.name != principal.name:
            s = None
        health = self.health(force=probe)
        m = self.metrics
        avg = (lambda xs: round(sum(xs) / len(xs), 1) if xs else None)
        snapshot = vstate.snapshot() if principal.owner else vstate.get(s.id) if s else vstate.get("")
        metrics = None
        if principal.owner:
            metrics = {"utterances": m["utterances"], "asr_ms_avg": avg(m["asr_ms"]),
                       "reply_ms_avg": avg(m["reply_ms"]), "first_sentence_ms_avg": avg(m["first_sentence_ms"]),
                       "barge_ins": m["barge_ins"], "overlaps": m["overlaps"], "refused_audio": m["refused_audio"],
                       "stop_latency_ms_avg": avg(m["stop_latency_ms"])}
        return {"settings": vconfig.public(cfg), "voices": list(vconfig.VOICES), "modes": list(vconfig.MODES),
                "engines": health, "capture": self._capture_public(capture, s, principal),
                "retained_audio_segments": retained, "session": self.public(s) if s else None, "snapshot": snapshot,
                "available": {"voice_in": health["asr"]["state"] == "ready",
                              "voice_out": health["tts"]["state"] == "ready", "text": True},
                "metrics": metrics,
                "principal": {"owner": principal.owner, "research": principal.research, "label": principal.label}}

    # ---- settings, consent, mode ------------------------------------------------------------------------------
    def settings(self, principal, changes) -> dict:
        """The owner changes the individual's settings; a Pocket principal only its own preferences."""
        with self.db() as db:
            if not principal.owner:
                return vconfig.public(vconfig.update_pref(db, principal.name, changes))
            cfg = vconfig.update(db, changes, owner=True)
            if not cfg["retain_audio"]:
                tx.sweep(db, self.clock())
        self._health = (0.0, None)
        return vconfig.public(cfg)

    def consent(self, sid, principal, accept: bool) -> dict:
        s = self._used(sid, principal)
        with self.db() as db:
            vconfig.record_consent(db, s.device, self.clock(), bool(accept))
            if not accept and tx.holds(db, s.device, self.clock()):
                tx.release(db, s.device)
        if not accept:
            self._state(s, mic_capturing=False, capture_device=None)
        return {"consented": bool(accept)}

    def set_mode(self, sid, principal, mode) -> dict:
        s = self._used(sid, principal)
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
            if s.principal.owner:                            # the preference, not a permission
                vconfig.update(db, {"mode": mode}, owner=True)
            else:
                vconfig.update_pref(db, s.principal.name, {"mode": mode})
            if mode == "text_only" and tx.holds(db, s.device, self.clock()):
                tx.release(db, s.device)
        changes = {"mode": mode}
        if mode == "text_only":
            changes.update(mic_capturing=False, user_speaking=False, capture_device=None, speaking=False)
        elif mode == "voice_in_text_out":
            changes.update(speaking=False)
        self._state(s, **changes)
        self._emit(s, "mode", mode=mode)
        self._wake(s, mode)

    # ---- capture ownership --------------------------------------------------------------------------------------
    def capture(self, sid, principal, action, *, handoff_confirm=None) -> dict:
        s = self._used(sid, principal)
        now = self.clock()
        with self.db() as db:
            if action in ("acquire", "handoff"):
                if s.mode == "text_only":
                    raise Conflict("text-only mode: the microphone is off (switch mode first)")
                if s.device not in vconfig.get(db)["consent"]:
                    raise Conflict("consent required before the first capture on this device")
                handoff = False
                if action == "handoff":
                    with s.lock:
                        p = s.pending
                        if not (p and p["action"] == "handoff" and p["id"] == handoff_confirm
                                and p["expires_at"] > now):
                            raise Conflict("hand-off needs a confirmation (ask for it, then confirm)")
                        s.pending = None
                    if not self._may_take(tx.holder(db, now), s):
                        raise Conflict("the device that holds the microphone must allow the hand-off")
                    handoff = True
                out = tx.acquire(db, s.device, s.label, principal.name, s.id, now, handoff=handoff)
                if out["granted"]:
                    self._state(s, capture_device=s.label)
                elif not principal.owner:                  # another principal's holder stays anonymous
                    holder = tx.holder(db, now)
                    if holder is not None and holder["principal"] != principal.name:
                        out["holder"] = {"label": "another device"}
                return out
            if action == "renew":
                ok = tx.renew(db, s.device, now)
                if not ok:
                    self._state(s, mic_capturing=False, capture_device=None)
                    with s.lock:
                        s.utterances.clear()
                request = tx.handoff_request(db, now, holder_device=s.device) if ok else None
                return {"granted": ok, "holder": self._capture_public(tx.holder(db, now), s),
                        "handoff_request": _request_public(request)}
            if action == "request_handoff":
                holder = tx.holder(db, now)
                if holder is not None and holder["device"] != s.device and not self._may_take(holder, s):
                    req = tx.request_handoff(db, device=s.device, label=s.label, principal=principal.name,
                                             session_id=s.id, holder_device=holder["device"], now=now)
                    self._emit(s, "notice", text="Asked the device that holds the microphone to hand it over.")
                    return {"requested": {"id": req["id"], "expires_at": req["expires_at"]}}
                pending = self.bridge._confirm("handoff", "handoff", {}, "Move the microphone to this device? The "
                                                                         "other device stops listening.").confirm
                with s.lock:
                    s.pending = pending
                self._emit(s, "confirm", confirm={k: pending[k] for k in ("id", "action", "summary", "expires_at")})
                return {"confirm": {k: pending[k] for k in ("id", "action", "summary", "expires_at")}}
            if action in ("grant_handoff", "refuse_handoff"):
                req = tx.answer_handoff(db, handoff_confirm, s.device, now, action == "grant_handoff")
                if req is None:
                    raise Conflict("no such hand-off request (it may have expired)")
                if action == "refuse_handoff":
                    other = self.sessions.get(req["to_session"])
                    if other is not None:   # tell the requester now, not when its own wait runs out
                        self._emit(other, "notice", handoff="refused",
                                   text="The device that holds the microphone did not hand it over.")
                    return {"refused": True}
                with s.lock:
                    s.utterances.clear()
                self._state(s, mic_capturing=False, user_speaking=False, capture_device=None)
                other = self.sessions.get(req["to_session"])
                if other is not None:
                    self._state(other, capture_device=other.label)
                    self._emit(other, "notice", text="The other device handed the microphone to this one.")
                return {"granted": False, "handed_off": True}
            if action == "release":
                released = tx.release(db, s.device)
                with s.lock:
                    s.utterances.clear()
                self._state(s, mic_capturing=False, user_speaking=False, capture_device=None)
                return {"released": released}
        raise ValidationError("action must be acquire, renew, release, request_handoff, handoff, grant_handoff or "
                              "refuse_handoff")

    @staticmethod
    def _may_take(holder, s: Session) -> bool:
        """A device takes the lease on its own confirmation from nobody, itself, its own principal, or (the owner)
        from anyone; otherwise only the holder can hand it over."""
        return (holder is None or holder["device"] == s.device or s.principal.owner
                or holder["principal"] == s.principal.name)

    # ---- audio in -------------------------------------------------------------------------------------------------
    def audio(self, sid, principal, utterance, seq, final, pcm: bytes, *, started_at=None) -> dict:
        s = self._used(sid, principal)
        now = self.clock()
        if not isinstance(utterance, str) or not ID.match(utterance):
            raise ValidationError("utterance id must match [A-Za-z0-9_-]{4,64}")
        if len(pcm) % 2 or len(pcm) > MAX_CHUNK:
            raise ValidationError(f"audio chunk must be 16-bit PCM of at most {MAX_CHUNK} bytes")
        if s.mode == "text_only":
            self.metrics["refused_audio"] += 1
            raise Conflict("text-only mode: audio is not accepted")
        with self.db() as db:      # reads only: a chunk never waits for a write transaction
            if s.device not in vconfig.get(db)["consent"]:
                self.metrics["refused_audio"] += 1
                raise Conflict("consent required before capture")
            if not tx.holds(db, s.device, now):
                self.metrics["refused_audio"] += 1
                with s.lock:
                    s.utterances.clear()
                raise Conflict("this device does not hold the microphone (another device has it, or it was "
                               "released)")
        with s.lock:
            u = s.utterances.get(utterance)
            # A client retries a chunk whose response it lost: a repeat of the last chunk (or of the final chunk of
            # an utterance already closed) is acknowledged and not added twice; any other gap is still refused.
            if u is not None and seq == u["seq"] >= 0 and hashlib.sha256(pcm).digest() != u.get("last"):
                raise Conflict(f"chunk {seq} was already received with other audio")     # a retry sends the same bytes
            if (u is None and (utterance, seq) in s.closed) or (u is not None and seq == u["seq"] >= 0):
                return {"status": "duplicate", "seq": seq}
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
            u["last"] = hashlib.sha256(pcm).digest()      # of the chunk as sent, to recognise its retry
            u["pcm"] += pcm
            u["seq"] = seq
            buf = bytes(u["pcm"])
            due = (time.monotonic() - u["last_prov"] >= PROVISIONAL_EVERY and
                   len(buf) >= MIN_PROVISIONAL_SECONDS * RATE * 2)
            if final:
                s.utterances.pop(utterance, None)
                s.closed.append((utterance, seq))
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
        now, text = self.clock(), result["text"]

        def persist():
            with s.lock:
                if utterance in s.finalised:      # the final segment is already queued or written
                    return
            with self.db() as db:
                seg = tx.upsert(db, s.id, utterance, speaker="user", source="asr", status="provisional", text=text,
                                now=now, started_at=started)
            self._emit(s, "transcript", segment=_seg(seg))
        self._write_later(persist)              # a provisional segment that cannot be queued is simply skipped
        return {"status": "provisional", "text": text}

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
        with s.lock:
            s.finalised.append(utterance)
        if not text or _noise(text):
            def drop():
                with self.db() as db:
                    with store.write(db):
                        db.execute("DELETE FROM voice_segments WHERE session_id=? AND utterance=?", (s.id, utterance))
            self._write_later(drop)
            self._emit(s, "transcript_dropped", utterance=utterance)
            return {"status": "empty"}
        now = self.clock()
        with self.db() as db:
            cfg = vconfig.get(db)
        ttl = cfg["audio_ttl_hours"] if cfg["retain_audio"] else None
        detail = {"asr_ms": round(result["latency"] * 1000, 1), "audio_seconds": round(result["audio_seconds"], 2)}
        replying = self._replies.acquire(blocking=False)
        gen = self._next_generation(s)

        def persist():
            try:
                with self.db() as db:
                    seg = tx.finalize(db, s.id, utterance, text=text, now=now, started_at=started, detail=detail,
                                      pcm=buf if ttl else None, rate=RATE, ttl_hours=ttl)
                self._emit(s, "transcript", segment=_seg(seg))
            finally:
                if replying:
                    threading.Thread(target=self._respond, args=(s, utterance, text, received, True, gen),
                                     daemon=True, name="nsf-voice-bridge").start()
        if not self._write_later(persist):
            if replying:
                self._replies.release()
            self._emit(s, "error", what="busy", code="busy", message=BUSY)
            return {"status": "busy", "text": text}
        self.metrics["utterances"] += 1
        if not replying:
            self._emit(s, "error", what="busy", code="busy", message=BUSY)
        return {"status": "final" if replying else "busy", "text": text, "asr_ms": round(result["latency"] * 1000, 1)}

    def text(self, sid, principal, text) -> dict:
        s = self._used(sid, principal)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValidationError("text must be 1..2000 characters")
        if not self._replies.acquire(blocking=False):
            raise Conflict("busy: " + BUSY)
        utterance = "t" + secrets.token_hex(6)
        try:
            with self.db() as db:
                seg = tx.upsert(db, s.id, utterance, speaker="user", source="typed", status="final",
                                text=" ".join(text.split()), now=self.clock(), ended_at=self.clock())
            self._emit(s, "transcript", segment=_seg(seg))
            threading.Thread(target=self._respond, args=(s, utterance, text, time.monotonic(), True,
                                                         self._next_generation(s)),
                             daemon=True, name="nsf-voice-bridge").start()
        except BaseException:
            self._replies.release()
            raise
        return {"status": "final", "segment": seg["id"]}

    # ---- the owner's device self-test (voice/selftest.py) ---------------------------------------------------------
    def selftest(self, principal, action, body) -> dict:
        if action == "start":
            return self.selftests.start(principal, body.get("surface") or ("console" if principal.owner else "pocket"))
        if action == "result":
            return self.selftests.result(principal, body.get("run"), body.get("step"), body.get("data"))
        if action == "finish":
            return self.selftests.finish(principal, body.get("run"), body.get("data"))
        raise ValidationError("action must be start, result or finish")

    def selftest_audio(self, principal, run, index, seq, final, pcm) -> dict:
        try:
            return self.selftests.audio(principal, run, index, seq, final, pcm, self.engines().asr)
        except vengines.EngineError as err:
            return {"status": "asr_failed", "error": err.code}

    def selftest_speech(self, principal, run, n) -> bytes:
        self.selftests.run(principal, run)
        if not isinstance(n, int) or not 0 <= n < len(vselftest.PHRASES):
            raise NotFound("no such test phrase")
        with self.db() as db:
            cfg = vconfig.effective(db, principal.name, principal.owner)
        try:
            return self.engines().tts.synthesize(vselftest.PHRASES[n], cfg["voice"], cfg["speed"])["wav"]
        except vengines.EngineError as err:
            raise Conflict(f"speech output is unavailable ({err.code})") from None

    # ---- replies ----------------------------------------------------------------------------------------------------
    @staticmethod
    def _next_generation(s: Session) -> int:
        """A new user turn: the generation its reply carries (Session.floor)."""
        with s.lock:
            s.generation += 1
            return s.generation

    def _respond(self, s: Session, utterance, text, received, slot=False, gen=None):
        try:
            with s.lock:      # a spoken confirm/deny claims the pending confirmation: it runs at most once
                pending = s.pending if s.pending and s.pending["expires_at"] > self.clock() else None
                if pending is not None and vcommands.parse(text).kind in ("confirm", "deny"):
                    s.pending = None
            from ..presence import activity     # Presence: the request is being interpreted (ADR-053)
            try:
                with self.db() as db, activity.track("interpreting", subsystem="voice", purpose="voice request",
                                                     priority="communication"):
                    ctx = tx.session(db, s.id)["context"]
                    reply = self.bridge.handle(db, s.principal, ctx, text, pending)
            except Exception as exc:   # the owner hears that something failed, never silence
                reply = vbridge.Reply("error", speech=["Sorry, something went wrong handling that."],
                                      text=f"Error: {type(exc).__name__}")
            self.metrics["reply_ms"].append((time.monotonic() - received) * 1000)
            self._deliver(s, reply, responding_to=utterance, received=received, gen=gen)
        finally:
            if slot:
                self._replies.release()

    def _deliver(self, s: Session, reply, *, responding_to=None, received=None, gen=None):
        """Record, show and (mode A) speak a reply. `gen`: the generation of the user turn it answers (None for
        replies no user turn waits for: research results, progress, confirmations); a stale one is not spoken."""
        with s.lock:
            s.reply_n += 1
            rid = f"r{s.reply_n}-{secrets.token_hex(3)}"
            stale = gen is not None and gen < s.floor
            if gen is not None:
                s.floor = max(s.floor, gen)
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
                with s.lock:
                    s.pending = None
            elif kind == "stop_speaking":
                self._stop_turns(s, "stop_command")
            elif kind == "mode":
                actions_after.append(action)
            elif kind == "handoff":
                with self.db() as db:
                    now = self.clock()
                    holder = tx.holder(db, now)
                    if self._may_take(holder, s):
                        out = tx.acquire(db, s.device, s.label, s.principal.name, s.id, now, handoff=True)
                    else:       # only the holder may hand it over: ask it instead of taking it
                        tx.request_handoff(db, device=s.device, label=s.label, principal=s.principal.name,
                                           session_id=s.id, holder_device=holder["device"], now=now)
                        out = {"granted": False}
                        reply.speech = ["I asked the device that has the microphone to hand it over."]
                        reply.text = reply.speech[0]
                if out["granted"]:
                    self._state(s, capture_device=s.label)
            elif kind in ("forget_conversation", "resume_speaking"):
                actions_after.append(action)
        if reply.confirm:
            with s.lock:
                s.pending = reply.confirm
        speak = bool(reply.speech) and s.mode == "voice_text" and not s.output_muted and s.active and not stale
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
                if gen is not None and gen < s.floor:      # a newer reply or an interruption got here meanwhile
                    turn = None
                else:
                    turn.asked = s.generation
                    s.turns[rid] = turn
                    s.last_turn = turn
                    for old in list(s.turns.values()):
                        if old is not turn and old.status == "speaking":
                            self._cancel_turn(old, "superseded")
                    while len(s.turns) > 8:
                        s.turns.pop(next(iter(s.turns)))
            if turn is not None:
                threading.Thread(target=self._synthesize, args=(s, turn, received), daemon=True,
                                 name="nsf-voice-tts").start()
        # R8: the follow is kept with the session BEFORE the reply that promises it is shown (Wave 2 lane X: it used
        # to be written after, so a process that ended right after the acknowledgement lost the follow); the
        # follower starts after the reply, so its events come after it
        follow = reply.follow if reply.follow and s.principal.research else None
        slot = kept = False
        if follow:
            slot = self._followers.acquire(blocking=False)
            if slot:
                with self.db() as db:      # R8: kept with the session, so a restarted process picks it up again
                    try:
                        if vconfig.get(db).get("resume_follow"):     # off: nothing is written (review1 A15)
                            tx.update_session(db, s.id, self.clock(), context={"follow_task_id": follow,
                                                                              "follow_by": self.token})
                            kept = True
                    except (NotFound, InstanceError):
                        pass
                self._watch(follow)
        self._emit(s, "reply", reply=reply.public(), segment=_seg(seg) if seg else None,
                   turn={"id": turn.id, "sentences": len(turn.sentences)} if turn else None)
        for action in actions_after:
            if action["kind"] == "mode":
                self._apply_mode(s, action["mode"])
            elif action["kind"] == "forget_conversation":
                with self.db() as db:
                    forgot = tx.forget_session(db, s.id, now=self.clock(), rewrite=False)
                    sid = tx.start_session(db, s.principal.name, s.device, s.mode, self.clock())
                self._rewrite_checkpoints(forgot)
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
        if follow:
            if slot:
                threading.Thread(target=self._follow, args=(s, follow, True, kept), daemon=True,
                                 name="nsf-voice-follow").start()
            else:
                self._emit(s, "notice", text="The research continues, but I can't follow more tasks right now; ask "
                                             "'show the evidence' when it is done.")

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
        s = self._used(sid, principal)
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
        s = self._used(sid, principal)
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
        with s.lock:      # answers to turns asked before this reply started speaking are stale now (Session.floor)
            s.floor = max(s.floor, turn.asked + 1)
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
        s = self._used(sid, principal)
        with s.lock:          # claim it: two concurrent confirms never both execute
            pending = s.pending
            if not pending or pending["id"] != confirm_id or pending["expires_at"] <= self.clock():
                if pending and pending["expires_at"] <= self.clock():
                    s.pending = None
                raise Conflict("no such confirmation (it may have expired)")
            s.pending = None
        if not accept:
            self._deliver(s, vbridge.Reply("deny", speech=["Okay, I won't do that."],
                                           text=f"Not done: {pending['summary']}"))
            return {"done": False}
        with self.db() as db:
            ctx = tx.session(db, s.id)["context"]
            reply = self.bridge.execute(db, principal, ctx, pending)
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
        if changes.get("user_speaking"):
            # Only the device that holds the microphone can hear the user. Since Pocket sessions reach the audio
            # beacon, a text-only session reporting "user speaking" would keep background research deferred.
            with self.db() as db:
                if s.mode == "text_only" or not tx.holds(db, s.device, self.clock()):
                    changes["user_speaking"] = False
        if "output_muted" in changes:
            s.output_muted = changes["output_muted"]
            if s.output_muted:
                changes["speaking"] = False
        if changes.get("speaking") and (s.mode != "voice_text" or s.output_muted):
            changes["speaking"] = False
        return {k: v for k, v in self._apply_status(s, changes).items()}

    def _apply_status(self, s, changes):
        return vstate.update(s.id, owner=s.principal.owner, **changes)

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
        s.seen = time.monotonic()           # a client still waiting on events is alive
        return {"events": items, "seq": s.seq, "active": s.active}

    # ---- long tasks ---------------------------------------------------------------------------------------------------
    def _follow(self, s: Session, task_id, slot=False, kept=False):
        """Report truthful progress of a research task and deliver its answer. The task belongs to the research
        system: it keeps running when the voice session ends (only 'cancel the research' stops it); its answer
        is still written to the transcript. kept: the session context records the follow (resume_follow), so the
        delivery is claimed there first."""
        try:
            self._follow_task(s, task_id, kept)
        finally:
            with self.lock:          # counted by the caller before the thread started (_watch)
                left = self._following.get(task_id, 1) - 1
                if left > 0:
                    self._following[task_id] = left
                else:
                    self._following.pop(task_id, None)
            if slot:
                self._followers.release()

    def _watch(self, task_id, *, unless_followed=False) -> bool:
        """Count one more follower of `task_id` before its thread starts; with unless_followed, refuse (False) when
        this hub already follows it. Under self.lock, so a session starting meanwhile sees it."""
        with self.lock:
            if unless_followed and self._following.get(task_id):
                return False
            self._following[task_id] = self._following.get(task_id, 0) + 1
            return True

    def _live(self, s: Session) -> Session | None:
        """The person's session to tell now: `s` while it is open, else the newest open session of this hub for the
        same principal and device (a Pocket session belongs to its device), else None."""
        if s.active:
            return s
        with self.lock:
            open_ = [x for x in self.sessions.values() if x.active and x.principal.name == s.principal.name
                     and x.principal.node_device == s.principal.node_device and x.principal.family == s.principal.family]
        return max(open_, key=lambda x: x.seen) if open_ else None

    def _follow_task(self, s: Session, task_id, kept=False):
        from ..webcortex import orchestrator
        started = time.monotonic()
        last_stage, last_spoken = None, time.monotonic()
        waits = follow_waits()
        pauses, graced = claim_waits(), False
        while time.monotonic() - started < FOLLOW_MAX:
            try:
                with self.db() as db:
                    task = orchestrator.get(db, task_id, family=s.principal.family)
            except InstanceError:
                return
            live = self._live(s)
            if task["status"] in TERMINAL:
                if kept:
                    if not graced:
                        graced = True
                        with self.db() as db:
                            elsewhere = live is None and tx.active_elsewhere(db, s.principal.name,
                                                                             list(self.sessions))
                        if elsewhere:        # the person is on another process now: its follower, if it has one,
                            if self._closed.wait(ELSEWHERE_GRACE):     # tells them; if none claims it, this one
                                return       # writes it into the asking transcript (review1b R3)
                            live = self._live(s)
                    claimed = self._claim(s, task_id)
                    if claimed is None:      # could not ask is not a yes (Wave 2 lane X, review2 M1): nothing is
                        pause = next(pauses)  # delivered and the follow stays; the claim is asked again later
                        log.warning("voice follow-up: the delivery claim of task %s could not be made; asked again "
                                    "in %.1f s, nothing delivered meanwhile", task_id, pause)
                        if self._closed.wait(pause):
                            return
                        continue
                    if not claimed:
                        return               # delivered by another follower, or forgotten meanwhile
                    try:                     # claimed: read it again, so a task forgotten meanwhile is not given
                        with self.db() as db:
                            task = orchestrator.get(db, task_id, family=s.principal.family)
                    except InstanceError:
                        return
                target = live or s           # no open session: the answer still goes into the asking transcript
                reply = vbridge.compose_answer(task)
                reply.actions.append({"kind": "context", "last_task_id": task_id, "follow_task_id": None})
                self._emit(target, "task_done", task_id=task_id, status=task["status"])
                self._deliver(target, reply)
                return
            stage = task.get("stage") or task["status"]
            target = live or s
            if stage != last_stage:
                last_stage = stage
                words = STAGE_WORDS.get(stage, stage)
                self._emit(target, "progress", task_id=task_id, stage=stage, text=f"Research: {words}",
                           elapsed=round(time.monotonic() - started, 1))
                if target.active and time.monotonic() - last_spoken >= PROGRESS_SPEAK_EVERY \
                        and target.mode == "voice_text" and not any(t.status == "speaking"
                                                                    for t in target.turns.values()):
                    last_spoken = time.monotonic()
                    self._deliver(target, vbridge.Reply("progress", speech=[f"Still working: {words}."],
                                                        text=f"Still working: {words}.", task_id=None))
            if self._closed.wait(next(waits)):       # the hub closed: nobody is left to tell
                return
        if kept:                                     # given up: no later session picks it up (review1 S9)
            with self.db() as db:
                tx.clear_follow(db, s.principal.name, task_id)

    def _claim(self, s: Session, task_id) -> int | None:
        """One attempt at the delivery claim (transcripts.clear_follow): how many sessions awaited the answer, or
        None when it could not be made (it raised, timed out, or returned something that is not a count). A failure
        is logged with the task id and the exception's type only."""
        try:
            with self.db() as db:
                claimed = tx.clear_follow(db, s.principal.name, task_id)
        except Exception as exc:         # noqa: BLE001 - logged; the caller asks again later and delivers nothing
            log.warning("voice follow-up: the delivery claim of task %s failed (%s)", task_id, type(exc).__name__)
            return None
        if isinstance(claimed, int) and not isinstance(claimed, bool):
            return claimed
        log.warning("voice follow-up: the delivery claim of task %s returned %s, not a count", task_id,
                    type(claimed).__name__)
        return None

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
            out = tx.forget_session(db, session_id, principal=None if principal.owner else principal.name,
                                    now=self.clock(), rewrite=False)
        s = self.sessions.get(session_id)
        if s is not None:
            self._end(s, "forgotten")
        return self._rewrite_checkpoints(out)

    def _rewrite_checkpoints(self, out) -> dict:
        """Rewrite, in the background, the checkpoint archives a forget made stale (a large archive takes seconds to
        minutes; the voice turn and the HTTP request do not wait). A killed rewrite leaves each archive whole."""
        stale = out.get("checkpoints_stale") or []
        if stale:
            def run():
                from ..instance import checkpoints
                with self.db() as db:
                    checkpoints.after_forget(db, self.clock())
            threading.Thread(target=run, daemon=True, name="nsf-checkpoint-rewrite").start()
            out = {k: v for k, v in out.items() if k != "next_steps"} | {"checkpoints_rewriting": stale}
        return out


def _hub_alive(token) -> bool | None:
    """Whether the hub that recorded `token` (VoiceHub.token: pid, process start, hub) still follows: True for an
    open hub of this process or a live other process, False when its process ended or the hub closed, None when the
    token is missing or unreadable (a context written before follow_by existed)."""
    from ..presence.activity import process_started
    try:
        pid, started, _ = str(token).split(":")
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if str(process_started(pid)) != started:
        return False                 # that process is gone (or its pid now names another)
    if pid == os.getpid():
        return token in _LIVE_HUBS
    return True


_NOISE = {"you", "thank you", "thanks for watching", "bye", "uh", "um", "hmm", "mm"}


def _noise(text):
    """Whisper's well-known outputs for silence or noise; not an utterance."""
    norm = re.sub(r"[^a-z ]+", "", text.lower()).strip()
    return not norm or norm in _NOISE


def _request_public(req):
    if not req:
        return None
    return {"id": req["id"], "summary": "Another device asks for the microphone. Hand it over?",
            "expires_at": req["expires_at"]}


def _seg(seg):
    if not seg:
        return None
    return {k: seg.get(k) for k in ("id", "session_id", "utterance", "seq", "speaker", "source", "status", "authority",
                                    "text", "corrected_text", "display_text", "label", "task_id", "started_at",
                                    "ended_at", "created_at", "updated_at", "detail")}
