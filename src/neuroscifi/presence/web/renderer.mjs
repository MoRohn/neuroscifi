// Presence renderer (docs/presence/PRESENCE_CORTEX_ARCHITECTURE.md): a fixed pool of SVG paths (one per slot, never
// re-created), colours by class (theme.css custom properties), one requestAnimationFrame chain at most, paused while
// the page is hidden or the element is off screen, stopped when the scene is settled and still.
import { SLOTS } from './contracts.mjs';
import { toPath } from './geometry.mjs';

const NS = 'http://www.w3.org/2000/svg';

export function createSvg(doc = document) {
  const svg = doc.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '-6 -6 112 112');
  svg.setAttribute('class', 'pc-svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  return svg;
}

export class Renderer {
  constructor(svg) {
    this.svg = svg;
    this.figure = svg.ownerDocument.createElementNS(NS, 'g');
    this.figure.setAttribute('class', 'pc-figure');
    this.paths = new Map();
    this.cls = new Map();
    for (const name of SLOTS) {
      const path = svg.ownerDocument.createElementNS(NS, 'path');
      path.setAttribute('data-slot', name);
      path.setAttribute('class', 'pc-off');
      this.figure.append(path);
      this.paths.set(name, path);
      this.cls.set(name, 'pc-off');
    }
    svg.append(this.figure);
  }

  // Draw engine positions (or, for a static scene, its resampled targets).
  draw(engine) {
    for (const [name, s] of engine.slots) {
      const path = this.paths.get(name);
      const visible = s.pos && s.opacity > 0.02;
      const cls = visible ? `pc-t-${s.tone} pc-w-${s.width}${s.fill ? ' pc-fill' : ''}${s.dash ? ' pc-dash' : ''}` : 'pc-off';
      if (this.cls.get(name) !== cls) { path.setAttribute('class', cls); this.cls.set(name, cls); }
      if (!visible) continue;
      path.setAttribute('d', toPath(s.pos, s.closed));
      if (s.opacity < 0.995) path.setAttribute('opacity', s.opacity.toFixed(3));
      else if (path.hasAttribute('opacity')) path.removeAttribute('opacity');
    }
  }

  setBreathe(on) { this.svg.classList.toggle('pc-breathe', !!on); }
}

// The frame loop. stats are read by the e2e tests and PERFORMANCE_RESULTS.md (frame pacing, scripting per frame).
export class Player {
  constructor(engine, renderer, { fpsCap = 0 } = {}) {
    this.engine = engine;
    this.renderer = renderer;
    this.raf = 0;
    this.visible = true;
    this.onscreen = true;
    this.fpsCap = fpsCap;
    this.lastTs = null;
    this.lastDraw = 0;
    this.stats = { frames: 0, loopStarts: 0, running: false, intervals: [], scriptMs: [], maxConcurrentLoops: 0 };
    this.tick = this.tick.bind(this);
  }

  get active() { return this.visible && this.onscreen; }

  start() {
    if (this.raf || !this.active) return;
    if (this.engine.reduced) { this.renderOnce(); return; }
    this.lastTs = null;
    this.stats.loopStarts++;
    this.stats.running = true;
    this.stats.maxConcurrentLoops = Math.max(this.stats.maxConcurrentLoops, 1);
    this.raf = requestAnimationFrame(this.tick);
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.stats.running = false;
  }

  renderOnce() {
    this.engine.step(1, performance.now() / 1000);
    this.renderer.draw(this.engine);
  }

  tick(ts) {
    this.raf = 0;
    if (!this.active) { this.stats.running = false; return; }
    if (this.fpsCap && this.lastTs !== null && ts - this.lastDraw < 1000 / this.fpsCap - 1) {
      this.raf = requestAnimationFrame(this.tick);
      return;
    }
    const t0 = performance.now();
    const dt = this.lastTs === null ? 1 / 60 : Math.min(0.05, (ts - this.lastTs) / 1000);
    if (this.lastTs !== null) push(this.stats.intervals, ts - this.lastTs);
    this.lastTs = ts;
    this.lastDraw = ts;
    const moving = this.engine.step(dt, ts / 1000);
    this.renderer.draw(this.engine);
    this.stats.frames++;
    push(this.stats.scriptMs, performance.now() - t0);
    if (moving) this.raf = requestAnimationFrame(this.tick);
    else this.stats.running = false;
  }

  setVisible(visible, onscreen = this.onscreen) {
    this.visible = visible;
    this.onscreen = onscreen;
    if (this.active) this.start(); else this.stop();
  }
}

function push(list, value) { list.push(value); if (list.length > 600) list.shift(); }
