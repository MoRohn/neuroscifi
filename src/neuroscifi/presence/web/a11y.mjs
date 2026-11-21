// Accessibility adapter (docs/presence/ACCESSIBILITY.md): live-region announcements and the reduced-motion decision.
// Polite for ordinary changes; assertive only for permission requests and errors. Announcements are coalesced (the
// latest wins) and rate-limited so rapid state changes never flood a screen reader.

export class Announcer {
  constructor(polite, assertive, { minGapMs = 1500 } = {}) {
    this.polite = polite;
    this.assertive = assertive;
    this.minGap = minGapMs;
    this.last = 0;
    this.lastText = '';
    this.timer = 0;
    this.pending = null;
  }

  say(text, { assertive = false } = {}) {
    if (text === this.lastText && !this.pending) return;
    this.pending = { text, assertive };
    if (this.timer) return;
    const wait = Math.max(0, this.last + this.minGap - Date.now());
    this.timer = setTimeout(() => this.flush(), wait);
  }

  flush() {
    this.timer = 0;
    const p = this.pending;
    this.pending = null;
    if (!p || p.text === this.lastText) return;
    this.last = Date.now();
    this.lastText = p.text;
    (p.assertive ? this.assertive : this.polite).textContent = p.text;
    (p.assertive ? this.polite : this.assertive).textContent = '';
  }

  dispose() { clearTimeout(this.timer); this.timer = 0; }
}

const reduceQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
export function systemReducedMotion() { return !!reduceQuery?.matches; }
export function onSystemReducedMotion(fn) { reduceQuery?.addEventListener?.('change', fn); }
export function reducedMotion(prefs) { return prefs.motion === 'reduced' || (prefs.motion === 'system' && systemReducedMotion()); }
