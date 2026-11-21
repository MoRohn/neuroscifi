// NeuroSciFi Voice Cortex panel (console and Pocket). DOM only through createElement/textContent (strict CSP, no
// inline script or style); colours come from the shared theme's CSS variables (voice.css).
import { VoiceClient, consoleTransport, pocketTransport, MODES } from "./voice-client.mjs";

const MODE_TEXT = {
  voice_text: ["Voice and text", "You speak or type; NeuroSciFi answers in text and speech."],
  voice_in_text_out: ["Voice in, text out", "You speak or type; answers are text only (no audio output)."],
  text_only: ["Text only", "No microphone and no speech; works without any voice service."],
};

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

// Source URLs come from fetched pages: only http(s) becomes a link (no javascript:, data:, ...), as research-ui.mjs.
export function safeUrl(u) {
  try { const x = new URL(u); return x.protocol === "http:" || x.protocol === "https:" ? x.href : null; }
  catch { return null; }
}

function time(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function mountConsole(root, token) {
  return mountVoice(root, consoleTransport(token), { owner: true, storage: window.localStorage });
}

export function mountPocket(root, getKey) {
  // Pocket keeps nothing in browser storage (its privacy rule); the server identifies the device by its pairing.
  return mountVoice(root, pocketTransport(getKey), { owner: false, storage: null, pauseInBackground: true });
}

export function mountVoice(root, transport, { owner, storage, pauseInBackground } = {}) {
  if (root.__nsfVoice) return root.__nsfVoice;
  const client = new VoiceClient(transport, { storage });
  const ui = {};
  const segments = new Map();

  ui.indicator = h("span", { class: "nsfv-indicator", "data-on": "false", role: "status", "aria-live": "polite",
                             text: "Microphone off" });
  ui.session = h("button", { type: "button", class: "nsfv-btn", "data-keep": true, text: "Start voice session" });
  ui.mic = h("button", { type: "button", class: "nsfv-btn", "aria-pressed": "false", "data-keep": true,
                         text: "Turn microphone on", disabled: true });
  ui.mute = h("button", { type: "button", class: "nsfv-btn", "aria-pressed": String(client.outputMuted),
                          "data-keep": true, text: client.outputMuted ? "Unmute speech" : "Mute speech" });
  ui.volume = h("input", { type: "range", min: "0", max: "1", step: "0.05", value: String(client.volume),
                           id: "nsfvVolume", "data-keep": true });
  ui.modes = h("fieldset", { class: "nsfv-modes" }, h("legend", { text: "Mode" }),
    MODES.map((m) => h("label", { class: "nsfv-mode" },
      h("input", { type: "radio", name: "nsfvMode", value: m, "data-keep": true }),
      h("span", { text: MODE_TEXT[m][0] }), h("small", { text: MODE_TEXT[m][1] }))));
  ui.voice = h("select", { id: "nsfvVoice", "data-keep": true });
  ui.input = h("select", { id: "nsfvInput", "data-keep": true }, h("option", { value: "", text: "Default microphone" }));
  ui.showTranscript = h("input", { type: "checkbox", id: "nsfvShow", checked: true, "data-keep": true });
  ui.retain = h("input", { type: "checkbox", id: "nsfvRetain", "data-keep": true });
  ui.ttl = h("input", { type: "number", id: "nsfvTtl", min: "1", max: "168", step: "1", value: "24", "data-keep": true });
  ui.consent = h("div", { class: "nsfv-consent", role: "region", "aria-label": "Microphone consent", hidden: true },
    h("p", { text: "Before the first capture on this device: when the microphone is on, NeuroSciFi sends your speech " +
                   "to its own speech recognition on this computer (never to an outside service) and keeps the " +
                   "transcript. Raw audio is not stored unless the owner turns retention on. You can turn the " +
                   "microphone off at any time; capture then stops." }),
    h("button", { type: "button", class: "nsfv-btn primary", "data-keep": true, text: "Allow microphone",
                  onclick: () => act(async () => { await client.consent(true); ui.consent.hidden = true; await micOn(); }) }),
    h("button", { type: "button", class: "nsfv-btn", "data-keep": true, text: "Not now",
                  onclick: () => { ui.consent.hidden = true; } }));
  ui.confirmText = h("p", {});
  ui.confirm = h("div", { class: "nsfv-confirm", role: "alertdialog", "aria-label": "Confirmation needed", hidden: true },
    ui.confirmText,
    h("button", { type: "button", class: "nsfv-btn danger", "data-keep": true, text: "Confirm",
                  onclick: () => act(async () => {
                    const p = client.pending;
                    if (p && p.action === "handoff" && !client.micOn) {   // move the microphone here: capture now
                      client.pending = null;
                      render();
                      return micOn({ handoff: p.id });
                    }
                    return client.confirm(true);
                  }) }),
    h("button", { type: "button", class: "nsfv-btn", "data-keep": true, text: "Cancel",
                  onclick: () => act(() => client.confirm(false)) }));
  ui.handoff = h("div", { class: "nsfv-handoff", hidden: true }, h("p", { class: "nsfv-handoff-text" }),
    h("button", { type: "button", class: "nsfv-btn", "data-keep": true, text: "Move the microphone here",
                  onclick: () => act(async () => { await client.requestHandoff(); }) }));
  ui.health = h("p", { class: "nsfv-health", text: "Checking voice services…" });
  ui.notice = h("p", { class: "nsfv-notice", role: "status", "aria-live": "polite" });
  ui.text = h("input", { type: "text", id: "nsfvText", maxlength: "2000", autocomplete: "off", "data-keep": true,
                         placeholder: "Type a question or a command (e.g. show evidence)" });
  ui.form = h("form", { class: "nsfv-type" }, h("label", { for: "nsfvText", class: "nsfv-vh", text: "Message" }), ui.text,
    h("button", { type: "submit", class: "nsfv-btn primary", "data-keep": true, text: "Send" }));
  ui.list = h("ol", { class: "nsfv-transcript", "aria-label": "Transcript", "aria-live": "polite" });
  ui.search = h("input", { type: "search", id: "nsfvSearch", placeholder: "Search transcripts", "data-keep": true });
  ui.results = h("ol", { class: "nsfv-results" });

  const settings = h("div", { class: "nsfv-settings" },
    h("label", { for: "nsfvVoice", text: "Voice" }), ui.voice,
    h("label", { for: "nsfvInput", text: "Input device" }), ui.input,
    h("label", { for: "nsfvVolume", text: "Volume" }), ui.volume,
    h("label", { class: "nsfv-check" }, ui.showTranscript, h("span", { text: "Show transcript" })),
    owner ? h("label", { class: "nsfv-check" }, ui.retain, h("span", { text: "Keep raw audio (off by default)" })) : null,
    owner ? h("label", { for: "nsfvTtl", text: "Keep audio for (hours)" }) : null, owner ? ui.ttl : null);

  root.replaceChildren(h("div", { class: "nsfv" },
    h("div", { class: "nsfv-bar" }, ui.indicator, ui.session, ui.mic, ui.mute),
    ui.health, ui.consent, ui.confirm, ui.handoff, ui.notice, ui.modes, settings, ui.form, ui.list,
    h("details", { class: "nsfv-find" }, h("summary", { text: "Search transcripts" }),
      h("form", { class: "nsfv-type", onsubmit: (e) => { e.preventDefault(); act(search); } },
        h("label", { for: "nsfvSearch", class: "nsfv-vh", text: "Search text" }), ui.search,
        h("button", { type: "submit", class: "nsfv-btn", "data-keep": true, text: "Search" })), ui.results)));

  function notice(text) { ui.notice.textContent = text || ""; }

  async function act(fn) {
    try { return await fn(); } catch (err) { notice(err.message || String(err)); return null; }
  }

  function render() {
    const s = client.snapshot();
    ui.session.textContent = s.session ? "End voice session" : "Start voice session";
    // turning the microphone OFF is always possible; only turning it on needs a session, a voice mode and ASR
    ui.mic.disabled = !s.micOn && (!s.session || s.mode === "text_only" || !state.voiceIn);
    ui.mic.setAttribute("aria-pressed", String(s.micOn));
    ui.mic.textContent = s.micOn ? "Turn microphone off" : "Turn microphone on";
    ui.mute.setAttribute("aria-pressed", String(s.outputMuted));
    ui.mute.textContent = s.outputMuted ? "Unmute speech" : "Mute speech";
    ui.mute.disabled = s.mode !== "voice_text";
    ui.indicator.dataset.on = String(s.micOn);
    ui.indicator.textContent = s.micOn ? (s.userSpeaking ? "● Microphone on — hearing you" : "● Microphone on — capturing")
      : (s.speaking ? "Speaking" : "Microphone off");
    for (const r of ui.modes.querySelectorAll("input")) r.checked = r.value === s.mode;
    ui.confirm.hidden = !s.pending;
    if (s.pending) ui.confirmText.textContent = s.pending.summary;
    ui.list.hidden = !ui.showTranscript.checked;
    root.dataset.mode = s.mode;
    root.dataset.mic = String(s.micOn);
  }

  const state = { voiceIn: false, voiceOut: false };

  async function refreshStatus() {
    const st = await client.status().catch((err) => ({ error: err.message }));
    if (st.error) { ui.health.textContent = `Voice status unavailable: ${st.error}`; return st; }
    state.voiceIn = st.available.voice_in;
    state.voiceOut = st.available.voice_out;
    if (!state.voiceIn && client.micOn) await client.micOff("Speech recognition is unavailable, so the microphone is off. Type instead.");
    const e = st.engines;
    const word = (x) => `${x.state}${x.reason && x.state !== "ready" ? ` (${String(x.reason).slice(0, 80)})` : ""}`;
    ui.health.textContent = `Speech recognition: ${word(e.asr)} · Speech output: ${word(e.tts)} · PersonaPlex ` +
      `(experimental, never answers questions): ${e.duplex.state}. Text works in every mode.`;
    if (!ui.voice.options.length) {
      for (const v of st.voices) ui.voice.append(h("option", { value: v, text: v }));
    }
    ui.voice.value = st.settings.voice;
    if (owner) { ui.retain.checked = st.settings.retain_audio; ui.ttl.value = String(st.settings.audio_ttl_hours); }
    ui.showTranscript.checked = st.settings.transcript_visible;
    const cap = st.capture;
    ui.handoff.hidden = !(client.session && cap.held && !cap.this_device);
    if (!ui.handoff.hidden) ui.handoff.querySelector(".nsfv-handoff-text").textContent =
      `The microphone is in use on ${cap.label}. Only one device captures at a time.`;
    render();
    return st;
  }

  function segmentItem(seg) {
    const who = seg.speaker === "user" ? "You" : "NeuroSciFi";
    const badge = seg.speaker === "neuroscifi" ? (seg.detail && seg.detail.label) || seg.label : seg.label;
    const li = h("li", { class: `nsfv-seg nsfv-${seg.speaker} nsfv-${seg.status}`, "data-seg": String(seg.id) },
      h("div", { class: "nsfv-meta" }, h("strong", { text: who }), h("time", { text: time(seg.created_at) }),
        h("span", { class: `nsfv-badge nsfv-${seg.authority}`, text: badge }),
        seg.status === "provisional" ? h("span", { class: "nsfv-badge", text: "provisional" }) : null,
        seg.status === "interrupted" ? h("span", { class: "nsfv-badge", text: `interrupted after sentence ${seg.detail.spoken_until ?? 0}` }) : null,
        seg.corrected_text ? h("span", { class: "nsfv-badge", text: "corrected" }) : null),
      h("p", { class: "nsfv-text", text: seg.display_text || seg.text }));
    const sources = seg.detail && seg.detail.sources;
    if (sources && sources.length) {
      li.append(h("ol", { class: "nsfv-sources", "aria-label": "Sources" }, sources.map((s) =>
        h("li", { value: String(s.n) }, h("a", { href: safeUrl(s.url), target: "_blank", rel: "noopener noreferrer",
                                                 text: s.title || s.url }), h("span", { text: ` ${s.domain}` })))));
    }
    if (seg.authority === "approximate" && seg.status === "final") {
      li.append(h("button", { type: "button", class: "nsfv-link", "data-keep": true, text: "Correct",
                              onclick: () => correct(li, seg) }));
    }
    return li;
  }

  function upsertSegment(seg) {
    if (!seg) return;
    const item = segmentItem(seg);
    const old = segments.get(seg.id);
    if (old) old.replaceWith(item); else ui.list.append(item);
    segments.set(seg.id, item);
  }

  function correct(li, seg) {
    const input = h("input", { type: "text", value: seg.display_text || seg.text, maxlength: "2000", "aria-label": "Corrected text" });
    const form = h("form", { class: "nsfv-type", onsubmit: (e) => { e.preventDefault(); act(async () => {
      const fixed = await transport.post("correct", { segment: seg.id, text: input.value });
      upsertSegment(fixed);
    }); } }, input, h("button", { type: "submit", class: "nsfv-btn", "data-keep": true, text: "Save correction" }));
    li.append(form);
    input.focus();
  }

  async function search() {
    const q = ui.search.value.trim();
    if (!q) return;
    const r = await transport.get("transcripts", { q, limit: 50 });
    ui.results.replaceChildren(...r.segments.map((s) => h("li", {},
      h("span", { class: "nsfv-badge", text: s.label }), h("time", { text: ` ${new Date(s.created_at * 1000).toLocaleString()} ` }),
      h("span", { text: s.display_text }))));
    if (!r.segments.length) ui.results.append(h("li", { text: "No match." }));
  }

  async function micOn(opts) {
    try {
      await client.micOnNow(opts);
      ui.handoff.hidden = true;
      const list = await client.inputs();
      if (ui.input.options.length <= 1) for (const d of list) ui.input.append(h("option", { value: d.id, text: d.label }));
      ui.input.value = client.inputDevice;
    } catch (err) {
      if (err.code === "consent") { ui.consent.hidden = false; return; }
      if (err.code === "busy") { ui.handoff.hidden = false; ui.handoff.querySelector(".nsfv-handoff-text").textContent = err.message; return; }
      notice(err.name === "NotAllowedError" ? "The browser blocked the microphone." : err.message);
    }
  }

  ui.session.addEventListener("click", () => act(async () => {
    if (client.session) { await client.stop(); ui.list.replaceChildren(); segments.clear(); render(); return; }
    await client.start();
    await refreshStatus();
    const t = await transport.get("transcripts", { session: client.session });
    for (const s of t.segments.reverse()) upsertSegment(s);
  }));
  ui.mic.addEventListener("click", () => act(async () => { if (client.micOn) await client.micOff(); else await micOn(); }));
  ui.mute.addEventListener("click", () => client.setMuted(!client.outputMuted));
  ui.volume.addEventListener("input", () => client.setVolume(ui.volume.value));
  ui.modes.addEventListener("change", (e) => act(() => client.setMode(e.target.value)));
  ui.voice.addEventListener("change", () => act(() => transport.post("settings", { voice: ui.voice.value })));
  ui.input.addEventListener("change", () => act(() => client.setInput(ui.input.value)));
  ui.showTranscript.addEventListener("change", () => { render(); act(() => transport.post("settings", { transcript_visible: ui.showTranscript.checked })); });
  if (owner) {
    ui.retain.addEventListener("change", () => act(() => transport.post("settings", { retain_audio: ui.retain.checked })));
    ui.ttl.addEventListener("change", () => act(() => transport.post("settings", { audio_ttl_hours: Number(ui.ttl.value) })));
  }
  ui.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = ui.text.value.trim();
    if (!text) return;
    act(async () => {
      if (!client.session) await client.start();
      await client.say(text);
      ui.text.value = "";
    });
  });

  client.addEventListener("state", render);
  client.addEventListener("segment", (e) => upsertSegment(e.detail));
  client.addEventListener("notice", (e) => notice(e.detail));
  client.addEventListener("forgotten", () => { ui.list.replaceChildren(); segments.clear(); });
  client.addEventListener("handoff_granted", () => act(() => micOn()));
  client.addEventListener("event", (e) => {
    const ev = e.detail;
    if (ev.type === "progress") notice(ev.text);
    else if (ev.type === "error") notice(ev.message);
    else if (ev.type === "notice") notice(ev.text);
    else if (ev.type === "confirm" || ev.type === "interrupted") render();
  });

  if (pauseInBackground) {   // mobile browsers suspend background pages: stop capture rather than fail silently
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && client.micOn) client.micOff("Microphone paused: the page went to the background.");
    });
  }
  window.addEventListener("pagehide", () => { if (client.micOn) client.micOff(); });

  const timer = setInterval(() => { if (!document.hidden) refreshStatus(); }, 15000);
  refreshStatus();
  render();
  const api = { client, refreshStatus, ui, stop: () => clearInterval(timer) };
  root.__nsfVoice = api;
  window.__nsfVoice = api;   // read-only handle for diagnostics and the end-to-end tests
  return api;
}
