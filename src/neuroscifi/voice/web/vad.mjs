// NeuroSciFi Voice Cortex: the energy voice-activity detector and barge-in rule (pure; no DOM, no audio graph).
//
// One 20 ms frame's RMS (0..1, measured by capture-worklet.mjs) goes in; the detector says whether the frame is
// voiced, when an utterance starts, when it is confirmed (MIN_SPEECH_MS of voiced audio; BARGE_MS while NeuroSciFi
// is speaking), when a barge-in fires (BARGE_MS voiced while playing) and when the utterance ends (END_SILENCE_MS of
// silence). The noise floor adapts only on unvoiced frames outside an utterance.
// voice-client.mjs runs it in the browser; scripts/voice_real_speech_eval.py replays real speech, noise, music and
// simulated echo through this same file (scripts/voice_vad_replay.mjs), and voice/selftest.py mirrors it for the
// terminal self-test (tests/voice/test_selftest.py checks that the two agree frame by frame).
// Thresholds: retuned in 0.7.0 on a TRAIN split of recorded human speech, noise, music and simulated echo and
// reported on a held-out TEST split (docs/voice/VOICE_VALIDATION.md); the earlier values were tuned on synthetic
// speech only and are kept in the comments.

export const VAD = Object.freeze({
  FRAME_MS: 20,
  MIN_SPEECH_MS: 160,       // voiced audio before an utterance is confirmed (and uploaded); was 250
  END_SILENCE_MS: 700,      // silence that ends an utterance
  PREROLL_MS: 300,          // audio kept from before the onset
  BARGE_MS: 250,            // voiced audio during playback that stops it; was 300
  FLOOR_INIT: 0.004,
  FLOOR_MAX: 0.05,
  FLOOR_ALPHA: 0.05,        // adaptive noise floor: exponential average of unvoiced frames
  SPEECH_K: 4,              // listening: voiced when rms > max(SPEECH_K x floor, SPEECH_MIN); was 3
  SPEECH_MIN: 0.006,        // was 0.012
  BARGE_K: 2,               // playing: voiced when rms > max(BARGE_K x floor, BARGE_MIN); was 5
  BARGE_MIN: 0.03,
});

export function createVad(params = VAD) {
  return { p: params, floor: params.FLOOR_INIT, voiced: 0, silent: 0, active: false, confirmed: false, barge: false,
           onset: 0 };
}

// Advances `v` by one frame. Returns what happened in this frame.
export function vadStep(v, rms, playing, now, P = v.p || VAD) {
  const threshold = Math.max(v.floor * P.SPEECH_K, P.SPEECH_MIN);
  const bargeThreshold = Math.max(v.floor * P.BARGE_K, P.BARGE_MIN);
  const voiced = rms > (playing ? bargeThreshold : threshold);
  if (!v.active && !voiced) v.floor = Math.min(P.FLOOR_MAX, v.floor * (1 - P.FLOOR_ALPHA) + rms * P.FLOOR_ALPHA);
  const out = { voiced, start: false, barge: false, confirm: false, end: false, confirmed: false, overlap: false };
  if (!v.active) {
    if (voiced) {
      v.active = true; v.confirmed = false; v.voiced = P.FRAME_MS; v.silent = 0; v.onset = now; v.barge = false;
      out.start = true;
    }
    return out;
  }
  if (voiced) { v.voiced += P.FRAME_MS; v.silent = 0; } else { v.silent += P.FRAME_MS; }
  if (playing && !v.barge && v.voiced >= P.BARGE_MS) { v.barge = true; out.barge = true; }
  if (!v.confirmed && v.voiced >= (playing ? P.BARGE_MS : P.MIN_SPEECH_MS)) { v.confirmed = true; out.confirm = true; }
  if (v.silent >= P.END_SILENCE_MS) {
    out.end = true;
    out.confirmed = v.confirmed;
    out.overlap = !v.confirmed && v.voiced > 0;   // a short sound: backchannel/overlap, not an utterance
    v.active = false; v.confirmed = false; v.voiced = 0; v.silent = 0;
  }
  return out;
}
