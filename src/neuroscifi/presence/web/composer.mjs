// Tier B: generative doodle composer (docs/presence/DOODLE_GRAMMAR.md). A semantic template turns a concept into
// a declarative scene spec (the same typed vocabulary the local model may propose in Tier C), and specToComposition
// turns any spec into slots from primitives. Specs are data only: no markup, no code, no path strings.
import * as g from './geometry.mjs';
import { ENTITY, ENTITY_EYES, eyeMarks, eyeKindFor } from './canonical.mjs';
import { SCENE_SCHEMA } from './contracts.mjs';

export const PRIMITIVES = Object.freeze(['curve', 'line', 'arc', 'loop', 'circle', 'dot', 'blob', 'ribbon', 'wave',
  'spiral', 'ring', 'rays', 'orbit', 'branch', 'node', 'polygon', 'container', 'connector']);
export const BODIES = Object.freeze(['present', 'thinking', 'working', 'responding', 'exploring', 'resting']);
export const EYES = Object.freeze(['open', 'closed', 'focused', 'none']);
export const MOTIONS = Object.freeze(['still', 'breathe', 'orbit', 'scan', 'pulse', 'drift', 'converge']);

const el = (primitive, x, y, size, o = {}) => ({ primitive, x, y, size, rotation: o.rotation ?? 0, tone: o.tone ?? 'ink',
  ...(o.count ? { count: o.count } : {}), ...(o.sides ? { sides: o.sides } : {}) });
// A straight connection between two points, trimmed by `trim` at both ends (so it meets node outlines cleanly).
const link = ([ax, ay], [bx, by], trim = 0, o = {}) => {
  const len = Math.hypot(bx - ax, by - ay);
  return el('line', round((ax + bx) / 2), round((ay + by) / 2), round(len - 2 * trim), { ...o, rotation: round((Math.atan2(by - ay, bx - ax) * 180) / Math.PI) });
};
const round = (v) => Math.round(v * 10) / 10;
// Template specs carry the eye kind the energy rule draws (open: vertical strokes, closed: arcs, focused: chevrons).
const EYE_ENUM = { open: 'open', closed: 'closed', working: 'focused' };
const spec = (concept, body, motion, elements) => ({ schema: SCENE_SCHEMA, concept, body, eyes: EYE_ENUM[eyeKindFor(body, motion)], motion, elements: deface(elements) });

// Semantic templates (first match wins). Each draws ONE recognisable object beside the entity (the prop area is
// x 40-96 of the 100-unit canvas; the entity sits on the ground at the left). Element order is priority: the first
// ink elements and the first accents are what the compact size keeps (contracts.SIZES), and the first element is the
// actor that scan motion moves. Layouts never place two small marks side by side above a curve (no faces, see
// facePattern) and stay within the reference complexity ceiling (<= 8 strokes, <= 2 accent colours).
export const TEMPLATES = [
  // magnifier over a page
  { name: 'evaluating evidence', match: /evidence|verif|inspect|check|audit/, make: (c) => spec(c, 'present', 'scan', [
    el('ring', 77, 40, 20), el('line', 87.5, 54, 9, { rotation: 45 }), el('container', 61, 68, 26, { rotation: 90 }),
    el('line', 61, 63, 10), el('line', 59, 71, 6), el('rays', 87, 21, 20, { tone: 'mint', rotation: -45 })]) },
  // two overlapping views converge on common ground (a Venn diagram), with a spark of agreement
  { name: 'resolving contradictions', match: /contradict|conflict|disagree|resolv/, make: (c) => spec(c, 'present', 'converge', [
    el('ring', 61, 62, 26), el('ring', 81, 62, 26), el('dot', 71, 62, 8, { tone: 'mint' }),
    el('rays', 71, 30, 22, { tone: 'yellow', rotation: -90 })]) },
  // a balance: beam, post, two pans, stand
  { name: 'comparing', match: /compar|weigh|balance|versus/, make: (c) => spec(c, 'present', 'scan', [
    el('line', 70, 30, 36), el('line', 70, 48, 36, { rotation: 90 }), el('polygon', 54, 40, 16, { sides: 3 }),
    el('polygon', 86, 40, 16, { sides: 3 }), el('polygon', 70, 74, 18, { sides: 3 }), el('dot', 70, 30, 8, { tone: 'blue' })]) },
  // a fork in the path, two destinations
  { name: 'exploring alternatives', match: /alternativ|option|branch|choice/, make: (c) => spec(c, 'exploring', 'drift', [
    el('branch', 63, 60, 32, { rotation: 90 }), el('node', 83, 44, 11, { tone: 'pink' }), el('node', 83, 76, 11, { tone: 'pink' })]) },
  // a small graph: three nodes, three links
  { name: 'mapping knowledge', match: /knowledge|graph|map|relat/, make: (c) => spec(c, 'present', 'drift', [
    link([54, 34], [86, 46], 5), link([86, 46], [66, 76], 5), link([66, 76], [54, 34], 5), el('node', 86, 46, 11),
    el('node', 66, 76, 11), el('node', 54, 34, 11, { tone: 'blue' })]) },
  // two ideas joined by a line; the second one lights up
  { name: 'connecting ideas', match: /connect|idea|link|associat/, make: (c) => spec(c, 'present', 'pulse', [
    el('ring', 57, 70, 15), el('ring', 80, 46, 15), link([57, 70], [80, 46], 10), el('rays', 87, 27, 20, { tone: 'yellow', rotation: -45 })]) },
  // bricks stacked into a small wall
  { name: 'constructing a solution', match: /construct|build|solution|assembl/, make: (c) => spec(c, 'working', 'converge', [
    el('container', 56, 80, 17), el('container', 75, 80, 17), el('container', 65.5, 66, 17), el('rays', 86, 46, 20, { tone: 'mint', rotation: -45 })]) },
  // a beaker with liquid and rising bubbles
  { name: 'running an experiment', match: /experiment|trial|test|simulat/, make: (c) => spec(c, 'present', 'pulse', [
    el('container', 68, 69, 26, { rotation: 90 }), el('wave', 68, 72, 15, { tone: 'purple' }), el('dot', 63, 46, 7, { tone: 'purple' }),
    el('dot', 74, 36, 9, { tone: 'purple' })]) },
  // a signal with one odd point, and a magnifier looking at it
  { name: 'investigating anomalies', match: /anomal|outlier|spike|unusual|investigat/, make: (c) => spec(c, 'exploring', 'scan', [
    el('ring', 80, 40, 18), el('line', 89, 52, 9, { rotation: 45 }), el('wave', 63, 76, 30), el('dot', 58, 62, 7, { tone: 'pink' })]) },
  // a phone under a magnifier
  { name: 'inspecting a device', match: /device|hardware|sensor|phone/, make: (c) => spec(c, 'present', 'scan', [
    el('ring', 83, 38, 16), el('line', 91, 48, 8, { rotation: 45 }), el('container', 63, 67, 28, { rotation: 90 }),
    el('line', 63, 59, 6), el('dot', 63, 76, 6, { tone: 'mint' })]) },
  // a thought bubble with a retrieved card
  { name: 'retrieving memory', match: /memor|recall|retriev|remember/, make: (c) => spec(c, 'thinking', 'orbit', [
    el('blob', 73, 30, 30), el('node', 45, 55, 6), el('node', 53, 46, 9), el('container', 73, 30, 12, { tone: 'blue' })]) },
  // a learning curve that rises and levels off, on two axes
  { name: 'learning from feedback', match: /feedback|learn|lesson|correct/, make: (c) => spec(c, 'present', 'drift', [
    el('connector', 70, 60, 34, { rotation: -30 }), el('line', 52, 61, 30, { rotation: 90 }), el('line', 69, 76, 34),
    el('dot', 85, 52, 8, { tone: 'mint' })]) },
  // a monitor with a live signal and an alert fan
  { name: 'monitoring an event', match: /monitor|watch|event|alert|track/, make: (c) => spec(c, 'present', 'pulse', [
    el('container', 67, 58, 32), el('line', 67, 76, 10, { rotation: 90 }), el('line', 67, 82, 16),
    el('wave', 67, 58, 20, { tone: 'mint' }), el('rays', 87, 31, 20, { tone: 'blue', rotation: -45 })]) },
  // a sound wave above its spectrum bars
  { name: 'analysing audio', match: /audio|sound|speech|voice|signal/, make: (c) => spec(c, 'present', 'pulse', [
    el('wave', 70, 36, 34, { tone: 'mint' }), el('line', 56, 74, 12, { rotation: 90 }), el('line', 66, 70, 20, { rotation: 90 }),
    el('line', 76, 72, 16, { rotation: 90 }), el('line', 86, 75, 10, { rotation: 90 })]) },
  // a phone and a screen with a shared wireless link
  { name: 'coordinating devices', match: /coordinat|sync|devices|handoff/, make: (c) => spec(c, 'present', 'drift', [
    el('container', 56, 72, 20, { rotation: 90 }), el('container', 84, 70, 22), el('arc', 70, 58, 26, { tone: 'blue' }),
    el('arc', 70, 58, 13, { tone: 'blue' })]) },
  // a page of text with a sparkle
  { name: 'researching documentation', match: /document|manual|docs|reference|spec/, make: (c) => spec(c, 'present', 'drift', [
    el('container', 66, 63, 30, { rotation: 90 }), el('line', 66, 56, 12), el('line', 66, 63, 12), el('line', 64, 70, 8),
    el('rays', 88, 30, 20, { tone: 'purple', rotation: -45 })]) },
  // a ticked box
  { name: 'finishing a task', match: /finish|complet|done|wrap/, make: (c) => spec(c, 'present', 'breathe', [
    el('container', 66, 66, 28), link([58, 66], [64, 72], 0, { tone: 'mint' }), link([64, 72], [76, 58], 0, { tone: 'mint' }),
    el('rays', 87, 40, 20, { tone: 'yellow', rotation: -45 })]) },
];

// A deterministic seeded layout for concepts no template knows: a small constellation, still validated.
function seeded(concept) {
  let h = 2166136261;
  for (const ch of concept) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
  const rnd = () => { h ^= h << 13; h >>>= 0; h ^= h >> 17; h ^= h << 5; h >>>= 0; return h / 4294967296; };
  const kinds = ['orbit', 'node', 'spiral', 'wave', 'polygon', 'arc'];
  const n = 2 + Math.floor(rnd() * 2);
  const els = [];
  const spots = [[66, 30], [84, 46], [64, 66], [86, 74]];
  for (let i = 0; i < n; i++) {
    const [x, y] = spots[(i + Math.floor(rnd() * 4)) % 4];
    if (els.some((e) => e.x === x && e.y === y)) continue;
    const kind = kinds[Math.floor(rnd() * kinds.length)];
    els.push(el(kind, x, y, 14 + Math.floor(rnd() * 6), { rotation: Math.round(rnd() * 90 - 45), sides: kind === 'polygon' ? 3 + Math.floor(rnd() * 4) : undefined }));
  }
  els.push(el('dot', 90, 14, 6, { tone: ['purple', 'blue', 'mint', 'pink'][Math.floor(rnd() * 4)] }));
  return spec(concept, 'present', 'drift', els);
}

// ---- the no-face rule (docs/presence/DOODLE_GRAMMAR.md §Validator) ----------------------------------------------
// The entity's own eye marks are the only eyes in a scene. Props must never form a conventional cartoon face:
// two small marks (dots, small circles, short vertical strokes) side by side above a curve or line ("mouth"), or
// two such marks inside a ring/outline ("head"). This works on spec elements; validator.validateScene checks the
// same pattern on the finished geometry.
const SMALL = ['dot', 'node', 'circle', 'ring', 'blob', 'polygon', 'container'];
const MOUTH = ['arc', 'curve', 'connector', 'wave', 'ribbon', 'loop', 'line', 'spiral'];
const OUTLINE = ['ring', 'circle', 'blob', 'container', 'polygon', 'orbit'];
const flat = (rot) => { const r = ((Math.abs(rot) % 180) + 180) % 180; return r <= 35 || r >= 145; };
export function eyeLike(e) {
  if (e.primitive === 'dot' || e.primitive === 'node') return e.size <= 14;
  if (SMALL.includes(e.primitive)) return e.size <= 12;
  if (e.primitive === 'line') return e.size <= 10 && !flat(e.rotation);
  return false;
}
const mouthLike = (e) => MOUTH.includes(e.primitive) && e.size >= 8 && flat(e.rotation);
// Returns null or { eyes: [i, j], mouth: k } / { eyes: [i, j], outline: k } (indices into elements).
export function facePattern(els) {
  for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
    const a = els[i], b = els[j];
    if (!eyeLike(a) || !eyeLike(b)) continue;
    const dx = Math.abs(a.x - b.x);
    if (Math.abs(a.y - b.y) > 5 || dx < 4 || dx > 34) continue;
    const mx = (a.x + b.x) / 2, ey = Math.max(a.y, b.y);
    for (let k = 0; k < els.length; k++) {
      if (k === i || k === j) continue;
      const m = els[k];
      if (mouthLike(m) && m.y - ey >= 3 && m.y - ey <= dx + 14 && Math.abs(m.x - mx) <= dx / 2 + 4) return { eyes: [i, j], mouth: k };
      const h = m.size / 2;
      if (OUTLINE.includes(m.primitive) && m.size >= 14 && Math.min(a.x, b.x) > m.x - h && Math.max(a.x, b.x) < m.x + h
        && Math.min(a.y, b.y) > m.y - h && ey <= m.y + 2) return { eyes: [i, j], outline: k };
    }
  }
  return null;
}

// Composer constraint: re-lay a face-like arrangement out so the parts read as separate objects. The pair is split
// vertically (one mark rises) and a "mouth" moves to the side; at most three passes, then the validator decides.
export function deface(els) {
  const out = els.map((e) => ({ ...e }));
  for (let pass = 0; pass < 3; pass++) {
    const f = facePattern(out);
    if (!f) break;
    const [i, j] = f.eyes, b = out[j];
    b.y = b.y - 12 >= 8 + b.size / 2 ? b.y - 12 : Math.min(92 - b.size / 2, b.y + 12);
    if (f.mouth !== undefined) {
      const m = out[f.mouth], mx = (out[i].x + b.x) / 2;
      const shift = Math.abs(out[i].x - b.x) / 2 + m.size / 2 + 4;
      m.x = Math.max(36 + m.size / 2, Math.min(98 - m.size / 2, m.x >= mx ? m.x + shift : m.x - shift));
    }
  }
  return out;
}

export function templateFor(concept) {
  const text = String(concept).toLowerCase();
  return TEMPLATES.find((t) => t.name === text) ?? TEMPLATES.find((t) => t.match.test(text)) ?? null;
}

// Tier B entry point: concept -> spec (template, else seeded constellation).
export function composeSpec(concept) {
  const t = templateFor(concept);
  return t ? { spec: t.make(concept), template: t.name } : { spec: seeded(concept), template: null };
}

// ---- spec -> composition ---------------------------------------------------------------------------------------
const S = (shape, tone = 'ink', o = {}) => ({ shape, tone, width: o.width ?? 'bold', fill: !!o.fill, dash: !!o.dash, anims: o.anims ?? [] });

function elementShapes(e) {
  const { x, y, size: s } = e, h = s / 2, rot = e.rotation || 0;
  const r = (shape) => g.transform(shape, { rot, ox: x, oy: y });
  switch (e.primitive) {
    case 'curve': return [r(g.bezier([x - h, y + h / 3], [x - h / 3, y - h], [x + h / 3, y + h], [x + h, y - h / 3]))];
    case 'line': return [r(g.line(x - h, y, x + h, y))];
    case 'arc': return [r(g.arc(x, y, h, 200, 340))];
    case 'loop': {
      const pts = [];
      for (let i = 0; i <= 60; i++) { const t = (i / 60) * Math.PI * 2 * 1.5; pts.push([x - h + (i / 60) * s + Math.sin(t) * h * 0.35, y - Math.cos(t) * h * 0.4 + h * 0.1]); }
      return [r(g.shape(pts))];
    }
    case 'circle': case 'ring': return [g.circle(x, y, h)];
    case 'dot': return [g.dot(x, y, Math.max(1.4, s / 5))];
    case 'node': return [g.circle(x, y, Math.max(2, s / 3))];
    case 'blob': return [g.blob(x, y, h)];
    case 'ribbon': return [r(g.wave(x - h, y, x + h, s / 7, 1.5))];
    case 'wave': return [r(g.wave(x - h, y, x + h, s / 6, 2))];
    case 'spiral': return [r(g.spiral(x, y, 1, h, 2))];
    case 'orbit': return [g.ellipse(x, y, h, h / 3.2, rot)];
    case 'branch': return [r(g.poly([[x, y + h], [x, y], [x - h * 0.7, y - h * 0.8], [x, y], [x + h * 0.7, y - h * 0.8]]))];
    case 'polygon': return [g.regular(x, y, h, e.sides ?? 5, rot)];
    case 'container': return [r(g.roundRect(x - h, y - h * 0.38, s, s * 0.76, Math.min(4, s / 6)))];
    case 'connector': return [r(g.quad([x - h, y], [x, y - h * 0.5], [x + h, y]))];
    case 'rays': {
      // the reference accent fan: short strokes radiating from a source behind the element, 30 degrees apart,
      // pointing along `rotation` (-90 = up, -45 = up-right)
      const n = e.count ?? 3, out = [], a0 = (rot * Math.PI) / 180;
      const sx = x - Math.cos(a0) * s * 0.62, sy = y - Math.sin(a0) * s * 0.62;
      for (let i = 0; i < n; i++) {
        const a = a0 + ((i - (n - 1) / 2) * 30 * Math.PI) / 180;
        out.push(g.line(sx + Math.cos(a) * s * 0.42, sy + Math.sin(a) * s * 0.42, sx + Math.cos(a) * s * 0.8, sy + Math.sin(a) * s * 0.8));
      }
      return out;
    }
    default: return [];
  }
}

// The Tier B entity is the Tier A entity body itself, scaled to about 36 units and seated on the ground at the left,
// so procedural scenes and canonical states share one creature. The eye pair moves with the body.
const BODY_FIT = { present: 0.54, thinking: 0.66, working: 0.55, responding: 0.58, exploring: 0.68, resting: 0.62 };
function seatedBody(kind) {
  const k = BODY_FIT[kind] ?? BODY_FIT.present;
  const src = (ENTITY[kind] ?? ENTITY.present)().slots.body.shape;
  const [x0, , x1, y1] = g.bbox(src.pts.flat());
  const place = (px, py) => [25 + (px - (x0 + x1) / 2) * k, 88 - (y1 - py) * k];
  const [ex, ey] = ENTITY_EYES[kind] ?? ENTITY_EYES.present;
  return { shape: g.shape(src.pts.map(([px, py]) => place(px, py)), src.closed), eye: place(ex, ey), k };
}

// Motion kinds: drift bobs every element; scan swings the actor (the first two elements, e.g. a magnifier and its
// handle) about the first; orbit swings the accents about the first element; pulse twinkles the accents; converge
// gathers the fragments.
function animsFor(sp, e, i) {
  const accent = !['ink', 'secondary'].includes(e.tone), first = sp.elements[0];
  switch (sp.motion) {
    case 'drift': return [{ k: 'bob', amp: 1.6, period: 3 + i * 0.4, phase: i * 0.2 }];
    case 'scan': return i < 2 ? [{ k: 'swing', amp: 7, period: 3.2, ox: first.x, oy: first.y }] : [];
    case 'orbit': return accent ? [{ k: 'swing', amp: 10, period: 6, ox: first.x, oy: first.y }] : [];
    case 'pulse': return accent ? [{ k: 'twinkle', amp: 0.16, period: 1.6, phase: i * 0.15 }] : [];
    case 'converge': return [{ k: 'converge', dx: (e.x - 72) * 0.12, dy: (e.y - 52) * 0.12, period: 3.2 }];
    default: return [];
  }
}

export function specToComposition(sp, { tier = 'B' } = {}) {
  const body = seatedBody(sp.body);
  const slots = { body: S(body.shape) };
  // ONE eye rule: the kind follows activity energy (eyeKindFor); a spec may only switch the marks off.
  if (sp.eyes !== 'none') Object.assign(slots, eyeMarks(eyeKindFor(sp.body, sp.motion), body.eye[0], body.eye[1], body.k * 1.2));
  const marks = ['m0', 'm1', 'm2', 'm3', 'm4'], accents = ['a0', 'a1', 'a2'];
  sp.elements.forEach((e, i) => {
    const accent = !['ink', 'secondary'].includes(e.tone);
    const anims = animsFor(sp, e, i);
    const fine = ['line', 'connector', 'wave', 'spiral'].includes(e.primitive) && !accent;
    for (const shape of elementShapes(e)) {
      const pool = accent ? accents : marks;
      const slot = pool.shift() ?? (accent ? marks.shift() : accents.shift());
      if (!slot) return;
      slots[slot] = S(shape, e.tone, { width: fine ? 'fine' : 'bold', fill: e.primitive === 'dot', anims });
    }
  });
  slots.ground = S(g.line(8, 90, 92, 90));
  return { id: sp.concept, tier, breathe: sp.motion === 'breathe' || sp.motion === 'still', slots };
}
