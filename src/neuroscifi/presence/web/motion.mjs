// Physics & motion engine (docs/presence/MOTION_ENGINE.md). Every slot is a point set whose points follow their
// targets on damped springs (semi-implicit Euler, sub-stepped), with velocity and acceleration limits. A new scene
// only changes targets: positions and velocities carry over, so an interruption never snaps. Shapes that appear
// grow out of a point and shapes that leave shrink into one (an intermediate abstraction, not a crossfade).
// Continuous motion (breathing, orbits, scanning, audio level) is applied to the targets, never to positions.
import { SLOTS, BUDGET, pointsFor } from './contracts.mjs';
import { align, collapsed, centroid } from './geometry.mjs';

const TAU = Math.PI * 2;

export class MotionEngine {
  constructor({ stiffness = 140, damping = 0.8 } = {}) {
    this.k = stiffness;
    this.c = 2 * damping * Math.sqrt(stiffness);
    this.slots = new Map();
    this.energy = 1;
    this.level = null;
    this.reduced = false;
    this.sceneAt = 0;
    this.maxSpeed = 0;
    this.scene = null;
    for (const name of SLOTS) {
      const n = pointsFor(name) * 2;
      this.slots.set(name, { name, pos: null, vel: new Float64Array(n), base: null, target: new Float64Array(n),
        closed: false, opacity: 0, opTarget: 0, anims: [], animState: [], tone: 'ink', width: 'bold', fill: false,
        dash: false, cx: 50, cy: 50 });
    }
  }

  // Assign a planned scene. instant: jump (reduced motion, first paint).
  setScene(scene, { t = 0, instant = false } = {}) {
    this.scene = scene;
    this.sceneAt = t;
    for (const [name, s] of this.slots) {
      const next = scene.slots[name];
      const show = !!next && next.visible !== false;
      if (show) {
        if (!s.pos) {
          s.pos = collapsed(pointsFor(name), next.cx, next.cy);   // appear: grow out of a point
          s.vel.fill(0);
        }
        s.base = align(s.pos, next.pts, next.closed);
        s.closed = next.closed;
        s.opTarget = 1;
        s.tone = next.tone; s.width = next.width; s.dash = next.dash;
        s.fillWanted = next.fill;
        if (s.opacity < 0.05) s.fill = next.fill;
        s.anims = next.anims ?? [];
        s.animState = s.anims.map(() => ({ phase: 0 }));
        s.cx = next.cx; s.cy = next.cy;
      } else if (s.pos) {
        const [cx, cy] = centroid(s.pos);
        s.base = collapsed(pointsFor(name), cx, cy);                // leave: shrink into a point
        s.opTarget = 0;
        s.anims = []; s.animState = [];
        s.fillWanted = false;
      }
      if (instant && s.base) {
        s.pos = Float64Array.from(s.base);
        s.vel.fill(0);
        s.opacity = s.opTarget;
        s.fill = !!s.fillWanted;
        if (s.opTarget === 0) s.pos = null;
      }
    }
  }

  animatedSlots() {
    let n = 0;
    for (const s of this.slots.values()) if (s.opTarget > 0 && s.anims.length) n++;
    return n;
  }

  // True while some visible slot has motion with a real amplitude (decayed motion and level-driven motion without a
  // measured level do not count), so a settled, still scene stops the frame loop.
  hasContinuousMotion(t = this.sceneAt) {
    if (this.reduced) return false;
    const age = t - this.sceneAt;
    for (const s of this.slots.values()) {
      if (s.opTarget > 0 && s.anims.some((a) => !(a.drive === 'level' && this.level === null)
          && !(a.decay && Math.exp(-age / a.decay) < 0.01))) return true;
    }
    return false;
  }

  // Animated target for one slot at time t (in seconds).
  _target(s, t, dt) {
    const out = s.target, base = s.base;
    out.set(base);
    if (this.reduced || !s.anims.length) return out;
    const E = this.energy;
    const age = t - this.sceneAt;
    s.anims.forEach((a, i) => {
      const st = s.animState[i];
      const decay = a.decay ? Math.exp(-age / a.decay) : 1;
      const ph = a.phase ?? 0;
      const wave = (period) => Math.sin(TAU * (t / period + ph));
      const ox = a.ox ?? s.cx, oy = a.oy ?? s.cy;
      switch (a.k) {
        case 'swing': rotate(out, ox, oy, ((a.amp * Math.PI) / 180) * E * decay * wave(a.period)); break;
        case 'spin': st.phase += Math.min(a.speed, BUDGET.maxRotationSpeed) * E * decay * dt; rotate(out, ox, oy, st.phase); break;
        case 'orbit': {
          st.phase += a.speed * Math.max(0.4, E) * decay * dt;
          const [cx, cy] = [s.cx, s.cy];
          const ang = (a.a0 ?? -Math.PI / 2) + st.phase;
          translate(out, ox + Math.cos(ang) * a.r - cx, oy + Math.sin(ang) * a.r - cy);
          break;
        }
        case 'shift': { const w = wave(a.period) * E * decay; translate(out, (a.dx ?? 0) * w, (a.dy ?? 0) * w); break; }
        case 'bob': translate(out, 0, a.amp * E * decay * wave(a.period)); break;
        case 'twinkle': { const f = 1 + a.amp * E * decay * wave(a.period); scale(out, s.cx, s.cy, f, f); break; }
        case 'level': if (this.level !== null) scale(out, s.cx, s.cy, 1, a.min + (a.max - a.min) * this.level); break;
        case 'dots': {
          const signal = this.level !== null ? this.level : Math.min(1, E);
          translate(out, 0, -a.amp * signal * Math.abs(Math.sin(TAU * (t / 0.75 + ph))));
          break;
        }
        case 'converge': { const w = E * decay * (0.5 + 0.5 * Math.cos(TAU * (t / a.period + ph))); translate(out, a.dx * w, a.dy * w); break; }
        default: break;
      }
    });
    return out;
  }

  // Advance by dt seconds; returns true while anything still moves (the caller keeps its frame loop only then).
  step(dt, t) {
    let moving = false;
    const vmax = BUDGET.maxVelocity, amax = BUDGET.maxAcceleration;
    const subs = Math.max(1, Math.ceil(dt / (1 / 120)));
    const h = dt / subs;
    for (const s of this.slots.values()) {
      if (!s.pos) continue;
      const target = this._target(s, t, dt);
      if (this.reduced) {
        s.pos.set(target); s.vel.fill(0); s.opacity = s.opTarget; s.fill = !!s.fillWanted;
        if (s.opTarget === 0) s.pos = null;
        continue;
      }
      const p = s.pos, v = s.vel;
      let maxD = 0, maxV = 0;
      for (let j = 0; j < subs; j++) {
        for (let i = 0; i < p.length; i++) {
          let a = this.k * (target[i] - p[i]) - this.c * v[i];
          if (a > amax) a = amax; else if (a < -amax) a = -amax;
          let vi = v[i] + a * h;
          if (vi > vmax) vi = vmax; else if (vi < -vmax) vi = -vmax;
          v[i] = vi;
          p[i] += vi * h;
        }
      }
      for (let i = 0; i < p.length; i++) {
        const d = Math.abs(target[i] - p[i]), av = Math.abs(v[i]);
        if (d > maxD) maxD = d;
        if (av > maxV) maxV = av;
      }
      if (maxV > this.maxSpeed) this.maxSpeed = maxV;
      const dop = s.opTarget - s.opacity;
      s.opacity += dop * Math.min(1, dt * (s.opTarget > s.opacity ? 7 : 5));
      if (Math.abs(s.opTarget - s.opacity) < 0.01) s.opacity = s.opTarget;
      if (s.fill !== s.fillWanted && maxD < 2.5) s.fill = !!s.fillWanted;
      if (s.opTarget === 0 && s.opacity === 0 && maxD < 0.3) { s.pos = null; s.vel.fill(0); continue; }
      if (maxD > 0.02 || maxV > 0.05 || s.opacity !== s.opTarget || s.fill !== !!s.fillWanted) moving = true;
    }
    return moving || this.hasContinuousMotion(t);
  }
}

function rotate(p, ox, oy, a) {
  if (!a) return;
  const c = Math.cos(a), s = Math.sin(a);
  for (let i = 0; i < p.length; i += 2) {
    const x = p[i] - ox, y = p[i + 1] - oy;
    p[i] = ox + x * c - y * s; p[i + 1] = oy + x * s + y * c;
  }
}
function translate(p, dx, dy) { for (let i = 0; i < p.length; i += 2) { p[i] += dx; p[i + 1] += dy; } }
function scale(p, ox, oy, sx, sy) { for (let i = 0; i < p.length; i += 2) { p[i] = ox + (p[i] - ox) * sx; p[i + 1] = oy + (p[i + 1] - oy) * sy; } }
