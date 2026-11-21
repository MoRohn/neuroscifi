// NeuroSciFi Voice Cortex browser client (console and Pocket).
//
// Capture: getUserMedia (echo cancellation on) -> AudioWorklet (16 kHz PCM16 frames + RMS) -> energy VAD with an
// adaptive noise floor -> utterance chunks uploaded to the server (16 kHz PCM, <= 64 KiB each). Nothing is uploaded
// before an utterance is confirmed (MIN_SPEECH_MS of voiced audio), and nothing at all while the mic is off: mic off
// stops every MediaStreamTrack (the browser's capture indicator goes out) and releases the server-side lease.
// Playback: each reply arrives as sentences; sentence n+1 is fetched while n plays (WebAudio, gain = volume).
// Barge-in: while speaking, user speech above the barge threshold for BARGE_MS stops playback at once on the
// client (no round trip), then the server is told; shorter or quieter speech is an overlap/backchannel and does
// not interrupt. Presence contract: window "nsf:voice" CustomEvent {mic_capturing, speaking, output_muted, mode,
// level} on every change and at LEVEL_HZ while capturing or speaking; level = RMS 0..1 from an AnalyserNode (mic
// while listening, output while speaking).

export const MODES = ["voice_text", "voice_in_text_out", "text_only"];
const CHUNK_MS = 500;
const MIN_SPEECH_MS = 250;
const END_SILENCE_MS = 700;
const PREROLL_MS = 300;
const BARGE_MS = 300;
const LEVEL_HZ = 15;
const RENEW_MS = 5000;
const FRAME_MS = 20;

// ---- transports -----------------------------------------------------------------------------------------------

export function consoleTransport(token) {
  const auth = { Authorization: `Bearer ${token}` };
  async function call(op, body, method) {
    const get = method === "GET";
    const url = `/api/voice/${op}` + (get && body ? "?" + new URLSearchParams(body).toString() : "");
    const res = await fetch(url, {
      method: method || "POST", headers: get ? auth : { ...auth, "Content-Type": "application/json" },
      body: get ? undefined : JSON.stringify(body || {}), cache: "no-store",
    });
    return res;
  }
  return {
    kind: "console",
    async json(op, body, method) {
      const res = await call(op, body, method);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
      return data;
    },
    get(op, query) { return this.json(op, query, "GET"); },
    post(op, body) { return this.json(op, body, "POST"); },
    async speech(q) {
      const res = await call("speech", q, "GET");
      if (!res.ok) throw Object.assign(new Error(`speech HTTP ${res.status}`), { status: res.status });
      return res.arrayBuffer();
    },
    async audio(headers, pcm) {
      const res = await fetch("/api/voice/audio", {
        method: "POST", body: pcm, cache: "no-store",
        headers: { ...auth, "Content-Type": "audio/L16; rate=16000", ...headers },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
      return data;
    },
  };
}

export function pocketTransport(getKey) {
  async function call(op, init) {
    return fetch(`/api/voice_${op}`, { method: "POST", cache: "no-store", ...init });
  }
  const headers = () => ({ Authorization: `Bearer ${getKey()}` });
  return {
    kind: "pocket",
    async post(op, body) {
      const res = await call(op, { headers: { ...headers(), "Content-Type": "application/json" },
                                   body: JSON.stringify(body || {}) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
      return data;
    },
    get(op, query) { return this.post(op, query || {}); },
    async speech(q) {
      const res = await call("speech", { headers: { ...headers(), "Content-Type": "application/json" },
                                         body: JSON.stringify(q) });
      if (!res.ok) throw Object.assign(new Error(`speech HTTP ${res.status}`), { status: res.status });
      return res.arrayBuffer();
    },
    async audio(h, pcm) {
      const res = await call("audio", { headers: { ...headers(), "Content-Type": "audio/L16; rate=16000", ...h },
                                        body: pcm });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
      return data;
    },
  };
}

function rid(n = 12) {
  const a = new Uint8Array(n);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => (b % 36).toString(36)).join("");
}

function rmsOf(analyser, buf) {
  analyser.getFloatTimeDomainData(buf);
  let s = 0;
  for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
  return Math.min(1, Math.sqrt(s / buf.length));
}

// ---- the client ---------------------------------------------------------------------------------------------------

export class VoiceClient extends EventTarget {
  constructor(transport, { deviceId, storage } = {}) {
    super();
    this.t = transport;
    this.storage = storage || null;
    this.device = deviceId || this._stored("nsf.voice.device") || rid();
    this._store("nsf.voice.device", this.device);
    this.session = null;
    this.mode = "text_only";
    this.consented = false;
    this.micOn = false;
    this.outputMuted = this._stored("nsf.voice.muted") === "1";
    this.volume = Number(this._stored("nsf.voice.volume") || "0.9");
    this.inputDevice = this._stored("nsf.voice.input") || "";
    this.speaking = false;
    this.userSpeaking = false;
    this.since = 0;
    this.pending = null;
    this.stats = { audioRequests: 0, audioBytes: 0, utterances: 0, bargeIns: 0, overlaps: 0 };
    this.metrics = { turns: [], bargeIns: [], replies: [] };   // measured timings (performance.now() ms), read by tests and the UI
    this.vad = { floor: 0.004, voiced: 0, silent: 0, active: false, confirmed: false, frames: [], pre: [],
                 utt: null, seq: 0, sent: 0, startedAt: 0, lastSend: 0, barge: false, onset: 0 };
    this.queue = Promise.resolve();
    this.playback = { turn: null, n: 0, sources: [], nextStart: 0, playing: 0, fetching: false };
    this._level = 0;
    this._polling = false;
  }

  _stored(k) { try { return this.storage ? this.storage.getItem(k) : null; } catch { return null; } }
  _store(k, v) { try { if (this.storage) this.storage.setItem(k, v); } catch { /* private mode */ } }

  emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })); }

  presence() {
    const detail = { mic_capturing: this.micOn, speaking: this.speaking && !this.outputMuted,
                     output_muted: this.outputMuted, mode: this.mode, level: this._level };
    window.dispatchEvent(new CustomEvent("nsf:voice", { detail }));
  }

  _report() {   // tell the server (Presence aggregator) what this client is doing; on change only, debounced
    clearTimeout(this._reportTimer);
    this._reportTimer = setTimeout(() => {
      if (!this.session) return;
      this.t.post("client", { session: this.session, mic_capturing: this.micOn, speaking: this.speaking,
                               user_speaking: this.userSpeaking, output_muted: this.outputMuted }).catch(() => {});
    }, 80);
    this.presence();
    this.emit("state", this.snapshot());
  }

  snapshot() {
    return { session: this.session, mode: this.mode, micOn: this.micOn, speaking: this.speaking,
             userSpeaking: this.userSpeaking, outputMuted: this.outputMuted, volume: this.volume,
             consented: this.consented, pending: this.pending, stats: { ...this.stats } };
  }

  // ---- audio graph ------------------------------------------------------------------------------------------
  _ensureContext() {
    if (this.ctx) return this.ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AC({ latencyHint: "interactive" });
    this.gain = this.ctx.createGain();
    this.gain.gain.value = this.outputMuted ? 0 : this.volume;
    this.outAnalyser = this.ctx.createAnalyser();
    this.outAnalyser.fftSize = 1024;
    this.gain.connect(this.outAnalyser);
    this.outAnalyser.connect(this.ctx.destination);
    this._buf = new Float32Array(1024);
    return this.ctx;
  }

  _levelLoop() {
    if (this._levelTimer) return;
    this._levelTimer = setInterval(() => {
      if (!this.micOn && !this.speaking) {
        clearInterval(this._levelTimer);
        this._levelTimer = null;
        this._level = 0;
        this.presence();
        return;
      }
      if (this.speaking && this.outAnalyser) this._level = rmsOf(this.outAnalyser, this._buf);
      else if (this.micAnalyser) this._level = rmsOf(this.micAnalyser, this._buf);
      this.presence();
      this.emit("level", this._level);
    }, 1000 / LEVEL_HZ);
  }

  // ---- session ----------------------------------------------------------------------------------------------------
  async start(mode) {
    const s = await this.t.post("session", { action: "start", device: this.device, ...(mode ? { mode } : {}) });
    this.session = s.session;
    this.mode = s.mode;
    this.consented = s.consented;
    this.since = s.events_seq || 0;
    this.pending = s.pending;
    this._poll();
    this._report();
    return s;
  }

  async stop() {
    await this.micOff();
    this.stopSpeaking("stop");
    const sid = this.session;
    this.session = null;
    this._report();
    if (sid) await this.t.post("session", { action: "stop", session: sid }).catch(() => {});
  }

  async status(probe) {
    return this.t.get("status", { ...(this.session ? { session: this.session } : {}), ...(probe ? { probe: 1 } : {}) });
  }

  async consent(accept) {
    await this.t.post("consent", { session: this.session, accept });
    this.consented = accept;
    if (!accept) await this.micOff();
    this._report();
  }

  async setMode(mode) {
    if (!MODES.includes(mode)) throw new Error("unknown mode");
    await this.t.post("mode", { session: this.session, mode });
    await this._applyMode(mode);
  }

  async _applyMode(mode) {
    this.mode = mode;
    if (mode === "text_only") await this.micOff();
    if (mode !== "voice_text") this.stopSpeaking("mode");
    this._report();
  }

  setMuted(muted) {
    this.outputMuted = !!muted;
    this._store("nsf.voice.muted", muted ? "1" : "0");
    if (this.gain) this.gain.gain.value = muted ? 0 : this.volume;
    if (muted) this.stopSpeaking("stop");
    this._report();
  }

  setVolume(v) {
    this.volume = Math.max(0, Math.min(1, Number(v)));
    this._store("nsf.voice.volume", String(this.volume));
    if (this.gain && !this.outputMuted) this.gain.gain.value = this.volume;
  }

  async inputs() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return [];
    const all = await navigator.mediaDevices.enumerateDevices();
    return all.filter((d) => d.kind === "audioinput").map((d, i) => ({ id: d.deviceId, label: d.label || `Microphone ${i + 1}` }));
  }

  async setInput(id) {
    this.inputDevice = id || "";
    this._store("nsf.voice.input", this.inputDevice);
    if (this.micOn) { await this.micOff(); await this.micOnNow(); }
  }

  // ---- microphone -------------------------------------------------------------------------------------------------
  async micOnNow({ handoff } = {}) {
    if (!this.session) throw new Error("start a session first");
    if (this.mode === "text_only") throw new Error("text-only mode: switch to a voice mode first");
    if (!this.consented) throw Object.assign(new Error("consent required"), { code: "consent" });
    if (!window.isSecureContext || !navigator.mediaDevices) throw new Error("microphone needs a secure context (HTTPS)");
    const lease = await this.t.post("capture", { session: this.session, action: handoff ? "handoff" : "acquire",
                                                 ...(handoff ? { confirm: handoff } : {}) });
    if (!lease.granted) throw Object.assign(new Error(`The microphone is in use on ${lease.holder.label}`),
                                            { code: "busy", holder: lease.holder });
    const ctx = this._ensureContext();
    await ctx.resume();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
        ...(this.inputDevice ? { deviceId: { exact: this.inputDevice } } : {}) } });
    } catch (err) {
      await this.t.post("capture", { session: this.session, action: "release" }).catch(() => {});
      throw err;
    }
    if (!this._workletLoaded) {
      await ctx.audioWorklet.addModule(new URL("./capture-worklet.mjs", import.meta.url));
      this._workletLoaded = true;
    }
    this.src = ctx.createMediaStreamSource(this.stream);
    this.micAnalyser = ctx.createAnalyser();
    this.micAnalyser.fftSize = 1024;
    this.node = new AudioWorkletNode(ctx, "nsf-capture");
    this.node.port.onmessage = (e) => this._frame(e.data);
    this.src.connect(this.micAnalyser);
    this.src.connect(this.node);
    this.micOn = true;
    this.vad = { ...this.vad, active: false, confirmed: false, frames: [], pre: [], voiced: 0, silent: 0 };
    this._renew = setInterval(() => this._renewLease(), RENEW_MS);
    this._levelLoop();
    this._report();
  }

  async micOff(reason) {
    const wasOn = this.micOn;
    this.micOn = false;
    this.userSpeaking = false;
    clearInterval(this._renew);
    if (this.node) { try { this.node.port.postMessage("stop"); this.node.disconnect(); } catch { /* gone */ } }
    if (this.src) { try { this.src.disconnect(); } catch { /* gone */ } }
    if (this.stream) for (const track of this.stream.getTracks()) track.stop();   // capture actually stops
    this.node = this.src = this.stream = this.micAnalyser = null;
    this.vad.active = false;
    this.vad.frames = [];
    this.vad.pre = [];
    if (wasOn && this.session) {
      await this.t.post("capture", { session: this.session, action: "release" }).catch(() => {});
    }
    if (wasOn) { this._report(); if (reason) this.emit("notice", reason); }
  }

  tracksLive() {
    return this.stream ? this.stream.getTracks().filter((t) => t.readyState === "live").length : 0;
  }

  async _renewLease() {
    if (!this.micOn) return;
    try {
      const r = await this.t.post("capture", { session: this.session, action: "renew" });
      if (!r.granted) await this.micOff(`The microphone moved to ${r.holder && r.holder.label ? r.holder.label : "another device"}.`);
      else if (r.handoff_request && !(this.pending && this.pending.id === r.handoff_request.id)) {
        this.pending = { ...r.handoff_request, action: "handoff_grant" };   // another device asks: this holder decides
        this._report();
      }
    } catch (err) {
      if (err.status === 404) await this.micOff("The voice session ended.");
    }
  }

  async requestHandoff() {
    const r = await this.t.post("capture", { session: this.session, action: "request_handoff" });
    if (r.requested) {   // the microphone belongs to another principal: only its holder can hand it over
      this.emit("notice", "Asked the device that holds the microphone to hand it over; it moves here when that device allows it.");
      this._awaitHandoff();
      return null;
    }
    this.pending = r.confirm;
    this._report();
    return r.confirm;
  }

  async _awaitHandoff() {
    const sid = this.session;
    const deadline = Date.now() + 65000;
    while (this.session === sid && !this.micOn && Date.now() < deadline) {
      await new Promise((ok) => setTimeout(ok, 2000));
      const st = await this.status().catch(() => null);
      if (st && st.capture && st.capture.this_device) { this.emit("handoff_granted", {}); return; }
    }
    if (this.session === sid && !this.micOn) this.emit("notice", "The other device did not hand over the microphone.");
  }

  // ---- VAD and upload -------------------------------------------------------------------------------------------------
  _frame({ pcm, rms }) {
    if (!this.micOn) return;   // a frame still in flight after mic off is dropped, never uploaded
    const v = this.vad;
    const now = performance.now();
    const playing = this.speaking && !this.outputMuted;
    const threshold = Math.max(v.floor * 3, 0.012);
    const bargeThreshold = Math.max(v.floor * 5, 0.03);
    const voiced = rms > (playing ? bargeThreshold : threshold);
    if (!v.active && !voiced) v.floor = Math.min(0.05, v.floor * 0.95 + rms * 0.05);   // adaptive noise floor
    const chunk = new Int16Array(pcm);
    if (!v.active) {
      v.pre.push(chunk);
      if (v.pre.length > PREROLL_MS / FRAME_MS) v.pre.shift();
      if (voiced) {
        v.active = true; v.confirmed = false; v.voiced = FRAME_MS; v.silent = 0; v.onset = now;
        v.frames = v.pre.slice(); v.pre = []; v.barge = false;
        this.userSpeaking = true;
        this._report();
      }
      return;
    }
    v.frames.push(chunk);
    if (voiced) { v.voiced += FRAME_MS; v.silent = 0; } else { v.silent += FRAME_MS; }
    if (playing && !v.barge && v.voiced >= BARGE_MS) {   // interruption: stop at once, tell the server afterwards
      v.barge = true;
      const detected = performance.now();
      const stopped = this.stopSpeaking("barge_in");
      this.stats.bargeIns++;
      this.metrics.bargeIns.push({ onset: v.onset, detected, stopped, stop_ms: stopped - v.onset,
                                   after_detect_ms: stopped - detected });
    }
    if (!v.confirmed && v.voiced >= (playing ? BARGE_MS : MIN_SPEECH_MS)) {
      v.confirmed = true;
      v.utt = "u" + rid(10); v.seq = 0; v.sent = 0; v.startedAt = Date.now() / 1000; v.lastSend = now;
      this.stats.utterances++;
    }
    if (v.silent >= END_SILENCE_MS) {
      const confirmed = v.confirmed;
      if (!confirmed && v.voiced > 0) {   // a short sound while speaking: backchannel/overlap, not an interruption
        if (this.speaking) {
          this.stats.overlaps++;
          if (this.playback.turn) this.t.post("interrupt", { session: this.session, kind: "overlap" }).catch(() => {});
        }
      }
      if (confirmed) this._send(true, now - END_SILENCE_MS);
      v.active = false; v.confirmed = false; v.frames = []; v.voiced = 0; v.silent = 0;
      this.userSpeaking = false;
      this._report();
      return;
    }
    if (v.confirmed && now - v.lastSend >= CHUNK_MS) { v.lastSend = now; this._send(false); }
  }

  _send(final, speechEnd) {
    const v = this.vad;
    const frames = v.frames.slice(v.sent);
    v.sent = v.frames.length;
    const len = frames.reduce((n, f) => n + f.length, 0);
    const pcm = new Int16Array(len);
    let o = 0;
    for (const f of frames) { pcm.set(f, o); o += f.length; }
    const headers = { "X-NSF-Session": this.session, "X-NSF-Utterance": v.utt, "X-NSF-Seq": String(v.seq++),
                      "X-NSF-Final": final ? "1" : "0" };
    const utt = v.utt;
    const eou = final ? performance.now() : null;
    if (final) this.metrics.turns.push({ utterance: utt, eou, speech_end: speechEnd });
    this.queue = this.queue.then(async () => {
      if (!this.micOn && !final) return;   // never upload after mic off (a final chunk was captured while on)
      this.stats.audioRequests++;
      this.stats.audioBytes += pcm.byteLength;
      try {
        const r = await this.t.audio(headers, pcm.buffer);
        if (final) {
          const m = this.metrics.turns.find((x) => x.utterance === utt);
          if (m) { m.final_ack = performance.now(); m.transcript = r.text || ""; m.asr_ms = r.asr_ms; }
        }
        this.emit("upload", { utterance: utt, final, result: r });
      } catch (err) {
        this.emit("notice", `Audio not accepted: ${err.message}`);
        if (err.status === 409 && /hold the microphone|text-only|consent/.test(err.message)) await this.micOff();
      }
    });
  }

  async say(text) {   // typed input through the same bridge (works in every mode, without voice services)
    return this.t.post("text", { session: this.session, text });
  }

  async confirm(accept) {
    if (!this.pending) return null;
    const { id, action } = this.pending;
    this.pending = null;
    this._report();
    if (action === "handoff_grant") {   // this device holds the microphone and another one asked for it
      const r = await this.t.post("capture", { session: this.session, action: accept ? "grant_handoff" : "refuse_handoff",
                                               confirm: id });
      if (accept) await this.micOff("The microphone moved to the other device.");
      return r;
    }
    return this.t.post("confirm", { session: this.session, id, accept });
  }

  // ---- events ---------------------------------------------------------------------------------------------------------
  async _poll() {
    if (this._polling) return;
    this._polling = true;
    let backoff = 500;
    while (this.session) {
      const sid = this.session;
      try {
        const r = await this.t.get("events", { session: sid, since: this.since, wait: 15 });
        backoff = 500;
        for (const e of r.events) { this.since = Math.max(this.since, e.seq); this._event(e); }
        if (!r.active) break;
      } catch (err) {
        if (err.status === 404) { this.emit("notice", "The voice session ended."); this.session = null; break; }
        await new Promise((ok) => setTimeout(ok, backoff));
        backoff = Math.min(8000, backoff * 2);
      }
    }
    this._polling = false;
  }

  _event(e) {
    if (e.type === "transcript" || e.type === "interrupted") { if (e.segment) this.emit("segment", e.segment); }
    else if (e.type === "reply") {
      if (e.segment) this.emit("segment", e.segment);
      const respondingTo = e.segment && e.segment.detail && e.segment.detail.responding_to;
      const m = this.metrics.turns.find((x) => x.utterance === respondingTo);
      const rec = { intent: e.reply.intent, source: e.reply.source, responding_to: respondingTo || null,
                    turn: e.turn ? e.turn.id : null, reply_event: performance.now() };
      this.metrics.replies.push(rec);
      if (m) m.reply_event = rec.reply_event;
      if (e.reply.confirm) { this.pending = e.reply.confirm; this._report(); }
      this.emit("reply", e.reply);
      if (e.turn && this.mode === "voice_text" && !this.outputMuted) this._play(e.turn, m, rec);
    } else if (e.type === "mode") { this._applyMode(e.mode); }
    else if (e.type === "stop_speaking") { this.stopSpeaking("stop"); }
    else if (e.type === "confirm") { this.pending = e.confirm; this._report(); }
    else if (e.type === "forgotten") { this.session = e.new_session; this.since = 0; this.emit("forgotten", e); this._poll(); }
    else if (e.type === "session" && e.status === "ended") { this.session = null; this.micOff(); this._report(); }
    this.emit("event", e);
  }

  // ---- playback ---------------------------------------------------------------------------------------------------------
  async _play(turn, metric, rec) {
    const ctx = this._ensureContext();
    await ctx.resume().catch(() => {});
    this.stopSpeaking("superseded");
    const pb = this.playback = { turn: turn.id, n: 0, total: turn.sentences, sources: [], nextStart: 0, playing: 0,
                                 metric };
    for (let n = 0; n < turn.sentences; n++) {
      if (this.playback !== pb) return;
      let buf;
      try {
        const data = await this.t.speech({ session: this.session, turn: turn.id, n });
        buf = await ctx.decodeAudioData(data);
      } catch (err) {
        if (this.playback === pb && err.status !== 409) this.emit("notice", "Speech output failed; the reply is shown as text.");
        break;
      }
      if (this.playback !== pb || this.outputMuted || this.mode !== "voice_text") return;
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.gain);
      const at = Math.max(ctx.currentTime + 0.02, pb.nextStart);
      pb.nextStart = at + buf.duration;
      const index = n;
      src.onended = () => {
        pb.playing--;
        pb.n = index + 1;
        if (this.playback === pb && pb.playing === 0 && pb.n >= pb.total) this._finished(pb);
      };
      src.start(at);
      pb.sources.push(src);
      pb.playing++;
      if (n === 0) {
        const startsIn = (at - ctx.currentTime) * 1000;
        if (metric) metric.first_audio = performance.now() + startsIn;
        if (rec) rec.first_audio = performance.now() + startsIn;
        this.speaking = true;
        this._levelLoop();
        this._report();
      }
    }
  }

  _finished(pb) {
    this.speaking = false;
    this.playback = { turn: null, n: 0, sources: [], nextStart: 0, playing: 0 };
    this._report();
    if (this.session) this.t.post("interrupt", { session: this.session, kind: "finished", turn: pb.turn }).catch(() => {});
  }

  stopSpeaking(kind) {   // returns performance.now() when every source was stopped
    const pb = this.playback;
    for (const src of pb.sources || []) { try { src.onended = null; src.stop(); src.disconnect(); } catch { /* done */ } }
    const stopped = performance.now();
    const had = pb.turn && (pb.sources || []).length;
    this.playback = { turn: null, n: 0, sources: [], nextStart: 0, playing: 0 };
    if (this.speaking) { this.speaking = false; this._report(); }
    if (had && this.session && (kind === "barge_in" || kind === "stop")) {
      const sentence = Math.min(pb.total || 0, pb.n || 0);
      this.t.post("interrupt", { session: this.session, kind, turn: pb.turn, sentence,
                                 stop_ms: kind === "barge_in" ? Math.round(stopped - this.vad.onset) : undefined })
        .catch(() => {});
    }
    return stopped;
  }
}
