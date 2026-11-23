// Presence renderer (docs/presence/PRESENCE_CORTEX_ARCHITECTURE.md): a fixed pool of SVG paths (one per slot, never
// re-created), colours by class (theme.css custom properties), one requestAnimationFrame chain at most, paused while
// the page is hidden or the element is off screen, and capped at LIFE.ambientFps while only the life layer moves.
import { SLOTS } from './contracts.mjs';
import { toPath } from './geometry.mjs';
import { LIFE } from './motion.mjs';

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
// `halo`: the light of the entity on an interaction stage, a soft radial glow behind the figure whose strength and
// reach follow engine.glow() (the activity, the voice, real stage changes); theme.css shows it only on a stage.
let haloId = 0;
export class Renderer {
  constructor(svg, { keep = null, keepWithoutEyes = keep, crop = false, halo = false } = {}) {
    this.svg = svg;
    this.keep = keep ? new Set(keep) : null;
    this.keepWithoutEyes = keepWithoutEyes ? new Set(keepWithoutEyes) : null;
    this.crop = crop;
    this.vb = null;
    this.halo = null;
    if (halo) {
      const doc = svg.ownerDocument, id = `pc-halo-${++haloId}`;
      const defs = doc.createElementNS(NS, 'defs'), grad = doc.createElementNS(NS, 'radialGradient');
      grad.setAttribute('id', id);
      for (const [offset, cls] of [['0', 'pc-halo-0'], ['0.55', 'pc-halo-1'], ['1', 'pc-halo-2']]) {
        const stop = doc.createElementNS(NS, 'stop');
        stop.setAttribute('offset', offset);
        stop.setAttribute('class', cls);
        grad.append(stop);
      }
      defs.append(grad);
      this.halo = doc.createElementNS(NS, 'circle');
      this.halo.setAttribute('class', 'pc-halo');
      this.halo.setAttribute('cx', '50');
      this.halo.setAttribute('cy', '50');
      this.halo.setAttribute('fill', `url(#${id})`);
      this.haloAt = -1;
      svg.append(defs, this.halo);
    }
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
      const p = visible ? engine.view(s) : null;
      if (visible && this.crop) {
        for (let i = 0; i < p.length; i += 2) {
          const x = p[i], y = p[i + 1];
          if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
      const cls = visible ? `pc-t-${s.tone} pc-w-${s.width}${s.fill ? ' pc-fill' : ''}${s.dash ? ' pc-dash' : ''}` : 'pc-off';
      if (this.cls.get(name) !== cls) { path.setAttribute('class', cls); this.cls.set(name, cls); }
      if (!visible) continue;
      path.setAttribute('d', toPath(p, s.closed));
      if (s.opacity < 0.995) path.setAttribute('opacity', s.opacity.toFixed(3));
      else if (path.hasAttribute('opacity')) path.removeAttribute('opacity');
    }
    if (this.crop && x1 > x0) {
      const side = Math.max(x1 - x0, y1 - y0) + 8, want = [(x0 + x1) / 2 - side / 2, (y0 + y1) / 2 - side / 2, side];
      this.vb = !this.vb || snap ? want : this.vb.map((v, i) => v + (want[i] - v) * 0.18);
      this.svg.setAttribute('viewBox', `${this.vb[0].toFixed(2)} ${this.vb[1].toFixed(2)} ${this.vb[2].toFixed(2)} ${this.vb[2].toFixed(2)}`);
    }
    if (this.halo) {
      const g = Math.round(engine.glow() * 400) / 400;
      if (g !== this.haloAt) {
        this.haloAt = g;
        this.halo.setAttribute('r', (46 + 10 * g).toFixed(2));   // <= 56: inside the viewBox, never clipped
        this.halo.setAttribute('opacity', (0.35 + 0.65 * g).toFixed(3));
      }
    }
  }
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
    this.mode = 'physics';   // what moved last frame: physics (display rate), responsive (60 fps cap), ambient (30)
    this.stats = { frames: 0, ambientFrames: 0, loopStarts: 0, running: false, settled: false, intervals: [], scriptMs: [],
      maxConcurrentLoops: 0 };
    this.tick = this.tick.bind(this);
  }

  get active() { return this.visible && this.onscreen; }

  start() {
    if (this.raf || !this.active) return;
    if (this.engine.reduced) { this.renderOnce(); return; }
    this.engine.rest?.();   // impulses that arrived while the loop was paused belong to a moment nobody saw
    this.mode = 'physics';
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
    const lim = this.mode === 'ambient' ? LIFE.ambientFps : this.mode === 'responsive' ? LIFE.responsiveFps : 0;
    const cap = lim ? Math.min(this.fpsCap || lim, lim) : this.fpsCap;
    if (cap && this.lastTs !== null && ts - this.lastDraw < 1000 / cap - 1) {
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
    if (this.mode === 'ambient') this.stats.ambientFrames++;
    push(this.stats.scriptMs, performance.now() - t0);
    this.mode = moving ? 'physics' : this.engine.excited ? 'responsive' : 'ambient';
    this.stats.settled = !moving;
    if (moving || this.engine.alive) this.raf = requestAnimationFrame(this.tick);
    else this.stats.running = false;
  }

  setVisible(visible, onscreen = this.onscreen) {
    this.visible = visible;
    this.onscreen = onscreen;
    if (this.active) this.start(); else this.stop();
  }
}

function push(list, value) { list.push(value); if (list.length > 600) list.shift(); }
