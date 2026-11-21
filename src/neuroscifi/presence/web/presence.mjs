// Presence Cortex controller: channel -> interpreter -> planner -> motion engine -> renderer, plus the accessible
// status, the expand/collapse panel and the settings (theme, motion, motion intensity). Used by the console and
// Pocket (console-boot.mjs, pocket-boot.mjs); the gallery uses the same planner, engine and renderer.
import { DEFAULT_PREFS, PREFS_KEY, MOTION_PREFS, INTENSITY_PREFS, sizeClass } from './contracts.mjs';
import { interpret, INTERRUPT_MS } from './interpreter.mjs';
import { plan } from './planner.mjs';
import { MotionEngine } from './motion.mjs';
import { Renderer, Player, createSvg } from './renderer.mjs';
import { Announcer, reducedMotion, onSystemReducedMotion } from './a11y.mjs';
import { initTheme, themePref, setThemePref, THEME_PREFS } from './theme.mjs';
import { validateSpec } from './validator.mjs';

export const COMMIT_GAP_MS = 220;   // rapid state changes are coalesced: at most one scene commit per gap

export function loadPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
    return { motion: MOTION_PREFS.includes(raw.motion) ? raw.motion : DEFAULT_PREFS.motion,
      intensity: raw.intensity in INTENSITY_PREFS ? raw.intensity : DEFAULT_PREFS.intensity,
      modelDoodles: raw.modelDoodles === true };
  } catch { return { ...DEFAULT_PREFS }; }
}
function savePrefs(prefs) { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* not persisted */ } }

let uid = 0;
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? '' : String(v));
  e.append(...kids);
  return e;
};
const option = (value, text) => el('option', { value }, text);

export class Presence {
  constructor(host, { channel = null, modelDoodles = false } = {}) {
    initTheme();
    const id = `pc${++uid}`;
    this.host = host;
    this.channel = channel;
    this.allowModel = modelDoodles;
    this.prefs = loadPrefs();
    this.server = null;
    this.local = { voice: null, typingAt: 0, interactedAt: 0, connection: channel ? 'connecting' : 'ok' };
    this.intent = null;
    this.scene = null;
    this.size = 'compact';
    this.smoothed = 0;
    this.lastCommit = 0;
    this.commitTimer = 0;
    this.transitions = 0;
    this.specs = new Map();
    this.battery = false;
    this.engine = new MotionEngine();
    this.svg = createSvg();
    this.renderer = new Renderer(this.svg);
    this.player = new Player(this.engine, this.renderer);

    this.word = el('span', { class: 'pc-word', 'aria-hidden': 'true' }, 'Presence');
    this.name = el('span', { class: 'pc-vh' }, 'Presence');
    this.live = el('p', { id: `${id}-live`, class: 'pc-vh', 'aria-live': 'polite' });
    this.alert = el('p', { class: 'pc-vh', 'aria-live': 'assertive' });
    this.toggle = el('button', { type: 'button', class: 'pc-toggle', 'aria-expanded': 'false', 'aria-controls': `${id}-panel`,
      'aria-describedby': `${id}-live`, 'data-presence-toggle': true }, this.svg, this.name, this.word);
    this.stage = el('div', { class: 'pc-stage' });
    this.statusText = el('p', { class: 'pc-status' });
    this.facts = el('dl', { class: 'pc-facts' });
    const theme = el('select', { id: `${id}-theme` }, option('system', 'Follow system'), option('light', 'Light'), option('dark', 'Dark'));
    const motion = el('select', { id: `${id}-motion` }, option('system', 'Follow system'), option('reduced', 'Reduced (static)'), option('full', 'Full'));
    const intensity = el('select', { id: `${id}-intensity` }, option('low', 'Low'), option('normal', 'Normal'), option('high', 'High'));
    theme.value = themePref(); motion.value = this.prefs.motion; intensity.value = this.prefs.intensity;
    theme.addEventListener('change', () => { if (THEME_PREFS.includes(theme.value)) setThemePref(theme.value); });
    motion.addEventListener('change', () => this.setPrefs({ motion: motion.value }));
    intensity.addEventListener('change', () => this.setPrefs({ intensity: intensity.value }));
    const settings = el('div', { class: 'pc-settings' },
      el('label', { for: theme.id }, 'Theme'), theme,
      el('label', { for: motion.id }, 'Motion'), motion,
      el('label', { for: intensity.id }, 'Motion intensity'), intensity);
    if (modelDoodles) {
      const box = el('input', { type: 'checkbox', id: `${id}-model` });
      box.checked = this.prefs.modelDoodles;
      box.addEventListener('change', () => this.setPrefs({ modelDoodles: box.checked }));
      settings.append(el('label', { class: 'pc-check', for: box.id }, box, 'Let the local model propose doodles for new concepts (validated; off by default)'));
    }
    this.controls = { theme, motion, intensity };
    const close = el('button', { type: 'button', class: 'pc-close' }, 'Close');
    close.addEventListener('click', () => this.collapse(true));
    this.panel = el('div', { id: `${id}-panel`, class: 'pc-panel', role: 'region', 'aria-labelledby': `${id}-head`, hidden: true },
      el('h2', { id: `${id}-head` }, 'Presence'), this.stage, this.statusText, this.facts, settings, close);
    this.root = el('div', { class: 'nsf-presence', 'data-size': 'compact' }, this.toggle, this.live, this.alert, this.panel);
    host.replaceChildren(this.root);
    this.announcer = new Announcer(this.live, this.alert);

    this.toggle.addEventListener('click', () => (this.expanded ? this.collapse(false) : this.expand()));
    this.root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.expanded) { e.preventDefault(); this.collapse(true); } });
    this.onVoice = (e) => this.voice(e.detail ?? {});
    addEventListener('nsf:voice', this.onVoice);
    this.onInput = (e) => {
      if (this.root.contains(e.target)) return;
      if (e.target?.matches?.('input, textarea, [contenteditable]')) { this.local.typingAt = Date.now(); this.request(); }
    };
    document.addEventListener('input', this.onInput, true);
    this.onInteract = () => { const now = Date.now(); if (now - this.local.interactedAt > 5000) { this.local.interactedAt = now; this.request(); } };
    document.addEventListener('pointerdown', this.onInteract, true);
    document.addEventListener('keydown', this.onInteract, true);
    this.onVisibility = () => this.player.setVisible(!document.hidden);
    document.addEventListener('visibilitychange', this.onVisibility);
    this.svg.classList.toggle('pc-paused', document.hidden);
    if ('IntersectionObserver' in window) {
      this.io = new IntersectionObserver((entries) => {
        const on = entries.some((x) => x.isIntersecting);
        this.svg.classList.toggle('pc-paused', !on);
        this.player.setVisible(!document.hidden, on);
      });
      this.io.observe(this.svg);
    }
    if ('ResizeObserver' in window) {
      this.ro = new ResizeObserver(() => { const s = sizeClass(this.svg.getBoundingClientRect().width); if (s !== this.size) { this.size = s; this.root.dataset.size = s; this.request(true); } });
      this.ro.observe(this.svg);
    }
    onSystemReducedMotion(() => this.applyMotion());
    navigator.getBattery?.().then((b) => {
      const update = () => { this.battery = !b.charging && b.level < 0.2; this.player.fpsCap = this.battery ? 30 : 0; this.applyEnergy(); };
      b.addEventListener('levelchange', update); b.addEventListener('chargingchange', update); update();
    }).catch(() => {});
    this.applyMotion();
    if (channel) {
      channel.onState = (state) => { this.server = state; this.request(); };
      channel.onStatus = (status) => { this.local.connection = status; this.request(); };
      channel.start();
    }
    this.commit();
  }

  get expanded() { return this.toggle.getAttribute('aria-expanded') === 'true'; }

  expand() {
    this.toggle.setAttribute('aria-expanded', 'true');
    this.panel.hidden = false;
    this.stage.append(this.svg);             // the same SVG moves: nothing restarts
    this.controls.theme.value = themePref();
    this.renderPanel();
  }

  collapse(focus) {
    this.toggle.setAttribute('aria-expanded', 'false');
    this.panel.hidden = true;
    this.toggle.prepend(this.svg);
    if (focus) this.toggle.focus();
  }

  setPrefs(change) {
    this.prefs = { ...this.prefs, ...change };
    savePrefs(this.prefs);
    this.applyMotion();
    if ('modelDoodles' in change) this.request(true);
  }

  applyMotion() {
    const reduced = reducedMotion(this.prefs);
    const was = this.engine.reduced;
    this.engine.reduced = reduced;
    this.svg.classList.toggle('pc-static', reduced);
    this.applyEnergy();
    if (reduced && !was) { this.player.stop(); this.player.renderOnce(); }
    if (!reduced) this.player.start();
  }

  applyEnergy() {
    const pref = INTENSITY_PREFS[this.prefs.intensity] ?? 1;
    const subtle = this.intent?.subtle ? 0.5 : 1;
    this.engine.energy = pref * subtle * (this.battery ? 0.5 : 1) * (0.35 + 0.65 * this.smoothed);
  }

  // Real voice activity from Lane V's page (event contract: docs/presence/VOICE_INTEGRATION.md).
  voice(d) {
    const now = Date.now(), prev = this.local.voice;
    const v = { mic_capturing: d.mic_capturing === true, speaking: d.speaking === true, output_muted: d.output_muted === true,
      mode: ['voice_text', 'voice_in_text_out', 'text_only'].includes(d.mode) ? d.mode : 'voice_text',
      level: Number.isFinite(d.level) ? Math.max(0, Math.min(1, d.level)) : null, at: now,
      micSince: prev?.mic_capturing ? prev.micSince : now, speakingSince: prev?.speaking ? prev.speakingSince : now,
      interruptedAt: prev?.interruptedAt ?? 0 };
    if (prev?.speaking && !v.speaking && v.mic_capturing) v.interruptedAt = now;   // barge-in: output stopped, mic on
    const changed = !prev || ['mic_capturing', 'speaking', 'output_muted', 'mode'].some((k) => prev[k] !== v[k]);
    this.local.voice = v;
    this.engine.level = this.intent?.audio ? (v.mode === 'text_only' || (this.intent.audio === 'output' && v.output_muted) ? null : v.level) : null;
    if (changed) {
      this.request();
      if (v.interruptedAt === now) setTimeout(() => this.request(), INTERRUPT_MS + 50);
    } else if (this.engine.level !== null) this.player.start();
  }

  // Coalesce requests: commit now, or at the end of the current gap (latest state wins).
  request(force = false) {
    if (force) this.forceNext = true;
    if (this.commitTimer) return;
    const wait = Math.max(0, this.lastCommit + COMMIT_GAP_MS - performance.now());
    this.commitTimer = setTimeout(() => { this.commitTimer = 0; this.commit(); }, wait);
  }

  commit() {
    this.lastCommit = performance.now();
    const intent = interpret(this.server, this.local, Date.now());
    this.smoothed += ((intent.intensity ?? 0) - this.smoothed) * 0.5;
    this.intent = intent;
    this.applyEnergy();
    const v = this.local.voice;
    this.engine.level = intent.audio && v && v.mode !== 'text_only' && !(intent.audio === 'output' && v.output_muted) ? v.level : null;
    const spec = this.prefs.modelDoodles && this.allowModel ? this.specs.get(intent.concept)?.spec ?? null : null;
    const sig = JSON.stringify([intent.concept, this.size, intent.condition, intent.subtle, intent.others.map((o) => o.priority), !!spec]);
    if (sig !== this.sig || this.forceNext) {
      this.forceNext = false;
      this.sig = sig;
      const scene = plan(intent, { size: this.size, spec });
      this.scene = scene;
      this.transitions++;
      const t = performance.now() / 1000;
      this.engine.setScene(scene, { t, instant: this.engine.reduced || !this.scene0 });
      this.scene0 = true;
      this.renderer.setBreathe(scene.breathe && !this.engine.reduced);
      this.root.dataset.concept = scene.concept;
      this.root.dataset.tier = scene.tier;
      if (this.engine.reduced) this.player.renderOnce(); else this.player.start();
      this.maybeAskModel(intent, scene);
    } else if (!this.engine.reduced) this.player.start();
    this.root.dataset.activity = intent.activity;
    this.word.textContent = intent.short;
    this.name.textContent = `Presence: ${intent.short}. Show details`;
    this.announcer.say(intent.text, { assertive: intent.assertive });
    if (this.expanded) this.renderPanel();
  }

  async maybeAskModel(intent, scene) {
    if (!this.allowModel || !this.prefs.modelDoodles || !this.channel?.requestScene) return;
    if (scene.tier !== 'B' || scene.template || this.specs.has(intent.concept)) return;
    this.specs.set(intent.concept, { pending: true });
    const reply = await this.channel.requestScene(intent.concept).catch(() => null);
    const ok = reply?.status === 'ok' && validateSpec(reply.spec).length === 0;
    this.specs.set(intent.concept, ok ? { spec: reply.spec } : { failed: reply?.status ?? 'error' });
    if (ok && this.intent?.concept === intent.concept) this.request(true);
  }

  renderPanel() {
    const i = this.intent;
    if (!i) return;
    this.statusText.textContent = i.text;
    const rows = [['Activity', i.short], ['Source', i.subsystem ? i.subsystem.replace('_', ' ') : i.unknown ? 'not connected yet' : '—'],
      ['Progress', i.step ? `stage ${i.step.index} of ${i.step.of} (no percentage is known)` : 'not measured'],
      ['Intensity', i.intensity >= 0.6 ? 'high' : i.intensity >= 0.25 ? 'moderate' : 'low'],
      ['Conditions', i.condition.length ? i.condition.join(', ').replaceAll('_', ' ') : 'healthy'],
      ['Composition', `${this.scene?.concept ?? '—'} (tier ${this.scene?.tier ?? '—'})`]];
    this.facts.replaceChildren(...rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)]));
  }

  stats() {
    return { transitions: this.transitions, concept: this.scene?.concept, tier: this.scene?.tier, activity: this.intent?.activity,
      level: this.engine.level, reduced: this.engine.reduced, energy: this.engine.energy, maxSpeed: this.engine.maxSpeed,
      requests: this.channel?.requests ?? 0, connection: this.local.connection, ...this.player.stats,
      intervals: undefined, scriptMs: undefined, paths: this.svg.querySelectorAll('path').length };
  }

  destroy() {
    this.channel?.stop();
    this.player.stop();
    this.announcer.dispose();
    clearTimeout(this.commitTimer);
    removeEventListener('nsf:voice', this.onVoice);
    document.removeEventListener('input', this.onInput, true);
    document.removeEventListener('pointerdown', this.onInteract, true);
    document.removeEventListener('keydown', this.onInteract, true);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.io?.disconnect(); this.ro?.disconnect();
    this.host.replaceChildren();
  }
}

// Read-only hooks for the e2e tests and PERFORMANCE_RESULTS.md (frame pacing samples, counters); no setters.
export function expose(presence) {
  window.__nsfPresence = {
    stats: () => presence.stats(),
    frames: () => ({ intervals: presence.player.stats.intervals.slice(), scriptMs: presence.player.stats.scriptMs.slice() }),
    intent: () => JSON.parse(JSON.stringify(presence.intent)),
    scene: () => ({ concept: presence.scene?.concept, tier: presence.scene?.tier, fallbackReason: presence.scene?.fallbackReason ?? null }),
  };
}
