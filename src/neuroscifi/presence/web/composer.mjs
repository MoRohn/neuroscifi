// Tier B: generative doodle composer (docs/presence/DOODLE_GRAMMAR.md). A semantic template turns a concept into
// a declarative scene spec (the same typed vocabulary the local model may propose in Tier C), and specToComposition
// turns any spec into slots from primitives. Specs are data only: no markup, no code, no path strings.
import * as g from './geometry.mjs';
import { presentBody } from './canonical.mjs';
import { SCENE_SCHEMA } from './contracts.mjs';

export const PRIMITIVES = Object.freeze(['curve', 'line', 'arc', 'loop', 'circle', 'dot', 'blob', 'ribbon', 'wave',
  'spiral', 'ring', 'rays', 'orbit', 'branch', 'node', 'polygon', 'container', 'connector']);
export const BODIES = Object.freeze(['present', 'thinking', 'working', 'responding', 'exploring', 'resting']);
export const EYES = Object.freeze(['open', 'closed', 'focused', 'none']);
export const MOTIONS = Object.freeze(['still', 'breathe', 'orbit', 'scan', 'pulse', 'drift', 'converge']);

const el = (primitive, x, y, size, o = {}) => ({ primitive, x, y, size, rotation: o.rotation ?? 0, tone: o.tone ?? 'ink',
  ...(o.count ? { count: o.count } : {}), ...(o.sides ? { sides: o.sides } : {}) });
const spec = (concept, body, eyes, motion, elements) => ({ schema: SCENE_SCHEMA, concept, body, eyes, motion, elements });

// Semantic templates: the concepts the spec names, matched by keywords (first match wins).
export const TEMPLATES = [
  { name: 'evaluating evidence', match: /evidence|verif|inspect|check|audit/, make: (c) => spec(c, 'present', 'focused', 'scan', [
    el('ring', 70, 40, 24), el('line', 84, 56, 12, { rotation: 45 }), el('rays', 70, 40, 34, { tone: 'mint', count: 3, rotation: -45 })]) },
  { name: 'resolving contradictions', match: /contradict|conflict|disagree|resolv/, make: (c) => spec(c, 'present', 'focused', 'pulse', [
    el('node', 58, 30, 12), el('node', 84, 30, 12), el('connector', 71, 52, 26, { rotation: 0 }), el('rays', 71, 74, 18, { tone: 'yellow', count: 3, rotation: 90 })]) },
  { name: 'comparing', match: /compar|weigh|balance|versus|alternativ.*compar/, make: (c) => spec(c, 'present', 'open', 'scan', [
    el('line', 71, 34, 36), el('polygon', 71, 46, 12, { sides: 3 }), el('container', 56, 58, 14), el('container', 86, 58, 14),
    el('dot', 71, 24, 6, { tone: 'blue' })]) },
  { name: 'exploring alternatives', match: /alternativ|option|branch|choice/, make: (c) => spec(c, 'exploring', 'open', 'drift', [
    el('branch', 70, 52, 34), el('node', 58, 26, 8, { tone: 'pink' }), el('node', 84, 26, 8, { tone: 'pink' })]) },
  { name: 'mapping knowledge', match: /knowledge|graph|map|relat/, make: (c) => spec(c, 'present', 'open', 'drift', [
    el('node', 58, 28, 10), el('node', 84, 40, 10), el('node', 64, 70, 10), el('connector', 71, 34, 22, { rotation: 25 }),
    el('connector', 74, 56, 22, { rotation: 115 }), el('dot', 86, 74, 6, { tone: 'blue' })]) },
  { name: 'connecting ideas', match: /connect|idea|link|associat/, make: (c) => spec(c, 'present', 'open', 'pulse', [
    el('node', 56, 40, 12), el('node', 86, 40, 12), el('connector', 71, 40, 24), el('rays', 71, 22, 20, { tone: 'yellow', count: 3, rotation: -90 })]) },
  { name: 'constructing a solution', match: /construct|build|solution|assembl/, make: (c) => spec(c, 'working', 'focused', 'converge', [
    el('polygon', 60, 72, 16, { sides: 4, rotation: 45 }), el('polygon', 82, 72, 16, { sides: 4, rotation: 45 }),
    el('polygon', 71, 52, 16, { sides: 4, rotation: 45 }), el('rays', 84, 24, 16, { tone: 'mint', count: 3, rotation: -45 })]) },
  { name: 'running an experiment', match: /experiment|trial|test|simulat/, make: (c) => spec(c, 'present', 'focused', 'pulse', [
    el('polygon', 72, 64, 30, { sides: 3 }), el('line', 72, 38, 10, { rotation: 90 }), el('dot', 64, 24, 7, { tone: 'purple' }),
    el('dot', 80, 16, 8, { tone: 'purple' })]) },
  { name: 'investigating anomalies', match: /anomal|outlier|spike|unusual|investigat/, make: (c) => spec(c, 'exploring', 'focused', 'scan', [
    el('wave', 70, 72, 36), el('ring', 74, 36, 22), el('line', 86, 52, 10, { rotation: 45 }), el('dot', 66, 58, 6, { tone: 'pink' })]) },
  { name: 'inspecting a device', match: /device|hardware|sensor|phone/, make: (c) => spec(c, 'present', 'focused', 'scan', [
    el('container', 70, 58, 26, { rotation: 90 }), el('ring', 84, 30, 18), el('line', 92, 44, 8, { rotation: 45 })]) },
  { name: 'retrieving memory', match: /memor|recall|retriev|remember/, make: (c) => spec(c, 'thinking', 'closed', 'orbit', [
    el('spiral', 72, 44, 30), el('node', 86, 72, 8), el('arc', 62, 74, 18, { tone: 'blue', rotation: 180 })]) },
  { name: 'learning from feedback', match: /feedback|learn|lesson|correct/, make: (c) => spec(c, 'present', 'open', 'drift', [
    el('loop', 70, 50, 36), el('rays', 86, 24, 16, { tone: 'mint', count: 3, rotation: -45 })]) },
  { name: 'monitoring an event', match: /monitor|watch|event|alert|track/, make: (c) => spec(c, 'present', 'open', 'scan', [
    el('ring', 72, 48, 30), el('ring', 72, 48, 16), el('rays', 75, 45, 36, { tone: 'blue', count: 3, rotation: -45 }), el('dot', 72, 48, 5)]) },
  { name: 'analysing audio', match: /audio|sound|speech|voice|signal/, make: (c) => spec(c, 'present', 'open', 'pulse', [
    el('wave', 70, 40, 34, { tone: 'mint' }), el('wave', 70, 60, 34, { tone: 'mint' }), el('arc', 70, 80, 20)]) },
  { name: 'coordinating devices', match: /coordinat|sync|devices|handoff/, make: (c) => spec(c, 'present', 'open', 'drift', [
    el('container', 58, 64, 16, { rotation: 90 }), el('container', 86, 64, 16, { rotation: 90 }), el('connector', 72, 40, 26),
    el('arc', 72, 26, 20, { tone: 'blue' })]) },
  { name: 'researching documentation', match: /document|manual|docs|reference|spec/, make: (c) => spec(c, 'present', 'focused', 'drift', [
    el('container', 70, 58, 30), el('line', 70, 52, 16), el('line', 70, 60, 16), el('line', 66, 68, 10), el('rays', 88, 26, 14, { tone: 'purple', count: 3, rotation: -45 })]) },
  { name: 'finishing a task', match: /finish|complet|done|wrap/, make: (c) => spec(c, 'present', 'open', 'breathe', [
    el('polygon', 72, 66, 30, { sides: 3 }), el('line', 72, 38, 22, { rotation: 90 }), el('container', 80, 26, 12, { tone: 'purple' })]) },
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
  return spec(concept, 'present', 'open', 'drift', els);
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
      const n = e.count ?? 3, out = [];
      for (let i = 0; i < n; i++) {
        const a = ((rot + (i - (n - 1) / 2) * 34) * Math.PI) / 180;
        out.push(g.line(x + Math.cos(a) * h * 0.5, y + Math.sin(a) * h * 0.5, x + Math.cos(a) * h, y + Math.sin(a) * h));
      }
      return out;
    }
    default: return [];
  }
}

const BODY_AT = (shape) => g.transform(shape, { scale: 0.62, ox: 50, oy: 50, dx: -20, dy: 6 });
function bodyShape(kind) {
  switch (kind) {
    case 'thinking': return g.circle(30, 54, 15);
    case 'working': return BODY_AT(g.smoothClosed([[58, 30], [72, 30], [83, 37], [86, 48], [80, 58], [66, 63], [44, 64], [24, 65], [19, 62], [26, 58], [36, 54], [42, 44], [48, 35]], 8));
    case 'responding': return g.ellipse(30, 54, 18, 13);
    case 'exploring': return g.flower(30, 56, 16, 5, 0.17);
    case 'resting': return g.smoothClosed([[10, 70], [14, 62], [22, 56], [30, 54], [38, 56], [46, 62], [50, 70], [30, 72]], 8);
    default: return BODY_AT(presentBody());
  }
}

const MOTION_ANIMS = {
  still: () => [],
  breathe: () => [],
  orbit: (e) => [{ k: 'swing', amp: 10, period: 6, ox: e.x, oy: e.y }],
  scan: (e) => [{ k: 'swing', amp: 7, period: 3.2, ox: e.x, oy: e.y }],
  pulse: () => [{ k: 'twinkle', amp: 0.16, period: 1.6 }],
  drift: (e, i) => [{ k: 'bob', amp: 1.6, period: 3 + i * 0.4, phase: i * 0.2 }],
  converge: (e) => [{ k: 'converge', dx: (e.x - 72) * 0.12, dy: (e.y - 52) * 0.12, period: 3.2 }],
};

export function specToComposition(sp, { tier = 'B' } = {}) {
  const slots = { body: S(bodyShape(sp.body)) };
  const ex = 28, ey = 48;
  if (sp.eyes === 'open') { slots.eyeL = S(g.line(ex, ey, ex, ey + 5)); slots.eyeR = S(g.line(ex + 6, ey, ex + 6, ey + 5)); }
  if (sp.eyes === 'focused') { slots.eyeL = S(g.line(ex - 1, ey + 2, ex + 3, ey + 2)); slots.eyeR = S(g.line(ex + 6, ey + 2, ex + 10, ey + 2)); }
  if (sp.eyes === 'closed') { slots.eyeL = S(g.arc(ex + 1, ey + 2, 3, 15, 165, 2.4), 'ink', { width: 'fine' }); slots.eyeR = S(g.arc(ex + 8, ey + 2, 3, 15, 165, 2.4), 'ink', { width: 'fine' }); }
  const marks = ['m0', 'm1', 'm2', 'm3', 'm4'], accents = ['a0', 'a1', 'a2'];
  sp.elements.forEach((e, i) => {
    const accent = !['ink', 'secondary'].includes(e.tone);
    const anims = (MOTION_ANIMS[sp.motion] ?? MOTION_ANIMS.still)(e, i);
    const fine = ['line', 'connector', 'rays', 'wave', 'spiral'].includes(e.primitive) && !accent;
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
