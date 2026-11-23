// Physics & motion engine (docs/presence/MOTION_ENGINE.md). Every slot is a point set whose points follow their
// targets on damped springs (semi-implicit Euler, sub-stepped), with velocity and acceleration limits. A new scene
// only changes targets: positions and velocities carry over, so an interruption never snaps. Shapes that appear
// grow out of a point and shapes that leave shrink into one (an intermediate abstraction, not a crossfade).
// Continuous motion (orbits, scanning, audio level) is applied to the targets, never to positions.
// On top of the physics sits the life layer (LIFE below): a continuous field added at display time, so the entity is
// never frozen while it is on screen, yet the springs keep their settling and no-snapping guarantees.
import { SLOTS, BUDGET, pointsFor } from './contracts.mjs';
import { align, collapsed, centroid, bbox } from './geometry.mjs';

const TAU = Math.PI * 2;
const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

// The life layer (docs/presence/MOTION_ENGINE.md §Life layer). Every input is real: the intensity of the actual
// activity, the measured audio level, the owner's pointer, scrolling and typing, the window's focus and real stage
// transitions. Nothing here invents activity: at idle the contour moves by about 1 % of the body radius and the
// breath is slower than 6 s. Fractions are of the body radius unless stated.
export const LIFE = Object.freeze({
  contourIdle: 0.011, contourActive: 0.03, contourLevel: 0.055,   // radial deformation of the body outline
  breathIdle: 0.012, breathActive: 0.022,                         // vertical scale about the ground contact
  periodIdle: 6.4, periodActive: 2.9,                             // breathing period, seconds
  gazeEye: 0.08, gazeLean: 0.035,                                 // pointer attention: eyes, then the whole body
  float: 0.55,                                                    // drift of the props, viewBox units (x vigour)
  swayUp: 5, swayDown: 1.2,                                       // scroll inertia: soft limits, viewBox units
  ripple: 0.05, rippleS: 0.55,                                    // a real stage change: one wave round the outline
  levelSwell: 0.045,                                              // the measured audio level swells the figure
  dim: 0.3,                                                       // vigour while disconnected or not yet known
  ambientFps: 30,                                                 // frame cap while only the life layer moves
  responsiveFps: 60,                                              // frame cap while it answers an input event
});
const ENV0 = () => ({ act: 0, lvl: 0, amp: 0, bAmp: 0, breath: 0, ph: [0, 1.7, 3.1], gx: 0, gy: 0, tx: 0, ty: 0, hold: 0,
  swayT: 0, sway: 0, swayV: 0, squash: 0, squashV: 0, rippleT: 0, rippleA: 0, calm: 1, calmT: 1, glow: 0.5,
  ax: 50, ay: 88, bcx: 50, bcy: 55, rb: 30, want: null });
const finite = (v, fallback = 0) => (Number.isFinite(v) ? v : fallback);

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
    this.life = true;          // the life layer (off under reduced motion, see alive)
    this.activity = 0;         // target: intensity of the real activity, 0..1 (smoothed in _life)
    this.vigor = 1;            // the owner's motion intensity x background subtlety x battery saver
    this.excited = false;      // the life layer is responding to an event (pointer, scroll, typing, voice, stage)
    this.dim = false;          // disconnected or not yet known: a faint breath and a low light, no attention
    this.stepNo = 0;
    this.env = ENV0();
    SLOTS.forEach((name, i) => {
      const n = pointsFor(name) * 2;
      this.slots.set(name, { name, index: i, pos: null, vel: new Float64Array(n), base: null, target: new Float64Array(n),
        disp: new Float64Array(n), lifeAt: -1, closed: false, opacity: 0, opTarget: 0, anims: [], animState: [],
        tone: 'ink', width: 'bold', fill: false, dash: false, cx: 50, cy: 50 });
    });
  }

  // True while the life layer runs: the frame loop keeps going (at LIFE.ambientFps when nothing else moves).
  get alive() { return this.life && !this.reduced && !!this.scene; }

  // ---- environment inputs (Presence feeds these from real events; none re-plans the scene) ----------------------
  // Ignored while the life layer is off (reduced motion), so nothing can pile up and land later; rest() drops what
  // arrived while the loop was paused (hidden page, off screen).
  look(x, y, hold = 6) {   // pointer direction, -1..1; back to rest after `hold` seconds without a new look
    if (!this.alive || this.dim) return;
    this.env.tx = clamp(finite(x), -1, 1); this.env.ty = clamp(finite(y), -1, 1); this.env.hold = finite(hold, 0);
  }
  release() { this.env.tx = 0; this.env.ty = 0; this.env.hold = 0; }
  kick(v) { if (this.alive) this.env.swayT = clamp(this.env.swayT + 0.02 * finite(v), -5, 5); }   // scroll impulse
  tap(f = 1) { if (this.alive) this.env.squashV = clamp(this.env.squashV + 5 * finite(f), -12, 12); }   // a keystroke
  pulse() { if (this.alive) { this.env.rippleT = 0; this.env.rippleA = 1e-6; } }      // a real stage transition
  focus(on) { this.env.calmT = on ? 1 : 0.55; }                                    // window focus
  rest() {
    const e = this.env;
    e.swayT = e.sway = e.swayV = 0; e.squash = e.squashV = 0; e.rippleA = 0; e.rippleT = 0;
    this.release(); e.gx = e.gy = 0;
  }

  // What to draw for a slot: the physics position plus the life field computed in the latest step.
  view(s) { return this.alive && s.lifeAt === this.stepNo ? s.disp : s.pos; }

  // Glow of the light halo, 0..1: the activity, the voice, a stage change; a slow breath at rest.
  glow() { return this.alive ? clamp(this.env.glow + 0.05 * Math.sin(this.env.breath), 0, 1) : 0.55; }

  // Assign a planned scene. instant: jump (reduced motion, first paint).
  setScene(scene, { t = 0, instant = false } = {}) {
    this.scene = scene;
    this.sceneAt = t;
    const body = scene.slots.body;
    if (body?.pts) {   // the life layer breathes about the ground contact and deforms about the body's centre
      const [x0, y0, x1, y1] = bbox(body.pts);
      const want = [(x0 + x1) / 2, y1, (x0 + x1) / 2, (y0 + y1) / 2, Math.max(4, Math.max(x1 - x0, y1 - y0) / 2)];
      if (want.every(Number.isFinite)) {
        this.env.want = want;
        if (instant || !this.stepNo) [this.env.ax, this.env.ay, this.env.bcx, this.env.bcy, this.env.rb] = want;
      }
    }
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
      s.lifeAt = -1;
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
  // The returned flag covers the physics only (springs, opacity, scene animations); the life layer keeps the loop
  // going on its own (alive), so "settled" still means what it says.
  step(dt, t) {
    let moving = false;
    const vmax = BUDGET.maxVelocity, amax = BUDGET.maxAcceleration;
    const subs = Math.max(1, Math.ceil(dt / (1 / 120)));
    const h = dt / subs;
    const alive = this.alive;
    this.stepNo++;
    if (alive) this._life(dt);
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
      if (alive) this._display(s);
    }
    return moving || this.hasContinuousMotion(t);
  }

  // Advance the life layer's own state once per step: smoothed inputs, phases, and two small damped springs (scroll
  // inertia and the squash of a keystroke). Everything is integrated, so no input can make the figure jump.
  _life(dt) {
    const L = LIFE, e = this.env, d = Math.min(finite(dt, 0), 0.05);
    const ease = (rate) => Math.min(1, d * rate);
    if (e.want) {
      const w = e.want, k = ease(4);
      e.ax += (w[0] - e.ax) * k; e.ay += (w[1] - e.ay) * k; e.bcx += (w[2] - e.bcx) * k; e.bcy += (w[3] - e.bcy) * k;
      e.rb += (w[4] - e.rb) * k;
    }
    e.calm += (e.calmT * (this.dim ? L.dim : 1) - e.calm) * ease(2);
    e.act += (clamp(finite(this.activity), 0, 1) - e.act) * ease(1.2);
    const lv = clamp(finite(this.level ?? 0), 0, 1);
    e.lvl += (lv - e.lvl) * ease(lv > e.lvl ? 18 : 6);
    const vig = clamp(finite(this.vigor, 1), 0, 1.6) * e.calm;
    e.amp += ((L.contourIdle + (L.contourActive - L.contourIdle) * e.act + L.contourLevel * e.lvl) * vig - e.amp) * ease(1.5);
    e.bAmp += ((L.breathIdle + (L.breathActive - L.breathIdle) * e.act) * vig - e.bAmp) * ease(1.5);
    const speed = (1 + 1.4 * e.act + 2.2 * e.lvl) * (0.6 + 0.4 * e.calm);
    const periods = [9.1, 6.3, 4.3];
    for (let k = 0; k < 3; k++) e.ph[k] = (e.ph[k] + (d * TAU * speed) / periods[k]) % TAU;
    e.breath = (e.breath + (d * TAU) / (L.periodIdle + (L.periodActive - L.periodIdle) * e.act)) % TAU;
    if (e.hold > 0 && (e.hold -= d) <= 0) this.release();
    if (this.dim) this.release();
    e.gx += (e.tx - e.gx) * ease(7); e.gy += (e.ty - e.gy) * ease(7);
    // scroll: the impulse sets where the figure is pushed (decaying), and an underdamped spring follows it; so the
    // push builds over a few frames instead of landing in one, and overshoots a little (the "jelly")
    e.swayT *= Math.exp(-3 * d);
    e.swayV += (55 * (e.swayT - e.sway) - 2 * 0.32 * Math.sqrt(55) * e.swayV) * d; e.sway += e.swayV * d;
    e.squashV += (-90 * e.squash - 2 * 0.3 * Math.sqrt(90) * e.squashV) * d; e.squash = clamp(e.squash + e.squashV * d, -1, 1);
    if (e.rippleA > 0) {   // a short attack (no step), then the decay
      e.rippleT += d;
      e.rippleA = Math.max(1e-6, (1 - Math.exp(-e.rippleT / 0.12)) * Math.exp(-e.rippleT / L.rippleS));
      if (e.rippleT > 0.5 && e.rippleA < 0.01) e.rippleA = 0;
    }
    e.glow += ((this.dim ? 0.3 : 0.5 + 0.28 * e.act + 0.32 * e.lvl + 0.25 * e.rippleA) - e.glow) * ease(3);
    if (![e.amp, e.bAmp, e.breath, e.gx, e.gy, e.sway, e.swayV, e.squash, e.glow, e.ax, e.ay, e.rb, ...e.ph].every(Number.isFinite)) {
      const want = e.want;
      this.env = ENV0();   // a bad input must never take the figure away: start the life layer over
      if (want?.every(Number.isFinite)) { this.env.want = want; [this.env.ax, this.env.ay, this.env.bcx, this.env.bcy, this.env.rb] = want; }
      this.excited = false;
      return;
    }
    this.excited = Math.abs(e.tx - e.gx) + Math.abs(e.ty - e.gy) > 0.004 || Math.abs(e.swayV) > 0.2 || Math.abs(e.sway - e.swayT) > 0.02
      || Math.abs(e.swayT) > 0.02 || Math.abs(e.squashV) > 0.05 || Math.abs(e.squash) > 0.005 || e.rippleA > 0 || e.lvl > 0.01;
  }

  // The drawn scroll offset: soft limits (no hard stop, so nothing sticks at a bound).
  _sway() {
    const v = this.env.sway, L = v < 0 ? LIFE.swayUp : LIFE.swayDown;
    return L * Math.tanh(v / L);
  }

  // The life field for one slot: physics position -> display. The body's outline flows (three slow harmonics, and
  // one travelling wave after a real stage change); body and eyes breathe about the ground contact, lean towards the
  // pointer and carry the scroll inertia; the eyes follow the pointer; props drift a little; the ground stays put.
  _display(s) {
    const out = s.disp, e = this.env;
    out.set(s.pos);
    s.lifeAt = this.stepNo;
    if (s.name === 'ground') return;
    const body = s.name === 'body', eye = s.name === 'eyeL' || s.name === 'eyeR';
    if (body && s.closed && (e.amp > 0 || e.rippleA > 0)) {
      const cx = e.bcx, cy = e.bcy, head = e.rippleT * (TAU / 1.4) - Math.PI / 2;
      for (let i = 0; i < out.length; i += 2) {
        const x = out[i] - cx, y = out[i + 1] - cy, th = Math.atan2(y, x);
        let dl = e.amp * (0.55 * Math.sin(2 * th + e.ph[0]) + 0.3 * Math.sin(3 * th + e.ph[1]) + 0.15 * Math.sin(5 * th + e.ph[2]));
        if (e.rippleA > 0) { const w = Math.atan2(Math.sin(th - head), Math.cos(th - head)); dl += LIFE.ripple * e.rippleA * Math.exp(-(w * w) / 0.32); }
        out[i] = cx + x * (1 + dl); out[i + 1] = cy + y * (1 + dl);
      }
    }
    const lean = e.gx * e.rb * LIFE.gazeLean, swell = LIFE.levelSwell * e.lvl * clamp(this.vigor, 0, 1.6);
    if (body || eye) {
      const b = e.bAmp * Math.sin(e.breath), q = e.squash * 0.035;
      if (swell > 0) scale(out, e.bcx, e.bcy, 1 + swell, 1 + swell);
      scale(out, e.ax, e.ay, 1 - 0.55 * b + 0.8 * q, 1 + b - q);
      translate(out, lean, this._sway());
      if (eye) translate(out, e.gx * e.rb * LIFE.gazeEye, 0.7 * e.gy * e.rb * LIFE.gazeEye);
    } else {
      const f = LIFE.float * (0.6 + 0.8 * e.act) * clamp(this.vigor, 0, 1.6) * e.calm * Math.sin(0.8 * e.breath + 1.3 * s.index);
      if (swell > 0) scale(out, s.cx, s.cy, 1 + 0.5 * swell, 1 + 0.5 * swell);
      translate(out, 0.4 * lean, f + 0.55 * this._sway());
    }
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
