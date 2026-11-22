// NeuroSciFi "Voice check": a guided self-test of the owner's real microphone, speaker, echo cancellation and
// barge-in, in the console Voice panel and in Pocket (docs/voice/VOICE_VALIDATION.md, "Owner device self-test").
// It captures with the same constraints and worklet as a voice session (voice-client.mjs CAPTURE,
// capture-worklet.mjs), runs the same VAD (vad.mjs) and plays through the same output path (the client's gain).
// Readings are uploaded only to be scored by this computer's speech recognition and are deleted there; the report
// (numbers, transcripts of the displayed sentences, device and browser model) is saved on the NeuroSciFi host.
// DOM through createElement/textContent only (strict CSP).
import { CAPTURE } from "./voice-client.mjs";
import { VAD, createVad, vadStep } from "./vad.mjs";

const FRAME = 320;                 // 20 ms at 16 kHz
const CHUNK_SAMPLES = 32000;       // 64 000 bytes per upload (< 64 KiB)
const READ_TIMEOUT_MS = 15000;
const SILENCE_MS = 5000;
// Microphone off (R6 D3): a frame the worklet posted just before it stopped can still be in the message queue, so
// frames within MICOFF_GRACE_MS of stopping are tolerated; any frame in the MICOFF_WATCH_MS after that means capture
// went on.
const MICOFF_GRACE_MS = 150;
const MICOFF_WATCH_MS = 300;

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid);
  return el;
}

const db = (x) => (x > 0 ? Math.round(200 * Math.log10(x)) / 10 : -120);

// The Microphone off result in words, naming the true reason when capture did not stop.
export function micOffMessage(r) {
  if (r.capture_stopped) return `Capture stopped: ${r.tracks_ended} track(s) ended, no frames after.`;
  const why = [];
  if (r.live_tracks_after) why.push(`${r.live_tracks_after} track(s) still live`);
  if (!r.tracks_ended) why.push("no capture track ended");
  if (r.frames_after) why.push(`${r.frames_after} audio frame(s) arrived after the tracks ended`);
  return `Capture did NOT stop: ${why.join("; ") || "the capture state is unknown"}.`;
}

export class VoiceCheck extends EventTarget {
  constructor(transport, client, { surface }) {
    super();
    this.t = transport;
    this.client = client;
    this.surface = surface;
    this.run = null;
    this.vad = createVad(VAD);
    this.playing = false;
    this.sink = null;
    this.frames = 0;
  }

  async begin() {
    if (this.client.micOn) await this.client.micOff("The voice session microphone is off during the voice check.");
    this.client.stopSpeaking("stop");
    const r = await this.t.post("selftest", { action: "start", surface: this.surface });
    this.run = r.run;
    this.sentences = r.sentences;
    this.passRules = r.pass;
    return r;
  }

  result(step, data) { return this.t.post("selftest", { action: "result", run: this.run, step, data }); }

  // ---- capture ----------------------------------------------------------------------------------------------------
  async micOn() {
    const ctx = this.client._ensureContext();
    await ctx.resume();
    const started = performance.now();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { ...CAPTURE } });
    } catch (err) {
      return { granted: false, error: err.name || "error" };
    }
    if (!this.client._workletLoaded) {
      await ctx.audioWorklet.addModule(new URL("./capture-worklet.mjs", import.meta.url));
      this.client._workletLoaded = true;
    }
    this.src = ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(ctx, "nsf-capture");
    this.node.port.onmessage = (e) => this._frame(e.data);
    this.src.connect(this.node);
    const track = this.stream.getAudioTracks()[0];
    const st = track && track.getSettings ? track.getSettings() : {};
    this.settings = st;
    return { granted: true, live_tracks: this.liveTracks(), permission_ms: Math.round(performance.now() - started),
             label: (track && track.label || "").slice(0, 60), echo_cancellation: st.echoCancellation ?? null,
             noise_suppression: st.noiseSuppression ?? null, auto_gain_control: st.autoGainControl ?? null,
             sample_rate: st.sampleRate ?? null, channel_count: st.channelCount ?? null,
             context_rate: ctx.sampleRate };
  }

  liveTracks() { return this.stream ? this.stream.getTracks().filter((t) => t.readyState === "live").length : 0; }

  _frame({ pcm, rms }) {
    this.frames++;
    const now = performance.now();
    const ev = vadStep(this.vad, rms, this.playing, now, VAD);
    if (this.sink) this.sink(new Int16Array(pcm), rms, ev, now);
  }

  async micOff() {
    const before = this.liveTracks();
    if (this.node) { try { this.node.port.postMessage("stop"); this.node.disconnect(); } catch { /* gone */ } }
    if (this.src) { try { this.src.disconnect(); } catch { /* gone */ } }
    const tracks = this.stream ? this.stream.getTracks() : [];
    for (const t of tracks) t.stop();
    const framesAt = this.frames;
    await new Promise((ok) => setTimeout(ok, MICOFF_GRACE_MS));
    const settled = this.frames;
    await new Promise((ok) => setTimeout(ok, MICOFF_WATCH_MS));
    this.node = this.src = null;
    const live = tracks.filter((t) => t.readyState === "live").length;
    const ended = tracks.filter((t) => t.readyState === "ended").length;
    const after = this.frames - settled;
    return { live_tracks_before: before, live_tracks_after: live, tracks_ended: ended,
             frames_in_grace: settled - framesAt, frames_after: after,
             capture_stopped: live === 0 && ended >= 1 && after === 0 };
  }

  // ---- steps --------------------------------------------------------------------------------------------------------
  read(index) {   // resolves with the server's score of one reading (VAD end of utterance, or a 15 s limit)
    return new Promise((resolve) => {
      const pre = [];
      let utt = null;
      const done = async (why) => {
        this.sink = null;
        clearTimeout(timer);
        if (!utt || !utt.length) { resolve({ index, status: "nothing_heard", why }); return; }
        resolve(await this._upload(index, utt).catch((err) => ({ index, status: "upload_failed", error: err.message })));
      };
      const timer = setTimeout(() => done("timeout"), READ_TIMEOUT_MS);
      this.sink = (chunk, rms, ev) => {
        if (!utt) {
          pre.push(chunk);
          if (pre.length > VAD.PREROLL_MS / VAD.FRAME_MS) pre.shift();
          if (ev.start) utt = pre.slice();
          return;
        }
        utt.push(chunk);
        if (ev.end) {
          if (ev.confirmed) done("end");
          else { utt = null; pre.length = 0; }      // a short noise, not the reading: keep listening
        }
      };
    });
  }

  async _upload(index, frames) {
    const n = frames.reduce((a, f) => a + f.length, 0);
    const pcm = new Int16Array(n);
    let o = 0;
    for (const f of frames) { pcm.set(f, o); o += f.length; }
    let last = null;
    const chunks = Math.max(1, Math.ceil(n / CHUNK_SAMPLES));
    for (let seq = 0; seq < chunks; seq++) {
      const part = pcm.slice(seq * CHUNK_SAMPLES, (seq + 1) * CHUNK_SAMPLES);
      last = await this.t.upload("selftest_audio", { "X-NSF-Selftest": this.run, "X-NSF-Index": String(index),
                                                      "X-NSF-Seq": String(seq),
                                                      "X-NSF-Final": seq === chunks - 1 ? "1" : "0" }, part.buffer);
    }
    pcm.fill(0);   // nothing of the reading stays in the page either
    return last;
  }

  silence() {
    return new Promise((resolve) => {
      const rms = [];
      let utterances = 0;
      this.sink = (chunk, r, ev) => { rms.push(r); if (ev.confirm) utterances++; };
      setTimeout(() => {
        this.sink = null;
        const s = rms.slice().sort((a, b) => a - b);
        const q = (p) => (s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : 0);
        resolve({ seconds: Math.round(rms.length * VAD.FRAME_MS / 100) / 10, noise_rms: q(0.5),
                  noise_dbfs: db(q(0.5)), noise_p95_dbfs: db(q(0.95)), floor_dbfs: db(this.vad.floor),
                  false_utterances: utterances });
      }, SILENCE_MS);
    });
  }

  async _phrase(n) {
    const ctx = this.client._ensureContext();
    await ctx.resume();
    const data = await this.t.bin("selftest_speech", { run: this.run, n });
    return ctx.decodeAudioData(data);
  }

  // play phrase n through the client's output path; onFrame(chunk, rms, ev, src) sees the mic while it plays
  async play(n, onFrame) {
    const ctx = this.client._ensureContext();
    const buf = await this._phrase(n);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.client.gain);
    let outMax = 0;
    const tmp = new Float32Array(1024);
    const meter = setInterval(() => {
      this.client.outAnalyser.getFloatTimeDomainData(tmp);
      let s = 0;
      for (let i = 0; i < tmp.length; i++) s += tmp[i] * tmp[i];
      outMax = Math.max(outMax, Math.sqrt(s / tmp.length));
    }, 50);
    const ended = new Promise((ok) => { src.onended = ok; });
    const started = performance.now();
    this.playing = true;
    this.sink = onFrame ? (chunk, rms, ev, now) => onFrame(chunk, rms, ev, src, now) : null;
    src.start();
    await ended;
    this.playing = false;
    this.sink = null;
    clearInterval(meter);
    return { seconds: Math.round(buf.duration * 10) / 10, played_ms: Math.round(performance.now() - started),
             output_peak_dbfs: db(outMax), muted: this.client.outputMuted, volume: this.client.volume };
  }

  async barge() {
    let detected = null;
    let onset = null;
    let stopped = null;
    const out = await this.play(1, (chunk, rms, ev, src, now) => {
      if (ev.barge && detected === null) {
        detected = now;
        onset = this.vad.onset;        // the first voiced frame of the utterance that interrupted
        try { src.stop(); } catch { /* ended */ }
        stopped = performance.now();
      }
    });
    return { detected: detected !== null, onset_to_detect_ms: detected ? Math.round(detected - onset) : null,
             onset_to_stop_ms: stopped ? Math.round(stopped - onset) : null, played_ms: out.played_ms,
             phrase_seconds: out.seconds, output_peak_dbfs: out.output_peak_dbfs };
  }

  async echo() {
    let barges = 0;
    let micMax = 0;
    const mic = [];
    const out = await this.play(1, (chunk, rms, ev) => {
      mic.push(rms);
      micMax = Math.max(micMax, rms);
      if (ev.barge) barges++;      // counted, and playback continues so the whole phrase is checked
    });
    mic.sort((a, b) => a - b);
    return { false_barge_ins: barges, echo_median_dbfs: db(mic[Math.floor(mic.length / 2)] || 0),
             echo_peak_dbfs: db(micMax), played_ms: out.played_ms, output_peak_dbfs: out.output_peak_dbfs };
  }

  async device() {
    const nav = navigator;
    const ua = nav.userAgentData;
    let hi = {};
    try { if (ua && ua.getHighEntropyValues) hi = await ua.getHighEntropyValues(["model", "platformVersion"]); }
    catch { /* not offered */ }
    const ctx = this.client.ctx;
    return { surface: this.surface, user_agent: (nav.userAgent || "").slice(0, 160),
             browser: ua && ua.brands ? ua.brands.filter((b) => !/Not.A.Brand/i.test(b.brand))
               .map((b) => `${b.brand} ${b.version}`).join(", ").slice(0, 80) : null,
             platform: (ua && ua.platform) || nav.platform || null, mobile: ua ? ua.mobile : null,
             model: hi.model || null, platform_version: hi.platformVersion || null,
             screen: `${screen.width}x${screen.height}@${window.devicePixelRatio || 1}`,
             base_latency_ms: ctx && ctx.baseLatency ? Math.round(ctx.baseLatency * 1000) : null,
             output_latency_ms: ctx && ctx.outputLatency ? Math.round(ctx.outputLatency * 1000) : null };
  }

  finish(device) { return this.t.post("selftest", { action: "finish", run: this.run, data: device }); }
}

// ---- the guided panel --------------------------------------------------------------------------------------------

const STEP_TEXT = {
  mic: ["Microphone", "Allow the microphone when the browser asks. Then look for the browser's microphone indicator " +
        "(a red dot or microphone icon in the tab, the address bar or the phone's status bar)."],
  read: ["Read five sentences", "Read each sentence aloud at your normal distance and volume when it appears."],
  silence: ["Silence", "Stay quiet for five seconds. This measures the background noise."],
  playback: ["Speaker", "NeuroSciFi says one sentence through your speaker or headphones."],
  barge: ["Interrupt", "NeuroSciFi talks for about 20 seconds. After a few words, say \"stop\" clearly."],
  echo: ["Echo", "NeuroSciFi talks again. Stay silent and let it finish: its own voice must not interrupt it."],
  micoff: ["Microphone off", "The check turns the microphone off and confirms that capture stopped."],
};
const ASK = {
  mic: ["indicator_seen", "Do you see the browser's microphone indicator?"],
  playback: ["heard", "Did you hear the sentence clearly?"],
  barge: ["stopped_confirmed", "Did it stop talking when you said \"stop\"?"],
  micoff: ["indicator_off", "Has the microphone indicator gone out?"],
};

export function mountVoiceCheck(root, transport, client, { surface }) {
  const check = new VoiceCheck(transport, client, { surface });
  const order = ["mic", "read", "silence", "playback", "barge", "echo", "micoff"];
  const ui = { steps: {} };
  const status = h("p", { class: "nsfv-vcheck-status", role: "status", "aria-live": "polite" });
  const say = (text) => { status.textContent = text; };
  const start = h("button", { type: "button", class: "nsfv-btn primary", "data-keep": true, text: "Start voice check" });
  const list = h("ol", { class: "nsfv-vcheck-steps", hidden: true });
  const report = h("div", { class: "nsfv-vcheck-report", hidden: true });
  let current = 0;

  for (const id of order) {
    const [title, text] = STEP_TEXT[id];
    const run = h("button", { type: "button", class: "nsfv-btn", "data-keep": true, "data-step": id, disabled: true,
                              text: `Run: ${title}` });
    const skip = h("button", { type: "button", class: "nsfv-link", "data-keep": true, disabled: true,
                               text: `Skip ${title.toLowerCase()}` });
    const prompt = h("p", { class: "nsfv-vcheck-prompt", "aria-live": "assertive" });
    const out = h("p", { class: "nsfv-vcheck-out" });
    const ask = h("div", { class: "nsfv-vcheck-ask", hidden: true });
    const li = h("li", { class: "nsfv-vcheck-step", "data-step": id, "data-state": "waiting" },
      h("h4", { text: title }), h("p", { text }), prompt, out, ask, h("div", { class: "nsfv-vcheck-row" }, run, skip));
    ui.steps[id] = { li, run, skip, prompt, out, ask };
    run.addEventListener("click", () => runStep(id));
    skip.addEventListener("click", () => { mark(id, "skipped", "Skipped."); advance(); });
    list.append(li);
  }

  function mark(id, state, text) {
    const s = ui.steps[id];
    s.li.dataset.state = state;
    if (text !== undefined) s.out.textContent = text;
  }

  function enable() {
    order.forEach((id, i) => {
      ui.steps[id].run.disabled = i !== current;
      ui.steps[id].skip.disabled = i !== current;
    });
    if (current < order.length) ui.steps[order[current]].run.focus();
  }

  function advance() {
    current += 1;
    enable();
    if (current >= order.length) finish();
  }

  function askUser(id, data) {
    const [field, question] = ASK[id];
    const s = ui.steps[id];
    return new Promise((resolve) => {
      const answer = async (value) => {
        s.ask.hidden = true;
        s.ask.replaceChildren();
        await check.result(id, { ...data, [field]: value }).catch(() => null);
        resolve(value);
      };
      s.ask.replaceChildren(h("p", { text: question }),
        h("button", { type: "button", class: "nsfv-btn", "data-keep": true, text: "Yes", onclick: () => answer(true) }),
        h("button", { type: "button", class: "nsfv-btn", "data-keep": true, text: "No", onclick: () => answer(false) }));
      s.ask.hidden = false;
      s.ask.querySelector("button").focus();
    });
  }

  async function runStep(id) {
    const s = ui.steps[id];
    s.run.disabled = s.skip.disabled = true;
    mark(id, "running", "Running…");
    try {
      if (id === "mic") {
        const r = await check.micOn();
        if (!r.granted) { await check.result("mic", r); mark(id, "failed", `The browser did not allow the microphone (${r.error}).`); return advance(); }
        mark(id, "running", `Microphone on: ${r.label || "default microphone"}; echo cancellation ` +
             `${r.echo_cancellation ? "on" : "OFF"}, noise suppression ${r.noise_suppression ? "on" : "off"}, ` +
             `automatic gain ${r.auto_gain_control ? "on" : "off"}.`);
        const ok = await askUser("mic", r);
        mark(id, ok ? "passed" : "failed");
      } else if (id === "read") {
        const rows = [];
        for (let i = 0; i < check.sentences.length; i++) {
          s.prompt.textContent = `Read aloud (${i + 1} of ${check.sentences.length}): “${check.sentences[i]}”`;
          s.li.dataset.reading = String(i);
          const r = await check.read(i);
          rows.push(r);
          s.out.textContent = r.status === "scored"
            ? `Heard: “${r.transcript}” · word error rate ${Math.round(r.wer * 100)}% · speech-to-noise ${r.snr_db} dB`
            : `Nothing usable was heard (${r.status}).`;
        }
        delete s.li.dataset.reading;
        s.prompt.textContent = "";
        const scored = rows.filter((r) => r.status === "scored");
        const bad = rows.length - scored.filter((r) => r.wer <= check.passRules.wer_max &&
                                                       r.snr_db >= check.passRules.snr_min_db).length;
        const avg = scored.length ? Math.round(100 * scored.reduce((a, r) => a + r.wer, 0) / scored.length) : null;
        mark(id, bad ? "failed" : "passed", `${scored.length} of ${rows.length} sentences scored; average word ` +
             `error rate ${avg ?? "n/a"}%; ${bad ? `${bad} below the bar (WER ≤ ${check.passRules.wer_max * 100}% ` +
             `and SNR ≥ ${check.passRules.snr_min_db} dB)` : "all passed"}.`);
      } else if (id === "silence") {
        s.prompt.textContent = "Quiet now, please…";
        const r = await check.silence();
        s.prompt.textContent = "";
        await check.result("silence", r);
        mark(id, r.false_utterances ? "failed" : "passed", `Noise floor ${r.noise_dbfs} dBFS (95th percentile ` +
             `${r.noise_p95_dbfs} dBFS)` + (r.false_utterances ? `; the detector heard ${r.false_utterances} ` +
             "utterance(s) in the silence." : "; no speech detected."));
      } else if (id === "playback") {
        const r = await check.play(0);
        const ok = await askUser("playback", r);
        mark(id, ok ? "passed" : "failed", `Played ${r.seconds} s at output peak ${r.output_peak_dbfs} dBFS` +
             (r.muted ? " (speech output is MUTED in this panel)" : "") + ".");
      } else if (id === "barge") {
        s.prompt.textContent = "Listen, then say “stop”.";
        const r = await check.barge();
        s.prompt.textContent = "";
        mark(id, "running", r.detected ? `Interruption detected; playback stopped ${r.onset_to_stop_ms} ms after you ` +
             "started speaking." : "No interruption was detected; it talked to the end.");
        const ok = await askUser("barge", r);
        mark(id, ok && r.detected ? "passed" : "failed");
      } else if (id === "echo") {
        s.prompt.textContent = "Stay silent until it finishes.";
        const r = await check.echo();
        s.prompt.textContent = "";
        await check.result("echo", r);
        mark(id, r.false_barge_ins ? "failed" : "passed", r.false_barge_ins
          ? `Its own voice triggered ${r.false_barge_ins} interruption(s) (echo peak ${r.echo_peak_dbfs} dBFS at the microphone).`
          : `No false interruption (echo at the microphone: median ${r.echo_median_dbfs}, peak ${r.echo_peak_dbfs} dBFS).`);
      } else if (id === "micoff") {
        const r = await check.micOff();
        mark(id, "running", micOffMessage(r));
        const ok = await askUser("micoff", r);
        mark(id, r.capture_stopped && ok ? "passed" : "failed");
      }
    } catch (err) {
      mark(id, "failed", `Failed: ${err.message || err}`);
    }
    advance();
  }

  async function finish() {
    say("Saving the report…");
    try {
      const r = await check.finish(await check.device());
      const v = r.evidence.verdict;
      const blob = new Blob([JSON.stringify(r.evidence, null, 1)], { type: "application/json" });
      report.replaceChildren(
        h("p", { class: "nsfv-vcheck-verdict", text: `${v.passed} of ${v.ran} steps passed` +
                 (v.all_passed ? ". Voice works on this device." : ". See the failed steps above.") }),
        h("p", { text: `Report saved on the NeuroSciFi computer as ${r.file}. No audio was kept.` }),
        h("a", { href: URL.createObjectURL(blob), download: r.file, class: "nsfv-link", text: "Download the report" }));
      report.hidden = false;
      root.dataset.checkDone = "true";
      say("Voice check finished.");
    } catch (err) {
      say(`Could not save the report: ${err.message}`);
    }
  }

  start.addEventListener("click", async () => {
    start.disabled = true;
    try {
      await check.begin();
    } catch (err) {
      say(`The voice check could not start: ${err.message}`);
      start.disabled = false;
      return;
    }
    list.hidden = false;
    current = 0;
    enable();
    say("Voice check started. Work through the steps in order.");
  });

  root.replaceChildren(h("details", { class: "nsfv-vcheck" }, h("summary", { text: "Voice check (microphone, speaker, interruptions)" }),
    h("p", { text: "A guided test of this device's microphone, speaker, echo cancellation and interruptions. You read " +
                   "five sentences; they go to this computer's speech recognition to be scored and are deleted " +
                   "right after. Only the numbers are kept, in a report on the NeuroSciFi computer." }),
    start, status, list, report));
  root.__nsfCheck = check;
  return check;
}
