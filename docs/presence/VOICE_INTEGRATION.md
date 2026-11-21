# Voice integration (contract between Lane P — Presence — and Lane V — Voice)

One presence covers every modality. Voice changes only *which* activity is shown and what drives the motion
amplitude. It never invents speech or listening.

## Backend: `neuroscifi.voice.state.snapshot()` (owner scope)

The presence imports it lazily on every derive (`aggregator.voice_snapshot()`). It should return:

```
{mode: "voice_text" | "voice_in_text_out" | "text_only", session_active: bool, capture_device: str | null,
 mic_capturing: bool, output_muted: bool, speaking: bool, user_speaking: bool,
 interrupted_at: epoch seconds | null, updated_at: epoch seconds | null}
```

- If the module is absent (`ImportError`), `voice` is `null` and `sources.voice = "absent"`. Every voice dimension
  is then **unknown**; none is reported as false. An exception gives `"error"` with the same effect. Values of the
  wrong type are dropped to `null`, and `capture_device` is truncated to 60 characters.
- Mapping: `mic_capturing` gives *listening* (communication priority). `speaking` with `mode == voice_text` and not
  `output_muted` gives *responding*. `output_muted` adds the interaction `voice_muted` and never shows speech.
  `interrupted_at` within 3 s adds `user_interruption`. `text_only` (only during an active session) adds the
  interaction `text_only` and suppresses all audio candidates.
- `updated_at` is excluded from the state version, so a heartbeat does not wake the long-poll. Lane V's module
  (`src/neuroscifi/voice/state.py`) exists in this tree and follows this contract.
- Pocket (family scope) does not read the voice snapshot.

## Browser: the `nsf:voice` event

Lane V's client (`voice/web/voice-client.mjs`) dispatches:

```js
window.dispatchEvent(new CustomEvent("nsf:voice", { detail: {
  mic_capturing: bool, speaking: bool, output_muted: bool,
  mode: "voice_text" | "voice_in_text_out" | "text_only",
  level: number   // real RMS 0..1 from a WebAudio analyser: the mic while listening, the output while speaking
}}));
```

What the presence does with it (`presence.mjs Presence.voice`, `interpreter.voiceCandidates`):

- The page's voice information replaces the backend's voice candidates, because it carries a measured level.
- `speaking && !output_muted && mode !== voice_in_text_out` gives *responding*. The responding dots bounce with the
  **output** level.
- Otherwise `mic_capturing` gives *listening*, and the headphone level bars scale with the **mic** level.
- `mic_capturing` false (and not speaking) removes listening, and the level is set to `null`, so no motion follows
  the microphone.
- `output_muted` keeps the cognitive state (research, idle, …) and never depicts audible speech.
- `mode = text_only` gives no audio-driven motion (level `null`) and no voice activity.
- **Interruption** (T2-05): only a real barge-in or stop counts. The `nsf:voice` event marks it with
  `interrupted: true` (or a new `interrupted_at`), and that sets `interruptedAt`. The server state also carries it:
  the hub sets `interrupted_at` only on barge-in or stop, which gives `user_interruption` for 3 s, and the
  interpreter keeps that server value. A reply that simply ends while the mic stays on is not an interruption. The
  intent carries `user_interruption` for 1.5 s and the scene morphs to listening on springs, without snapping.
  (Voice lane: the client should add `interrupted: true` to the event it sends on a barge-in or stop.)
- **Typing** (T2-11): only text entry (text-like inputs, textareas, contenteditable) sets `user_typing`; radios,
  checkboxes and sliders do not.
- A level change alone does not re-plan the scene. It updates `engine.level` and keeps the frame loop alive.
- An event older than 8 s that reports neither capture nor speech no longer drives anything.

## Verification

| What | How | Kind |
|---|---|---|
| Backend mapping (absent, listening, muted, text-only, interruption) | `tests/presence/test_aggregator.py::test_voice_unknown_without_module_and_mapped_with_it` (fake module in `sys.modules`) | unit |
| A idle → listening on capture start, motion follows level, mic off stops it | `tests/e2e/test_presence.py::test_voice_events_listening_level_interrupt_and_mute` | **event-level**: the test dispatches `nsf:voice` in a real Chromium; no real microphone |
| B listening → the real task after the utterance | same test: capture stops, a real research task is queued, and the presence shows it | event-level voice + real task |
| F interruption without snapping or stale state | same test: barge-in samples the body path every 16 ms (≤20 units per sample), peak point speed ≤260 | event-level |
| muted / text-only | same test | event-level |

With real microphone capture these scenarios have **not** been verified in this lane. That needs Lane V's running
voice session and a person speaking (see `FEATURE_ACCEPTANCE.md`).
