# Streaming, barge-in, transcripts and capture ownership (Voice Cortex, v0.7.0)

Code: `src/neuroscifi/voice/web/{capture-worklet,voice-client,voice-ui,pocket-voice}.mjs` (browser),
`src/neuroscifi/voice/{hub,transcripts,state,http}.py` (server). Overview: `COGNITIVE_VOICE_BRIDGE.md`.

## 1. Capture and upload (client)

1. Mic on requires: a session, a voice mode (A or B), recorded consent for this device, the server's capture lease
   (`POST capture {action: acquire}`), and a secure context (Pocket over the LAN is HTTPS; the bridge refuses LAN
   without a certificate). Then `getUserMedia({audio: {echoCancellation, noiseSuppression, autoGainControl,
   channelCount: 1, deviceId?}})`.
2. `capture-worklet.mjs` (AudioWorklet) resamples to 16 kHz mono and posts 20 ms PCM16 frames with their RMS. It
   sends nothing over the network.
3. VAD (`voice-client.mjs`): adaptive noise floor (EMA of non-speech RMS); voiced = RMS > max(3 × floor, 0.012);
   while NeuroSciFi is speaking the threshold is max(5 × floor, 0.03). 300 ms pre-roll. An utterance is
   **confirmed** after 250 ms voiced (300 ms while speaking); nothing is uploaded before that. Chunks go every
   500 ms (`final=0`), serialised per utterance (`X-NSF-Seq` must increase by one), and the rest at end-of-utterance
   = 700 ms silence (`final=1`).
4. Mic off: every `MediaStreamTrack` is stopped (`tracksLive() == 0`, the browser indicator goes out), the worklet
   and source are disconnected, frames still in flight are dropped, queued non-final uploads are skipped, and the
   lease is released. The server refuses audio from a device that does not hold the lease, has no consent, or is in
   text-only mode (409), so a buggy or stale client cannot keep streaming.
5. The lease (20 s) is renewed every 5 s; a refused renewal (another device took it) turns the mic off with "The
   microphone moved to …". Pocket turns the mic off when the page goes to the background (`visibilitychange`), and
   every client on `pagehide`.

## 2. Server side of an utterance (`hub.py`)

- Audio is kept in memory only, per utterance, at most 30 s (960 000 bytes; longer is closed as final), at most two
  open utterances per session, dropped on mic release, mode C, session end and lease loss.
- Provisional: at most once per second and only with ≥ 0.6 s of audio, the whole open utterance is transcribed and
  the user segment is upserted with `status=provisional` (event `transcript`).
- Final: one transcription of the whole utterance; Whisper's silence/noise outputs ("you", "thank you", …) and
  < 0.25 s are dropped; the segment becomes `final`; raw audio is stored **only** if retention is on (§4); the
  bridge runs in a worker thread so the upload returns with the transcript at once.

## 3. Replies, sentence streaming and barge-in

- A reply is stored as an authoritative `neuroscifi` segment (full text, label, sources, `responding_to`). In mode A
  (not muted) its spoken part is split into sentences; a TTS thread synthesises them in order while the client
  fetches sentence 0 and plays it, then prefetches n+1 while n plays (WebAudio, scheduled back to back).
- **Barge-in**: while playback runs, voiced speech above the barge threshold for 300 ms stops every playing source
  immediately in the client (no round trip), then `POST interrupt {kind: barge_in, turn, sentence, stop_ms}`. The
  server cancels the rest of the turn, marks the segment `interrupted` with `detail.spoken_until`, sets
  `interrupted_at`, and the utterance continues to ASR as a normal turn (e.g. "Please stop." → stop command).
- **Overlap / backchannel**: voiced sound that ends before 300 ms (or stays under the barge threshold) does not
  interrupt; it is counted (`interrupt {kind: overlap}`) and nothing is uploaded.
- **Recovery**: "go on" re-speaks the interrupted reply from `spoken_until`; a new question supersedes it. Research
  is never cancelled by barge-in.
- Events reach the client by long-poll (`events?since=n&wait≤20 s`; Pocket ≤ 7 s, its connection deadline), at most
  500 per session in memory.

## 4. Transcripts (instance database, schema v9)

Forward-only migration 9 (`instance/schema.py`; v8 is Lane D's knowledge-evolution migration):

| Table | Content |
|---|---|
| `voice_sessions` | id, principal (`owner` or `pocket:<salted family pseudonym>`), device, mode, status, context JSON (`last_task_id`, `last_objective`), started/ended/updated |
| `voice_segments` | one per utterance or reply: `seq` (order within the session), speaker `user`/`neuroscifi`, source `asr`/`typed`/`model`/`template`, status `provisional`/`final`/`interrupted`, authority `approximate`/`user_typed`/`authoritative`, text, `corrected_text` + `corrected_at`, detail JSON, task id, `started_at`/`ended_at` (speech) and `created_at`/`updated_at` (row) |
| `voice_capture` | the single capture lease of the individual (device, label, principal, session, expiry) |
| `voice_audio` | retained raw PCM per segment with `expires_at` — empty unless the owner enables retention |

- ASR text is labelled "Approximate (speech recognition)" everywhere; NeuroSciFi text "NeuroSciFi" plus its
  model/template label. Only approximate segments can be corrected; the original text is kept.
- Search: case-insensitive substring over text and corrected text (`GET transcripts?q=`, `nsf voice transcripts
  --search`), LIKE wildcards escaped; a Pocket principal sees only its own sessions.
- Forget: `POST forget {session_id, confirm: true}`, the voice command "forget this conversation" (confirmed),
  `nsf voice forget SESSION` or `nsf forget voice SESSION`. The rows are deleted and the WAL is truncated. Every
  checkpoint archive the forget made stale is then rewritten in place without the session
  (`checkpoints.rewrite`; see `docs/operations/RUNBOOK.md` §6). The commands wait for the rewrite and list the
  archives under `checkpoints_rewritten`. The hub (HTTP and spoken forget) runs the rewrite in the background and
  answers `checkpoints_rewriting` with the archive ids.
- Retention: `retain_audio` (default **false**) and `audio_ttl_hours` (1–168, default 24) are owner-only settings;
  the hub sweeps every minute (`nsf voice sweep` by hand); switching retention off deletes every retained clip.
  A new checkpoint deletes expired clips before it archives (so it never contains audio past its TTL). Limitation:
  an expiry does not rewrite archives. A clip that was live when an earlier checkpoint was written stays in that
  archive until a forget rewrites the archive (a rewrite drops every clip the live instance no longer holds, as a
  restore does), or until the archive is rebuilt or pruned (`nsf checkpoint prune --keep N`).

## 5. Capture ownership (one device at a time)

The lease row is per individual. `acquire` by another device while a live lease exists is refused with the
holder's label (a Pocket device sees "another device" for a holder of another principal); the UI shows "The
microphone is in use on <device>" and "Move the microphone here". Who confirms the move:

- the owner (from any device), or a device taking it from its own principal: the requester confirms
  (`capture {action: request_handoff}` → confirm id → `capture {action: handoff, confirm}`);
- a Pocket device taking it from another principal (e.g. the owner console): `request_handoff` answers
  `{requested: {id, expires_at}}` and files one request (meta `voice_handoff`, 60 s). The holder's next renewal
  returns `handoff_request`; its UI asks "Another device asks for the microphone. Hand it over?" and answers with
  `capture {action: grant_handoff | refuse_handoff, confirm: id}`. On grant the lease moves to the requester, whose
  client (polling `status` every 2 s) then turns its microphone on. On refusal the requester's session gets a
  `notice` event at once (`handoff: "refused"`, "The device that holds the microphone did not hand it over."), which
  ends its wait. The spoken "use this device for the microphone" files the same request.

Sessions: at most 4 per principal (the owner; each Pocket family) and 16 in all. When the hub is full the owner
displaces the oldest Pocket session; a Pocket family displaces only its own oldest session, never another family's
or the owner's, and is refused when it has none.

The previous holder's next renewal or upload is refused and it stops its tracks. Console tabs and Pocket devices
share the same lease (the console and Pocket processes coordinate through the database).

A session whose client stops calling (closed tab, vanished phone; open clients long-poll events at least every
20 s) ends after 120 s idle and releases its microphone lease. If speech recognition fails or is reported
unavailable while the microphone is on, the client stops capture (tracks ended, lease released) and says so;
"Turn microphone off" is never disabled while the microphone is on.

Principals are kept apart in the hub: at most 4 sessions per principal (a principal's oldest goes first), a Pocket
device never displaces an owner session (a full hub answers 409), a Pocket principal's mode, voice, speed and
transcript visibility are its own preferences (meta `voice_pref:<principal>`; the owner's settings are unchanged),
and its `status` carries only its own session's state (no owner snapshot, metrics or retained-audio count).

Upload path: audio requests do no database writes. Chunks are buffered in memory; consent and the lease are read
(WAL reads never wait for a writer); provisional segments, the final segment and (with retention on) its raw
audio in the same transaction are written by one writer thread in order. Measured with an emulated research
worker holding 0.5 s write transactions back to back (`HOLD=0.5`, 120 chunks of 0.5 s, fake ASR): chunk-ack p95
534 ms → 5.9 ms, max 1632 ms → 27.5 ms (before/after this change). Replies run on at most 8 threads and research
follow-ups on 16; beyond that typed text answers 409 "busy" and a spoken utterance is transcribed with a "busy"
notice instead of a reply.

## 6. Presence contract (Lane P)

Backend: `neuroscifi.voice.state.snapshot()` → `{mode, session_active, capture_device, mic_capturing, output_muted,
speaking, user_speaking, interrupted_at, updated_at}` for the most recently updated active session of the process
(text-only idle when none). Client facts come from `POST client` reports (on change only); the server refuses a
`mic_capturing` claim without the lease, never reports `speaking` while muted or outside mode A, and claims no live
audio once a client has been silent for 30 s.

Browser: `window.dispatchEvent(new CustomEvent("nsf:voice", {detail: {mic_capturing, speaking, output_muted, mode,
level}}))` on every change and at 15 Hz while capturing or speaking; `level` is the RMS (0..1) of an
`AnalyserNode` on the mic while listening and on the output path while speaking.
