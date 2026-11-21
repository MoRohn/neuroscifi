// Geometry and spec validator (docs/presence/DOODLE_GRAMMAR.md §Validator). Every Tier B/C scene passes here before
// display; a failure means the planner shows the nearest canonical composition instead.
// validateSpec mirrors presence/scene_spec.py (the server validates first; this is the second, independent check).
import { SCENE_SCHEMA, SLOTS, TONES, ACCENT_TONES, BUDGET, pointsFor } from './contracts.mjs';
import { PRIMITIVES, BODIES, EYES, MOTIONS } from './composer.mjs';
import { TOKENS, contrast } from './theme.mjs';
import { bbox } from './geometry.mjs';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const KEYS = ['schema', 'concept', 'body', 'eyes', 'motion', 'elements'];
const EL_KEYS = ['primitive', 'x', 'y', 'size', 'rotation', 'tone', 'count', 'sides'];

export function validateSpec(sp) {
  const errors = [];
  if (!sp || typeof sp !== 'object' || Array.isArray(sp)) return ['spec must be an object'];
  for (const k of Object.keys(sp)) if (!KEYS.includes(k)) errors.push(`unexpected property ${k}`);
  for (const k of KEYS) if (!(k in sp)) errors.push(`missing ${k}`);
  if (errors.length) return errors;
  if (sp.schema !== SCENE_SCHEMA) errors.push('unsupported schema version');
  if (typeof sp.concept !== 'string' || !/^[a-z][a-z ]{0,47}$/.test(sp.concept)) errors.push('concept must be 1-48 lowercase letters');
  if (!BODIES.includes(sp.body)) errors.push('unknown body');
  if (!EYES.includes(sp.eyes)) errors.push('unknown eyes');
  if (!MOTIONS.includes(sp.motion)) errors.push('unknown motion');
  if (!Array.isArray(sp.elements) || sp.elements.length < 1 || sp.elements.length > 6) return [...errors, 'elements must hold 1 to 6 items'];
  sp.elements.forEach((e, i) => {
    if (!e || typeof e !== 'object' || Array.isArray(e)) { errors.push(`elements[${i}] must be an object`); return; }
    for (const k of Object.keys(e)) if (!EL_KEYS.includes(k)) errors.push(`elements[${i}]: unexpected property ${k}`);
    if (!PRIMITIVES.includes(e.primitive)) errors.push(`elements[${i}]: unknown primitive`);
    if (!TONES.includes(e.tone)) errors.push(`elements[${i}]: tone outside the palette`);
    if (!isNum(e.x) || e.x < 40 || e.x > 96) errors.push(`elements[${i}]: x out of range`);
    if (!isNum(e.y) || e.y < 8 || e.y > 92) errors.push(`elements[${i}]: y out of range`);
    if (!isNum(e.size) || e.size < 4 || e.size > 36) errors.push(`elements[${i}]: size out of range`);
    if (!isNum(e.rotation) || e.rotation < -180 || e.rotation > 180) errors.push(`elements[${i}]: rotation out of range`);
    if ('count' in e && !(Number.isInteger(e.count) && e.count >= 1 && e.count <= 5)) errors.push(`elements[${i}]: count out of range`);
    if ('sides' in e && !(Number.isInteger(e.sides) && e.sides >= 3 && e.sides <= 8)) errors.push(`elements[${i}]: sides out of range`);
  });
  if (errors.length) return errors;
  const els = sp.elements;
  els.forEach((e, i) => {
    const h = e.size / 2;
    if (e.x - h < 36 || e.y - h < 4 || e.x + h > 98 || e.y + h > 94) errors.push(`elements[${i}]: outside the prop area`);
  });
  const accents = els.filter((e) => ACCENT_TONES.includes(e.tone)).map((e) => e.tone);
  if (accents.length > 3) errors.push('more than three accent-coloured elements (accents are sparse)');
  if (new Set(accents).size > 2) errors.push('more than two accent colours');
  for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
    const a = els[i], b = els[j], ratio = a.size / b.size;
    if (Math.abs(a.x - b.x) < 3 && Math.abs(a.y - b.y) < 3 && ratio >= 0.8 && ratio <= 1.25) errors.push(`elements[${i}] and [${j}] are stacked on each other (unreadable)`);
  }
  const strokes = els.reduce((n, e) => n + (['rays', 'dot', 'node'].includes(e.primitive) ? (e.count ?? 1) : 1), 0);
  if (strokes > 10) errors.push('too many strokes for the complexity ceiling');
  return errors;
}

const MIN_CONTRAST = { ink: 4.5, secondary: 3 };   // meaning-bearing tones; accents are decorative (never sole carriers)
export function paletteContrastErrors() {
  const errors = [];
  for (const [theme, t] of Object.entries(TOKENS)) {
    for (const [tone, min] of Object.entries(MIN_CONTRAST)) {
      const c = contrast(t[tone], t.bg);
      if (c < min) errors.push(`${theme}: ${tone} contrast ${c.toFixed(2)} < ${min}`);
    }
  }
  return errors;
}

// Validate a finished (resampled) scene: slot vocabulary, finite in-bounds geometry, palette-only tones, sparse
// accents, readable minimum size, the complexity ceiling and the animation budget.
export function validateScene(scene) {
  const errors = [];
  const entries = Object.entries(scene.slots ?? {});
  if (!entries.length || !scene.slots.body) errors.push('a scene needs a body');
  let accentSlots = 0, animated = 0, points = 0;
  const accentTones = new Set();
  for (const [name, slot] of entries) {
    if (!SLOTS.includes(name)) { errors.push(`unknown slot ${name}`); continue; }
    if (!TONES.includes(slot.tone)) errors.push(`${name}: tone outside the palette`);
    if (ACCENT_TONES.includes(slot.tone) && slot.visible !== false) { accentSlots++; accentTones.add(slot.tone); }
    const p = slot.pts;
    if (!(p instanceof Float64Array) || p.length !== pointsFor(name) * 2) { errors.push(`${name}: not resampled`); continue; }
    points += p.length / 2;
    for (let i = 0; i < p.length; i++) if (!Number.isFinite(p[i]) || p[i] < -4 || p[i] > 104) { errors.push(`${name}: geometry outside the canvas`); break; }
    const [x0, y0, x1, y1] = bbox(p);
    if (slot.visible !== false && Math.hypot(x1 - x0, y1 - y0) < 1.5) errors.push(`${name}: too small to read`);
    for (const a of slot.anims ?? []) {
      animated++;
      const amp = Math.abs(a.amp ?? a.dx ?? 0) + Math.abs(a.dy ?? 0);
      if (a.k !== 'twinkle' && a.k !== 'level' && amp > BUDGET.maxAmplitude) errors.push(`${name}: animation amplitude over budget`);
      if (a.k === 'spin' && Math.abs(a.speed) > BUDGET.maxRotationSpeed) errors.push(`${name}: rotation over budget`);
      if (a.k === 'orbit' && Math.abs(a.speed) > BUDGET.maxRotationSpeed) errors.push(`${name}: orbit over budget`);
    }
  }
  if (accentSlots > 7) errors.push('too many accent strokes');
  if (accentTones.size > 3) errors.push('more than three accent colours in one scene');
  if (animated > BUDGET.maxAnimatedSlots + 2) errors.push('animation budget exceeded');
  if (points > 72 + 13 * 28) errors.push('complexity over budget');
  return errors;
}
