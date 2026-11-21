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

// A Renderer draws one SVG. A page may attach extra renderers to the same engine and frame loop (Player.add), e.g.
// the small status indicator: `keep` limits it to some slots (detail simplification) and `crop` frames the viewBox
// on them, following the moving geometry smoothly so the tiny figure stays legible without a hard cut.
export class Renderer {
  constructor(svg, { keep = null, keepWithoutEyes = keep, crop = false } = {}) {
    this.svg = svg;
    this.keep = keep ? new Set(keep) : null;
    this.keepWithoutEyes = keepWithoutEyes ? new Set(keepWithoutEyes) : null;
    this.crop = crop;
    this.vb = null;
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
  draw(engine, { snap = true } = {}) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const keep = engine.scene?.slots?.eyeL || !engine.scene ? this.keep : this.keepWithoutEyes;
    for (const [name, s] of engine.slots) {
      const path = this.paths.get(name);
      const visible = s.pos && s.opacity > 0.02 && (!keep || keep.has(name));
      if (visible && this.crop) {
        for (let i = 0; i < s.pos.length; i += 2) {
          const x = s.pos[i], y = s.pos[i + 1];
          if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
      const cls = visible ? `pc-t-${s.tone} pc-w-${s.width}${s.fill ? ' pc-fill' : ''}${s.dash ? ' pc-dash' : ''}` : 'pc-off';
      if (this.cls.get(name) !== cls) { path.setAttribute('class', cls); this.cls.set(name, cls); }
      if (!visible) continue;
      path.setAttribute('d', toPath(s.pos, s.closed));
      if (s.opacity < 0.995) path.setAttribute('opacity', s.opacity.toFixed(3));
      else if (path.hasAttribute('opacity')) path.removeAttribute('opacity');
    }
    if (this.crop && x1 > x0) {
      const side = Math.max(x1 - x0, y1 - y0) + 8, want = [(x0 + x1) / 2 - side / 2, (y0 + y1) / 2 - side / 2, side];
      this.vb = !this.vb || snap ? want : this.vb.map((v, i) => v + (want[i] - v) * 0.18);
      this.svg.setAttribute('viewBox', `${this.vb[0].toFixed(2)} ${this.vb[1].toFixed(2)} ${this.vb[2].toFixed(2)} ${this.vb[2].toFixed(2)}`);
    }
  }

  setBreathe(on) { this.svg.classList.toggle('pc-breathe', !!on); }
}

// The frame loop. stats are read by the e2e tests and PERFORMANCE_RESULTS.md (frame pacing, scripting per frame).
export class Player {
  constructor(engine, renderer, { fpsCap = 0 } = {}) {
    this.engine = engine;
    this.renderer = renderer;
    this.renderers = [renderer];   // one frame loop draws every view of the presence on the page
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

  add(renderer) { this.renderers.push(renderer); renderer.draw(this.engine); }

  draw(snap) { for (const r of this.renderers) r.draw(this.engine, { snap }); }

  renderOnce() {
    this.engine.step(1, performance.now() / 1000);
    this.draw(true);
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
    this.draw(false);
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
