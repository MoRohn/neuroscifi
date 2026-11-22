// R6 D3: the Voice check's "Microphone off" step failed when a worklet frame already in flight arrived after the
// tracks ended, and then said "0 track(s) still live". It now decides from the tracks (none live, at least one
// ended) and from frames after a short grace, and its message names the true reason.
import assert from "node:assert/strict";
import { VoiceCheck, micOffMessage } from "../../src/neuroscifi/voice/web/voice-check.mjs";

function rig({ inflight = [], stream = false, liveTrack = false } = {}) {
  const check = new VoiceCheck({}, {}, { surface: "console" });
  const frame = () => check._frame({ pcm: new Int16Array(320).buffer, rms: 0 });
  const tracks = [{ readyState: "live", stop() { if (!liveTrack) this.readyState = "ended"; } }];
  let timer = null;
  check.stream = { getTracks: () => tracks };
  check.src = { disconnect() {} };
  check.node = {
    disconnect() {},
    port: { postMessage() {
      for (const ms of inflight) setTimeout(frame, ms);                // frames posted before the stop landed
      if (stream) timer = setInterval(frame, 20);                       // a worklet that never stopped
    } },
  };
  return { check, done: () => clearInterval(timer) };
}

{
  const { check } = rig({ inflight: [5, 60] });
  const r = await check.micOff();
  assert.equal(r.live_tracks_after, 0);
  assert.equal(r.tracks_ended, 1);
  assert.equal(r.frames_in_grace, 2);
  assert.equal(r.frames_after, 0);
  assert.equal(r.capture_stopped, true, "a frame already in flight must not fail the step");
  assert.match(micOffMessage(r), /^Capture stopped: 1 track\(s\) ended/);
}
{
  const { check, done } = rig({ stream: true });
  const r = await check.micOff();
  done();
  assert.equal(r.capture_stopped, false);
  assert.ok(r.frames_after > 0);
  const text = micOffMessage(r);
  assert.match(text, /audio frame\(s\) arrived after the tracks ended/);
  assert.doesNotMatch(text, /still live/, "no live track: the message must not blame one");
}
{
  const { check } = rig({ liveTrack: true });
  const r = await check.micOff();
  assert.equal(r.capture_stopped, false);
  assert.match(micOffMessage(r), /1 track\(s\) still live; no capture track ended/);
}
console.log("voice micoff check ok");
