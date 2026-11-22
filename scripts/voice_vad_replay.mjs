// Replays per-frame RMS sequences through the product's VAD (src/neuroscifi/voice/web/vad.mjs) for
// scripts/voice_real_speech_eval.py. stdin: {"params": [{...overrides}], "sequences": [{"id", "rms": [...],
// "playing": [0/1 per frame] | 0 | 1}]}. stdout: {"results": [[{"id", "events": [[frame, "start"|"confirm"|"barge"|
// "end", confirmed]]}] per params]}. Nothing else is read or written.
import { VAD, createVad, vadStep } from "../src/neuroscifi/voice/web/vad.mjs";

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { raw += d; });
process.stdin.on("end", () => {
  const job = JSON.parse(raw);
  const results = (job.params || [{}]).map((over) => {
    const P = Object.freeze({ ...VAD, ...over });
    return job.sequences.map((seq) => {
      const v = createVad(P);
      const events = [];
      const playing = seq.playing;
      for (let i = 0; i < seq.rms.length; i++) {
        const on = Array.isArray(playing) ? Boolean(playing[i]) : Boolean(playing);
        const ev = vadStep(v, seq.rms[i], on, i * P.FRAME_MS, P);
        if (ev.start) events.push([i, "start", false]);
        if (ev.barge) events.push([i, "barge", false]);
        if (ev.confirm) events.push([i, "confirm", false]);
        if (ev.end) events.push([i, "end", ev.confirmed]);
      }
      return { id: seq.id, events };
    });
  });
  process.stdout.write(JSON.stringify({ defaults: VAD, results }));
});
