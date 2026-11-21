// Geometry engine (docs/presence/DOODLE_GRAMMAR.md): primitives as point lists in a 100 x 100 viewBox,
// shared point-count resampling (the basis of morphing), alignment of point sets, and smooth path output.
// A shape is { pts: [[x, y], ...], closed }. Resampled shapes are Float64Array [x0, y0, x1, y1, ...]; a closed
// shape repeats its first point at the end, so every slot renders as one open path and closure is pure geometry.

const TAU = Math.PI * 2;
const rad = (deg) => (deg * Math.PI) / 180;

// ---- primitives -----------------------------------------------------------------------------------------------
export const shape = (pts, closed = false) => ({ pts, closed });

export function polar(cx, cy, radius, { n = 120, rot = 0, sx = 1, sy = 1, start = -Math.PI / 2 } = {}) {
  const pts = [];
  for (let i = 0; i < n; i++) {
    const t = start + (i / n) * TAU;
    const r = radius(t - start);
    const x = Math.cos(t) * r * sx, y = Math.sin(t) * r * sy;
    pts.push([cx + x * Math.cos(rot) - y * Math.sin(rot), cy + x * Math.sin(rot) + y * Math.cos(rot)]);
  }
  return shape(pts, true);
}

export const ellipse = (cx, cy, rx, ry, rotDeg = 0) => polar(cx, cy, () => 1, { sx: rx, sy: ry, rot: rad(rotDeg) });
export const circle = (cx, cy, r) => ellipse(cx, cy, r, r);
export const dot = (cx, cy, r = 1.4) => polar(cx, cy, () => r, { n: 16 });

// Organic blob: radius with a few low harmonics [[k, amplitude, phase], ...].
export function blob(cx, cy, r, harmonics = [[2, 0.08, 0.4], [3, 0.1, 1.3], [5, 0.04, 0.2]], sx = 1, sy = 1) {
  return polar(cx, cy, (t) => r * (1 + harmonics.reduce((a, [k, amp, ph]) => a + amp * Math.sin(k * t + ph), 0)),
    { sx, sy });
}

// Scalloped cloud/flower outline (Curious): lobes with soft valleys.
export const flower = (cx, cy, r, lobes = 5, depth = 0.16) =>
  polar(cx, cy, (t) => r * (1 - depth + depth * Math.abs(Math.cos((lobes * t) / 2)) ** 0.6));

export function arc(cx, cy, r, a0, a1, ry = r, n = 40) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = rad(a0 + ((a1 - a0) * i) / n);
    pts.push([cx + Math.cos(t) * r, cy + Math.sin(t) * ry]);
  }
  return shape(pts, false);
}

export const line = (x0, y0, x1, y1) => shape([[x0, y0], [x1, y1]], false);
export const poly = (pts, closed = false) => shape(pts.map((p) => [p[0], p[1]]), closed);

export function bezier(p0, p1, p2, p3, n = 40) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    pts.push([u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
      u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]]);
  }
  return shape(pts, false);
}
export const quad = (p0, c, p1, n = 32) => bezier(p0, [p0[0] + (2 / 3) * (c[0] - p0[0]), p0[1] + (2 / 3) * (c[1] - p0[1])],
  [p1[0] + (2 / 3) * (c[0] - p1[0]), p1[1] + (2 / 3) * (c[1] - p1[1])], p1, n);

export function wave(x0, y, x1, amp, cycles, n = 48) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    pts.push([x0 + (x1 - x0) * t, y + Math.sin(t * cycles * TAU) * amp]);
  }
  return shape(pts, false);
}

export function spiral(cx, cy, r0, r1, turns, n = 60) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, a = t * turns * TAU;
    const r = r0 + (r1 - r0) * t;
    pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]);
  }
  return shape(pts, false);
}

export function roundRect(x, y, w, h, r) {
  const pts = [];
  const corner = (cx, cy, a0) => { for (let i = 0; i <= 4; i++) { const a = rad(a0 + i * 22.5); pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); } };
  corner(x + w - r, y + r, -90); corner(x + w - r, y + h - r, 0); corner(x + r, y + h - r, 90); corner(x + r, y + r, 180);
  return shape(pts, true);
}

export function regular(cx, cy, r, sides, rotDeg = 0) {
  const pts = [];
  for (let i = 0; i < sides; i++) {
    const a = rad(rotDeg - 90) + (i / sides) * TAU;
    pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]);
  }
  return shape(pts, true);
}

export function gear(cx, cy, r, teeth = 8, depth = 0.22) {
  return polar(cx, cy, (t) => {
    const s = Math.sin(teeth * t);
    return r * (1 - depth / 2 + (depth / 2) * Math.max(-1, Math.min(1, s * 2.2)));
  }, { n: teeth * 16 });
}

// Four-point sparkle (achievement/discovery accents).
export function sparkle(cx, cy, r) {
  const pts = [];
  for (let i = 0; i < 8; i++) {
    const a = rad(-90 + i * 45), rr = i % 2 ? r * 0.32 : r;
    pts.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]);
  }
  return shape(pts, true);
}

// Glyph strokes used only as accents (question mark, z, exclamation): drawn as curves, not text.
export function questionHook(x, y, s) {
  const top = arc(x, y - s * 0.5, s * 0.42, 195, 395, s * 0.42, 24).pts;
  const [ex, ey] = top[top.length - 1];
  return top.concat(quad([ex, ey], [x, y - s * 0.12], [x, y + s * 0.08], 10).pts.slice(1), [[x, y + s * 0.26]]);
}
export const zee = (x, y, s) => [[x - s / 2, y - s / 2], [x + s / 2, y - s / 2], [x - s / 2, y + s / 2], [x + s / 2, y + s / 2]];

// Smooth closed curve through key points (Catmull-Rom), for hand-authored organic bodies.
export function smoothClosed(keys, per = 10) {
  const pts = [], n = keys.length;
  for (let i = 0; i < n; i++) {
    const p0 = keys[(i - 1 + n) % n], p1 = keys[i], p2 = keys[(i + 1) % n], p3 = keys[(i + 2) % n];
    for (let j = 0; j < per; j++) {
      const t = j / per, t2 = t * t, t3 = t2 * t;
      pts.push([0, 1].map((k) => 0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)));
    }
  }
  return shape(pts, true);
}

// ---- transforms ------------------------------------------------------------------------------------------------
export function transform(s, { dx = 0, dy = 0, scale = 1, sx = scale, sy = scale, rot = 0, ox = 50, oy = 50 } = {}) {
  const c = Math.cos(rad(rot)), si = Math.sin(rad(rot));
  return shape(s.pts.map(([x, y]) => {
    const px = (x - ox) * sx, py = (y - oy) * sy;
    return [ox + px * c - py * si + dx, oy + px * si + py * c + dy];
  }), s.closed);
}

// ---- resampling and alignment ---------------------------------------------------------------------------------
// Resample to exactly n points. Few-vertex polylines keep their corners (points are spread along each segment in
// proportion to its length, vertices included); dense curves are resampled uniformly by arc length.
export function resample(s, n) {
  const src = s.closed ? [...s.pts, s.pts[0]] : s.pts.slice();
  const out = new Float64Array(n * 2);
  if (src.length === 1 || n < 2) { for (let i = 0; i < n; i++) { out[2 * i] = src[0][0]; out[2 * i + 1] = src[0][1]; } return out; }
  const seg = [];
  let total = 0;
  for (let i = 1; i < src.length; i++) { const d = Math.hypot(src[i][0] - src[i - 1][0], src[i][1] - src[i - 1][1]); seg.push(d); total += d; }
  if (total < 1e-9) { for (let i = 0; i < n; i++) { out[2 * i] = src[0][0]; out[2 * i + 1] = src[0][1]; } return out; }
  if (src.length <= n) {
    // corner-preserving: give each segment k_i >= 1 intervals (largest remainder over the rest)
    const intervals = n - 1, m = seg.length;
    const k = seg.map(() => 1);
    let left = intervals - m;
    const want = seg.map((d) => (d / total) * left);
    const base = want.map(Math.floor);
    base.forEach((b, i) => { k[i] += b; left -= b; });
    const order = want.map((w, i) => [w - Math.floor(w), i]).sort((a, b) => b[0] - a[0]);
    for (let j = 0; left > 0; j = (j + 1) % m, left--) k[order[j][1]] += 1;
    let idx = 0;
    for (let i = 0; i < m; i++) {
      const [ax, ay] = src[i], [bx, by] = src[i + 1];
      for (let j = 0; j < k[i]; j++) { const t = j / k[i]; out[2 * idx] = ax + (bx - ax) * t; out[2 * idx + 1] = ay + (by - ay) * t; idx++; }
    }
    const last = src[src.length - 1];
    out[2 * idx] = last[0]; out[2 * idx + 1] = last[1];
    return out;
  }
  let acc = 0, i = 0;
  for (let j = 0; j < n; j++) {
    const target = (total * j) / (n - 1);
    while (i < seg.length - 1 && acc + seg[i] < target) { acc += seg[i]; i++; }
    const t = seg[i] > 0 ? Math.min(1, Math.max(0, (target - acc) / seg[i])) : 0;
    out[2 * j] = src[i][0] + (src[i + 1][0] - src[i][0]) * t;
    out[2 * j + 1] = src[i][1] + (src[i + 1][1] - src[i][1]) * t;
  }
  return out;
}

// Signed area (shoelace); positive = clockwise on screen (y down).
export function area(p) {
  let a = 0;
  for (let i = 0; i < p.length / 2 - 1; i++) a += p[2 * i] * p[2 * i + 3] - p[2 * i + 2] * p[2 * i + 1];
  return a / 2;
}

function cost(a, b) { let c = 0; for (let i = 0; i < a.length; i++) { const d = a[i] - b[i]; c += d * d; } return c; }

function reversed(p) {
  const n = p.length / 2, out = new Float64Array(p.length);
  for (let i = 0; i < n; i++) { out[2 * i] = p[2 * (n - 1 - i)]; out[2 * i + 1] = p[2 * (n - 1 - i) + 1]; }
  return out;
}

// Re-order `next` so its points travel the shortest total distance from `current` (same point count): the best
// cyclic start (and direction) for closed shapes, the better direction for open ones. Prevents morphs that twist.
export function align(current, next, closed) {
  if (!current || current.length !== next.length) return next;
  if (!closed) { const r = reversed(next); return cost(current, r) < cost(current, next) ? r : next; }
  const n = next.length / 2 - 1;   // unique points (the last repeats the first)
  let best = next, bestCost = Infinity;
  for (const cand of [next, reversed(next)]) {
    for (let s = 0; s < n; s++) {
      let c = 0;
      for (let i = 0; i < n && c < bestCost; i++) {
        const j = (i + s) % n, dx = current[2 * i] - cand[2 * j], dy = current[2 * i + 1] - cand[2 * j + 1];
        c += dx * dx + dy * dy;
      }
      if (c < bestCost) { bestCost = c; best = [cand, s]; }
    }
  }
  const [cand, s] = best;
  const out = new Float64Array(next.length);
  for (let i = 0; i <= n; i++) { const j = (i + s) % n; out[2 * i] = cand[2 * j]; out[2 * i + 1] = cand[2 * j + 1]; }
  return out;
}

export function bbox(p) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < p.length; i += 2) {
    if (p[i] < x0) x0 = p[i]; if (p[i] > x1) x1 = p[i];
    if (p[i + 1] < y0) y0 = p[i + 1]; if (p[i + 1] > y1) y1 = p[i + 1];
  }
  return [x0, y0, x1, y1];
}

export function centroid(p) { const [x0, y0, x1, y1] = bbox(p); return [(x0 + x1) / 2, (y0 + y1) / 2]; }

export function collapsed(n, x, y) {
  const out = new Float64Array(n * 2);
  for (let i = 0; i < n; i++) { out[2 * i] = x; out[2 * i + 1] = y; }
  return out;
}

// ---- path output ----------------------------------------------------------------------------------------------
// Midpoint-quadratic smoothing: the curve passes through segment midpoints with the points as controls. It never
// overshoots, rounds polyline corners slightly (the hand-drawn look) and closes smoothly when `closed`.
const f = (v) => (Math.round(v * 100) / 100).toString();
export function toPath(p, closed) {
  const n = p.length / 2;
  if (n < 2) return '';
  if (n === 2) return `M${f(p[0])} ${f(p[1])}L${f(p[2])} ${f(p[3])}`;
  const mx = (i, j) => f((p[2 * i] + p[2 * j]) / 2), my = (i, j) => f((p[2 * i + 1] + p[2 * j + 1]) / 2);
  if (closed) {
    const m = n - 1;   // unique points
    let d = `M${mx(0, 1)} ${my(0, 1)}`;
    for (let i = 1; i <= m; i++) { const a = i % m, b = (i + 1) % m; d += `Q${f(p[2 * a])} ${f(p[2 * a + 1])} ${mx(a, b)} ${my(a, b)}`; }
    return d;
  }
  let d = `M${f(p[0])} ${f(p[1])}`;
  for (let i = 1; i < n - 1; i++) d += `Q${f(p[2 * i])} ${f(p[2 * i + 1])} ${mx(i, i + 1)} ${my(i, i + 1)}`;
  return d + `L${f(p[2 * n - 2])} ${f(p[2 * n - 1])}`;
}
