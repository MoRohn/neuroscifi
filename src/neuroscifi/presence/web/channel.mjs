// State adapter: exactly one channel per page (docs/presence/PRESENCE_CORTEX_ARCHITECTURE.md §Channel).
// Console: a long-poll (GET /api/presence?since=&wait=20) — one request open at a time, answered as soon as the
// state changes. Pocket: a conditional POST /api/presence {"since"} every 2 s while something runs, 10 s when idle.
// Both stop while the page is hidden and back off on errors. Status: connecting | ok | disconnected | locked.

const sleep = (ms, signal) => new Promise((resolve) => {
  const id = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(id); resolve(); }, { once: true });
});

class Channel {
  constructor({ token, onState, onStatus }) {
    this.token = token;
    this.onState = onState;
    this.onStatus = onStatus;
    this.version = null;
    this.running = false;
    this.abort = null;
    this.status = 'connecting';
    this.requests = 0;
    this.backoff = 1000;
    this.onVisibility = () => { if (document.hidden) this.pause(); else this.resume(); };
  }

  setStatus(status) { if (status !== this.status) { this.status = status; this.onStatus?.(status); } }

  start() {
    if (this.running) return;
    this.running = true;
    document.addEventListener('visibilitychange', this.onVisibility);
    if (!document.hidden) this.resume();
  }

  stop() {
    this.running = false;
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.pause();
  }

  pause() { this.abort?.abort(); this.abort = null; }

  resume() {
    if (!this.running || this.abort || document.hidden) return;
    const ctl = new AbortController();
    this.abort = ctl;
    this.loop(ctl.signal).finally(() => { if (this.abort === ctl) this.abort = null; });
  }

  async loop(signal) {
    while (!signal.aborted && this.running) {
      const token = this.token();
      if (!token) { this.setStatus('locked'); return; }
      let delay = 0;
      try {
        const body = await this.request(token, signal);
        this.requests++;
        this.backoff = 1000;
        this.setStatus('ok');
        if (body && !body.unchanged && body.version) { this.version = body.version; this.onState(body); }
        delay = this.pace(body);
      } catch (error) {
        if (signal.aborted) return;
        if (error.status === 403) { this.setStatus('locked'); return; }
        this.setStatus('disconnected');
        delay = this.backoff;
        this.backoff = Math.min(30000, this.backoff * 2);
      }
      if (delay) await sleep(delay, signal);
    }
  }
}

export class ConsoleChannel extends Channel {
  constructor(options) { super(options); this.wait = options.wait ?? 20; }

  async request(token, signal) {
    const q = new URLSearchParams({ wait: String(this.version ? this.wait : 0) });
    if (this.version) q.set('since', this.version);
    const response = await fetch(`/api/presence?${q}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store',
      credentials: 'omit', signal });
    if (!response.ok) throw Object.assign(new Error(`presence ${response.status}`), { status: response.status });
    return response.json();
  }

  pace() { return 0; }   // the server holds the request until something changes

  async requestScene(concept) {
    const token = this.token();
    if (!token) return null;
    const response = await fetch('/api/presence/scene', { method: 'POST', cache: 'no-store', credentials: 'omit',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ concept }) });
    return response.ok ? response.json() : null;
  }
}

export class PocketChannel extends Channel {
  constructor(options) { super(options); this.busyMs = options.busyMs ?? 2000; this.idleMs = options.idleMs ?? 10000; this.idle = true; }

  async request(token, signal) {
    const response = await fetch('/api/presence', { method: 'POST', cache: 'no-store', credentials: 'omit', signal,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(this.version ? { since: this.version } : {}) });
    if (!response.ok) throw Object.assign(new Error(`presence ${response.status}`), { status: response.status });
    const body = await response.json();
    if (!body.unchanged) this.idle = ['idle', 'resting'].includes(body.primary?.activity);
    return body;
  }

  pace() { return this.idle ? this.idleMs : this.busyMs; }
}
