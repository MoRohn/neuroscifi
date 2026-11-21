// Tier A: curated canonical compositions (docs/presence/VISUAL_DESIGN_SYSTEM.md). The six entity states and the
// twelve doodles follow docs/assets/presence/reference-*.png (style and complexity ceiling), plus five system
// compositions in the same grammar (asking, error, waiting, recovering, disconnected).
// A composition is { id, tier, breathe, slots: { slotName: { shape, tone, width, fill, dash, anims } } }.
import * as g from './geometry.mjs';

const S = (shape, tone = 'ink', o = {}) => ({ shape, tone, width: o.width ?? 'bold', fill: !!o.fill, dash: !!o.dash,
  anims: o.anims ?? [] });
const fan = (x, y, tone, anims = [{ k: 'twinkle', amp: 0.12, period: 2.6 }]) => ({
  a0: S(g.line(x - 8, y + 4, x - 4, y - 4), tone, { anims: anims.map((a) => ({ ...a, phase: 0 })) }),
  a1: S(g.line(x - 2, y + 8, x + 6, y + 3), tone, { anims: anims.map((a) => ({ ...a, phase: 0.33 })) }),
  a2: S(g.line(x + 1, y + 15, x + 9, y + 14), tone, { anims: anims.map((a) => ({ ...a, phase: 0.66 })) }),
});
const ground = (y = 88, x0 = 10, x1 = 90) => S(g.line(x0, y, x1, y));
// The entity's internal marks follow ONE rule (docs/presence/VISUAL_DESIGN_SYSTEM.md §Eye marks): two short vertical
// strokes (attentive), two downward arcs (calm or closed: thinking, resting, dormant) or the working chevrons `> <`.
// Which one is drawn depends on activity energy (eyeKindFor), never on the concept. (cx, cy) is the centre of the pair;
// s scales it with the body, with floors that keep the two marks apart at small sizes.
export const EYE_KINDS = Object.freeze(['open', 'closed', 'working']);
export function eyeMarks(kind, cx, cy, s = 1, tone = 'ink') {
  if (kind === 'open') {
    const gap = Math.max(6.2, 7 * s), half = Math.max(2.4, 3.5 * s);
    return { eyeL: S(g.line(cx - gap / 2, cy - half, cx - gap / 2, cy + half), tone), eyeR: S(g.line(cx + gap / 2, cy - half, cx + gap / 2, cy + half), tone) };
  }
  if (kind === 'closed') {
    const r = Math.max(2.8, 4 * s), gap = Math.max(10, 13 * s);
    return { eyeL: S(g.arc(cx - gap / 2, cy, r, 15, 165, r * 0.8), tone, { width: 'fine' }),
      eyeR: S(g.arc(cx + gap / 2, cy, r, 15, 165, r * 0.8), tone, { width: 'fine' }) };
  }
  if (kind === 'working') {
    const w = Math.max(3.6, 5 * s), h = Math.max(3.4, 4 * s), tip = Math.max(2.6, 3 * s);
    return { eyeL: S(g.poly([[cx - tip - w, cy - h], [cx - tip, cy], [cx - tip - w, cy + h]]), tone),
      eyeR: S(g.poly([[cx + tip + w, cy - h], [cx + tip, cy], [cx + tip + w, cy + h]]), tone) };
  }
  return {};
}
// Activity energy -> eye kind: working energy (the working body, converging work) shows chevrons; calm or inward
// energy (thinking, resting, orbiting reorganisation, stillness) shows closed arcs; everything else is attentive.
export function eyeKindFor(body, motion) {
  if (body === 'working' || motion === 'converge') return 'working';
  if (body === 'thinking' || body === 'resting' || motion === 'orbit' || motion === 'still') return 'closed';
  return 'open';
}
const eyesOpen = (x, y, gap = 7, len = 7) => eyeMarks('open', x + gap / 2, y + len / 2, len / 7);
const eyesClosed = (x, y, gap = 13, r = 4, tone = 'ink') => eyeMarks('closed', x + gap / 2, y, r / 4, tone);

// The idle body: four soft, slightly asymmetric lobes (top, right, bottom, left) like the reference.
export const presentBody = (o = {}) => g.transform(g.polar(49, 50, (u) => 28 * (1 + 0.2 * Math.cos(4 * u - 0.5)
  + 0.07 * Math.cos(2 * u + 1.1) + 0.04 * Math.sin(3 * u + 0.6)), { sx: 1.06 }), o);
const smallBody = (cx, cy, r) => g.blob(cx, cy, r, [[2, 0.06, 0.9], [3, 0.07, 2.1], [4, 0.03, 0.4]]);

function bubble(cx, cy, rx, ry, tip) {
  const pts = [];
  for (let a = 74; a <= 404; a += 6) { const t = (a * Math.PI) / 180; pts.push([cx + Math.cos(t) * rx, cy + Math.sin(t) * ry]); }
  pts.push(tip);
  return g.shape(pts, true);
}
function question(x, y, s, tone, o = {}) {
  return { hook: S(g.shape(g.questionHook(x, y, s), false), tone, o),
    dot: S(g.dot(x, y + s * 0.62, 1.7), tone, { fill: true, anims: o.anims }) };
}
function cube(cx, cy, r) {
  const v = (deg) => [cx + Math.cos((deg * Math.PI) / 180) * r, cy + Math.sin((deg * Math.PI) / 180) * r];
  return [g.regular(cx, cy, r, 6, 0), g.poly([v(210), [cx, cy], v(-30), [cx, cy], v(90)])];
}

// Centre of the eye pair of each entity body (Tier B seats the same bodies and moves the pair with them).
export const ENTITY_EYES = Object.freeze({ present: [58.5, 45.5], thinking: [50, 47], working: [69, 46], responding: [47, 47],
  exploring: [42.5, 54.5], resting: [50.5, 62] });

export const ENTITY = {
  present: () => ({ breathe: true, slots: { body: S(presentBody()), ...eyesOpen(55, 42), ...fan(76, 14, 'purple') } }),
  thinking: () => ({ slots: { body: S(g.circle(50, 50, 24)),
    m0: S(g.ellipse(50, 52, 39, 12, -18), 'ink', { anims: [{ k: 'swing', amp: 9, period: 7.5, ox: 50, oy: 52 }] }),
    ...eyesClosed(43.5, 47), ...fan(77, 9, 'blue') } }),
  working: () => ({ slots: {
    body: S(g.smoothClosed([[58, 30], [72, 30], [83, 37], [86, 48], [80, 58], [66, 63], [44, 64], [24, 65], [19, 62],
      [26, 58], [36, 54], [42, 44], [48, 35]], 8), 'ink', { anims: [{ k: 'bob', amp: 1.2, period: 1.6 }] }),
    ...eyeMarks('working', 69, 46),
    a0: S(g.line(8, 40, 30, 40), 'mint', { anims: [{ k: 'shift', dx: -5, period: 0.9 }] }),
    a1: S(g.line(4, 49, 22, 49), 'mint', { anims: [{ k: 'shift', dx: -6, period: 0.9, phase: 0.3 }] }),
    a2: S(g.line(12, 57, 20, 57), 'mint', { anims: [{ k: 'shift', dx: -4, period: 0.9, phase: 0.6 }] }) } }),
  responding: () => ({ slots: { body: S(bubble(47, 48, 30, 22, [76, 74])),
    m0: S(g.dot(37, 48, 2.3), 'ink', { fill: true, anims: [{ k: 'dots', amp: 4, phase: 0, drive: 'signal' }] }),
    m1: S(g.dot(47, 48, 2.3), 'ink', { fill: true, anims: [{ k: 'dots', amp: 4, phase: 0.33, drive: 'signal' }] }),
    m2: S(g.dot(57, 48, 2.3), 'ink', { fill: true, anims: [{ k: 'dots', amp: 4, phase: 0.66, drive: 'signal' }] }),
    ...fan(79, 12, 'yellow') } }),
  exploring: () => {
    const q = question(78, 26, 10, 'pink', { anims: [{ k: 'twinkle', amp: 0.1, period: 2.2 }] });
    return { slots: { body: S(g.flower(44, 56, 25, 5, 0.17), 'ink', { anims: [{ k: 'swing', amp: 4, period: 3.4, ox: 44, oy: 70 }] }),
      ...eyesOpen(39, 51), a0: q.hook, a1: q.dot } };
  },
  resting: () => ({ slots: {
    body: S(g.smoothClosed([[22, 71], [26, 62], [34, 53], [44, 48], [56, 48], [67, 53], [75, 62], [80, 71], [70, 73],
      [51, 73.5], [31, 73]], 8)),
    ...eyesClosed(44, 62, 13, 3.8),
    a0: S(g.poly(g.zee(68, 38, 7)), 'purple', { anims: [{ k: 'bob', amp: 2.2, period: 4.2, decay: 12 }] }),
    a1: S(g.poly(g.zee(80, 25, 10)), 'purple', { anims: [{ k: 'bob', amp: 2.2, period: 4.2, phase: 0.5, decay: 12 }] }) } }),
};

export const DOODLES = {
  reading: () => ({ slots: { body: S(smallBody(31, 50, 19)), ...eyesOpen(31, 42, 6, 5), ground: ground(),
    m0: S(g.poly([[47, 58], [62, 63], [62, 85], [47, 80]], true)),
    m1: S(g.poly([[62, 63], [79, 56], [79, 79], [62, 85]], true), 'ink', { anims: [{ k: 'swing', amp: 3, period: 2.6, ox: 62, oy: 74 }] }),
    ...fan(74, 18, 'purple') } }),
  computing: () => ({ slots: { body: S(smallBody(31, 50, 19)), ...eyesOpen(31, 42, 6, 5), ground: ground(),
    m0: S(g.poly([[48, 56], [82, 56], [77, 82], [44, 82]], true)), m1: S(g.line(38, 86, 86, 86)),
    m2: S(g.dot(63, 69, 2), 'ink', { fill: true, anims: [{ k: 'twinkle', amp: 0.35, period: 1.1 }] }),
    ...fan(75, 22, 'mint') } }),
  researching: () => ({ slots: { body: S(smallBody(27, 58, 16)), ...eyesOpen(26, 51, 6, 4), ground: ground(),
    m0: S(g.transform(g.roundRect(46, 34, 36, 11, 5), { rot: -28, ox: 64, oy: 40 }), 'ink',
      { anims: [{ k: 'swing', amp: 7, period: 3.4, ox: 60, oy: 47 }] }),
    m1: S(g.line(60, 47, 50, 86)), m2: S(g.line(60, 47, 72, 86)),
    a0: S(g.sparkle(87, 14, 7), 'yellow', { fill: true, anims: [{ k: 'twinkle', amp: 0.25, period: 1.8 }] }) } }),
  discovery: () => {
    const rocket = (s) => g.transform(s, { rot: 38, ox: 50, oy: 46 });
    return { slots: {
      body: S(rocket(g.smoothClosed([[50, 12], [58, 24], [60, 42], [57, 58], [43, 58], [40, 42], [42, 24]], 8)), 'ink',
        { anims: [{ k: 'bob', amp: 1.8, period: 1.4 }] }),
      m0: S(rocket(g.circle(50, 34, 4.6)), 'ink', { anims: [{ k: 'bob', amp: 1.8, period: 1.4 }] }),
      m1: S(rocket(g.poly([[42, 46], [33, 58], [42, 58]]))), m2: S(rocket(g.poly([[58, 46], [67, 58], [58, 58]]))),
      a0: S(rocket(g.poly([[45, 60], [50, 72], [55, 60]], true)), 'yellow', { fill: true, anims: [{ k: 'twinkle', amp: 0.3, period: 0.5 }] }),
      ground: S(g.shape(Array.from({ length: 49 }, (_, i) => [12 + i * 1.6, 86 - 4.5 * Math.abs(Math.sin(i / 48 * Math.PI * 4))]))) } };
  },
  ideation: () => {
    const ray = (x0, y0, x1, y1, ph) => S(g.line(x0, y0, x1, y1), 'yellow', { anims: [{ k: 'twinkle', amp: 0.3, period: 1.6, phase: ph }] });
    return { slots: {
      body: S(g.smoothClosed([[50, 16], [62, 20], [68, 32], [64, 46], [58, 56], [42, 56], [36, 46], [32, 32], [38, 20]], 8)),
      m0: S(g.poly([[44, 40], [50, 46], [50, 56], [50, 46], [56, 40]]), 'ink', { width: 'fine' }),
      m1: S(g.line(42, 62, 58, 62)), m2: S(g.line(44, 69, 56, 69)), m3: S(g.line(47, 76, 53, 76)),
      a0: ray(22, 34, 14, 30, 0), a1: ray(28, 16, 22, 8, 0.2), a2: ray(72, 16, 78, 8, 0.4), a3: ray(78, 34, 86, 30, 0.6),
      m4: ray(22, 50, 14, 54, 0.8) } };
  },
  experiment: () => ({ slots: {
    body: S(g.poly([[43, 22], [57, 22], [57, 42], [74, 74], [70, 82], [30, 82], [26, 74], [43, 42]], true)),
    m0: S(g.line(38, 22, 62, 22)),
    m1: S(g.poly([[32, 67], [41, 63], [50, 67], [59, 63], [68, 67], [72, 76], [69, 79], [31, 79], [28, 76]], true), 'purple',
      { fill: true, anims: [{ k: 'shift', dx: 0, dy: 0.8, period: 2.2 }] }),
    a0: S(g.circle(56, 13, 3), 'purple', { anims: [{ k: 'bob', amp: 2.5, period: 2, phase: 0 }] }),
    a1: S(g.circle(66, 8, 4), 'purple', { anims: [{ k: 'bob', amp: 2.5, period: 2, phase: 0.4 }] }),
    a2: S(g.circle(67, 20, 2.4), 'purple', { anims: [{ k: 'bob', amp: 2, period: 2, phase: 0.7 }] }),
    ground: ground(88, 16, 84) } }),
  exploration: () => ({ slots: {
    body: S(g.poly([[12, 32], [36, 25], [60, 33], [88, 25], [86, 78], [60, 86], [36, 78], [14, 85]], true)),
    m0: S(g.line(36, 25, 36, 78), 'ink', { width: 'fine' }), m1: S(g.line(60, 33, 60, 86), 'ink', { width: 'fine' }),
    m2: S(g.bezier([20, 70], [32, 52], [46, 72], [58, 56]), 'ink', { width: 'fine', dash: true }),
    m3: S(g.poly([[64, 58], [72, 66], [68, 62], [64, 66], [72, 58]]), 'ink', { width: 'fine' }),
    a0: S(g.smoothClosed([[56, 8], [64, 12], [66, 20], [56, 36], [46, 20], [48, 12]], 6), 'purple',
      { anims: [{ k: 'bob', amp: 1.8, period: 1.8 }] }),
    a1: S(g.dot(56, 19, 2.4), 'purple', { fill: true, anims: [{ k: 'bob', amp: 1.8, period: 1.8 }] }) } }),
  construction: () => {
    const [aH, aY] = cube(35, 68, 14), [bH, bY] = cube(59.2, 68, 14), [cH, cY] = cube(47.1, 47, 14);
    const conv = (dx, dy, ph) => [{ k: 'converge', dx, dy, period: 3.2, phase: ph }];
    return { slots: { body: S(aH, 'ink', { anims: conv(-3, 2, 0) }), m0: S(aY, 'ink', { width: 'fine', anims: conv(-3, 2, 0) }),
      m1: S(bH, 'ink', { anims: conv(3, 2, 0.1) }), m2: S(bY, 'ink', { width: 'fine', anims: conv(3, 2, 0.1) }),
      m3: S(cH, 'ink', { anims: conv(0, -4, 0.2) }), m4: S(cY, 'ink', { width: 'fine', anims: conv(0, -4, 0.2) }),
      ground: ground(90, 16, 84), ...fan(78, 16, 'mint') } };
  },
  execution: () => ({ slots: {
    body: S(g.gear(40, 50, 20, 8), 'ink', { anims: [{ k: 'spin', speed: 0.55, ox: 40, oy: 50 }] }),
    m0: S(g.circle(40, 50, 6)),
    m1: S(g.gear(68, 70, 13, 7), 'ink', { anims: [{ k: 'spin', speed: -0.75, ox: 68, oy: 70 }] }),
    m2: S(g.circle(68, 70, 3.6)),
    a0: S(g.arc(64, 30, 15, -80, -15), 'blue'), a1: S(g.arc(66, 28, 23, -70, -25), 'blue'),
    a2: S(g.arc(30, 72, 20, 105, 165), 'blue') } }),
  listening: () => {
    const bar = (x, h, slot) => [slot, S(g.line(x, 68 - h / 2, x, 68 + h / 2), 'mint', { anims: [{ k: 'level', min: 0.3, max: 1.7, drive: 'level' }] })];
    return { slots: { body: S(g.arc(50, 58, 31, 180, 360)),
      m0: S(g.roundRect(13, 54, 12, 24, 5.5)), m1: S(g.roundRect(75, 54, 12, 24, 5.5)),
      ...Object.fromEntries([bar(38, 8, 'a0'), bar(44, 14, 'a1'), bar(50, 20, 'a2'), bar(56, 14, 'a3'), bar(62, 8, 'm2')]),
      ground: ground(90, 16, 84) } };
  },
  growth: () => ({ slots: {
    body: S(g.shape([...g.arc(50, 84, 22, 180, 360, 12).pts], true)),
    m0: S(g.quad([50, 72], [48, 60], [50, 48])),
    m1: S(g.smoothClosed([[50, 50], [42, 40], [30, 38], [32, 46], [42, 51]], 6), 'ink', { anims: [{ k: 'swing', amp: 4, period: 3, ox: 50, oy: 50 }] }),
    m2: S(g.smoothClosed([[50, 46], [56, 34], [70, 30], [68, 40], [58, 47]], 6), 'ink', { anims: [{ k: 'swing', amp: 4, period: 3, phase: 0.5, ox: 50, oy: 48 }] }),
    a0: S(g.line(58, 41, 64, 36), 'mint', { width: 'fine' }), a1: S(g.arc(70, 54, 10, -60, 20), 'mint'),
    a2: S(g.arc(72, 54, 16, -50, 10), 'mint'), ground: ground(86, 14, 86) } }),
  achievement: () => ({ slots: {
    body: S(g.poly([[16, 84], [50, 32], [84, 84]], true)),
    m0: S(g.poly([[39, 49], [44, 54], [50, 48], [56, 55], [61, 49]]), 'ink', { width: 'fine' }),
    m1: S(g.line(50, 32, 50, 9)),
    a0: S(g.poly([[50, 9], [67, 12], [61, 16], [67, 21], [50, 21]], true), 'purple', { fill: true, anims: [{ k: 'swing', amp: 5, period: 1.6, ox: 50, oy: 15 }] }),
    m2: S(g.shape(Array.from({ length: 25 }, (_, i) => [6 + i * 0.75, 84 - 5 * Math.abs(Math.sin(i / 24 * Math.PI * 2))]))),
    m3: S(g.shape(Array.from({ length: 25 }, (_, i) => [76 + i * 0.75, 84 - 5 * Math.abs(Math.sin(i / 24 * Math.PI * 2))]))),
    ground: ground(88, 6, 94) } }),
};

export const SYSTEM = {
  asking: () => {
    const q = question(80, 22, 11, 'pink', { anims: [{ k: 'bob', amp: 1.5, period: 1.8 }] });
    return { breathe: true, slots: { body: S(presentBody()), ...eyesOpen(55, 42), a0: q.hook, a1: q.dot } };
  },
  error: () => ({ slots: { body: S(presentBody({ sy: 0.9, oy: 82 })), ...eyesOpen(55, 47, 7, 5),
    m0: S(g.line(84, 10, 84, 25)), m1: S(g.dot(84, 32, 2.2), 'ink', { fill: true }) } }),
  waiting: () => ({ breathe: true, slots: { body: S(presentBody()), ...eyesOpen(55, 42),
    m0: S(g.circle(82, 18, 9), 'secondary', { width: 'fine' }),
    m1: S(g.poly([[82, 12], [82, 18], [86, 21]]), 'secondary', { width: 'fine', anims: [{ k: 'spin', speed: 0.5, ox: 82, oy: 18 }] }) } }),
  recovering: () => ({ slots: { body: S(presentBody()), ...eyesOpen(55, 42),
    m0: S(g.arc(81, 19, 9, -60, 230), 'blue', { anims: [{ k: 'spin', speed: 1.2, ox: 81, oy: 19 }] }),
    m1: S(g.poly([[86, 6], [88.5, 11.2], [83, 12.5]]), 'blue', { anims: [{ k: 'spin', speed: 1.2, ox: 81, oy: 19 }] }) } }),
  disconnected: () => ({ slots: {
    body: S(g.smoothClosed([[22, 71], [26, 62], [34, 53], [44, 48], [56, 48], [67, 53], [75, 62], [80, 71], [70, 73],
      [51, 73.5], [31, 73]], 8), 'secondary'),
    ...eyesClosed(44, 62, 13, 3.8, 'secondary'),   // dormant: calm marks, grey
    m0: S(g.arc(80, 22, 10, 200, 320), 'secondary', { width: 'fine' }), m1: S(g.arc(80, 22, 10, 20, 140), 'secondary', { width: 'fine' }) } }),
};

export const CANONICAL = { ...ENTITY, ...DOODLES };
export const CANONICAL_NAMES = Object.freeze(Object.keys(CANONICAL));
export const SYSTEM_NAMES = Object.freeze(Object.keys(SYSTEM));

// Overlays on any composition (slots m5 and a3 are kept free for them): a background activity orbits as a dot;
// a condition that is not primary shows as a small shape (never colour alone).
export const OVERLAY = {
  // the orbiting dot rests at the upper left (start angle -135 deg), clear of the props on the right
  orbit: (tone = 'mint') => ({ a3: S(g.dot(18.9, 20.9, 2.2), tone, { fill: true, anims: [{ k: 'orbit', r: 44, speed: 0.55, ox: 50, oy: 52, a0: -0.75 * Math.PI }] }) }),
  question: () => { const q = question(92, 82, 6, 'pink'); return { m5: q.hook, a3: q.dot }; },
  alert: () => ({ m5: S(g.line(93, 74, 93, 84)), a3: S(g.dot(93, 89, 1.8), 'ink', { fill: true }) }),
  degraded: () => ({ m5: S(g.arc(91, 84, 6, 200, 320), 'secondary', { width: 'fine' }), a3: S(g.arc(91, 84, 6, 20, 140), 'secondary', { width: 'fine' }) }),
};

export function composition(name) {
  const make = CANONICAL[name] ?? SYSTEM[name];
  if (!make) return null;
  const c = make();
  return { id: name, tier: CANONICAL[name] ? 'A' : 'A-system', breathe: !!c.breathe, slots: c.slots };
}
