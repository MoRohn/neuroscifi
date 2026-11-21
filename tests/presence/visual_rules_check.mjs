// Regression checks for the presence visual rules (docs/presence/DOODLE_GRAMMAR.md §Validator,
// VISUAL_DESIGN_SYSTEM.md §Eye marks): (1) no face-like prop layouts; (2) every composition uses ONE sanctioned
// eye mark chosen by activity energy. Run by tests/presence/test_visual_rules.py.
import assert from 'node:assert/strict';
import * as g from '../../src/neuroscifi/presence/web/geometry.mjs';
import { plan, finalize } from '../../src/neuroscifi/presence/web/planner.mjs';
import { CANONICAL_NAMES, SYSTEM_NAMES, eyeKindFor, eyeMarks } from '../../src/neuroscifi/presence/web/canonical.mjs';
import { TEMPLATES, BODIES, MOTIONS, composeSpec, specToComposition, facePattern, deface } from '../../src/neuroscifi/presence/web/composer.mjs';
import { validateSpec, validateScene, eyeErrors, eyeMarkKind, faceErrors } from '../../src/neuroscifi/presence/web/validator.mjs';
import { SCENE_SCHEMA } from '../../src/neuroscifi/presence/web/contracts.mjs';

let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks++; };
const base = { condition: [], others: [], secondary: [], priority: 'foreground', interaction: [] };
const el = (primitive, x, y, size, o = {}) => ({ primitive, x, y, size, rotation: o.rotation ?? 0, tone: o.tone ?? 'ink' });
const spec = (elements, o = {}) => ({ schema: SCENE_SCHEMA, concept: 'odd thing', body: 'present', eyes: 'open', motion: 'pulse', elements, ...o });

// ---- rule 1: no face-like arrangements ------------------------------------------------------------------------
// The layout that shipped for "resolving contradictions": two small circles above a frown curve, a flame below.
const OLD_CONTRADICTIONS = [el('node', 58, 30, 12), el('node', 84, 30, 12), el('connector', 71, 52, 26),
  el('rays', 71, 74, 18, { tone: 'yellow', rotation: 90 })];
const FACES = {
  'two nodes above a curve (old resolving contradictions)': OLD_CONTRADICTIONS,
  'two dots above an arc': [el('dot', 60, 40, 6), el('dot', 78, 40, 6), el('arc', 69, 58, 20, { rotation: 180 })],
  'two dots above a straight line': [el('dot', 62, 44, 6), el('dot', 80, 44, 6), el('line', 71, 60, 16)],
  'two short vertical strokes above a wave': [el('line', 62, 40, 8, { rotation: 90 }), el('line', 76, 41, 8, { rotation: 90 }), el('wave', 69, 56, 20)],
  'two small circles inside a ring': [el('ring', 70, 50, 34), el('circle', 63, 46, 6), el('circle', 77, 46, 6)],
};
for (const [name, els] of Object.entries(FACES)) {
  ok(facePattern(els) !== null, `facePattern finds: ${name}`);
  ok(validateSpec(spec(els)).some((e) => /face/.test(e)), `validateSpec rejects: ${name}`);
  // the same layout drawn without the spec check is still caught on the finished geometry
  const scene = finalize(specToComposition(spec(els)));
  ok(faceErrors(scene.slots).length > 0 && validateScene(scene).some((e) => /face/.test(e)), `validateScene rejects: ${name}`);
  // a model (Tier C) proposing it is never displayed
  const shown = plan({ ...base, concept: 'odd thing' }, { spec: spec(els) });
  ok(shown.tier !== 'C' && /face/.test(shown.fallbackReason), `Tier C face spec not displayed: ${name}`);
  // the composer constraint re-lays it out into separate objects
  const fixed = deface(els);
  ok(facePattern(fixed) === null && validateSpec(spec(fixed)).length === 0, `deface re-lays out: ${name}`);
}
const NOT_FACES = {
  'two dots stacked vertically above a line': [el('dot', 70, 30, 6), el('dot', 70, 44, 6), el('line', 70, 60, 16)],
  'two big overlapping rings (Venn)': [el('ring', 61, 62, 26), el('ring', 81, 62, 26), el('dot', 71, 62, 8, { tone: 'mint' })],
  'two dots with the line beside them': [el('dot', 50, 30, 6), el('dot', 64, 30, 6), el('line', 86, 70, 14)],
};
for (const [name, els] of Object.entries(NOT_FACES)) ok(facePattern(els) === null && validateSpec(spec(els)).length === 0, `allowed: ${name}`);

// every shipped composition: Tier A, system, Tier B templates, seeded concepts, with every overlay, at every size
const SEEDED = ['quantum gardening', 'tidal folklore', 'harmonising tidal folklore', 'weaving a hypothesis', 'untangling a timeline',
  'folding paper cranes', 'counting river stones', 'listening to static', 'odd thing', 'zzz qqq'];
const ALL = [...CANONICAL_NAMES, ...SYSTEM_NAMES, ...TEMPLATES.map((t) => t.name), ...SEEDED];
const OVERLAYS = [{}, { condition: ['awaiting_permission'] }, { condition: ['error'] }, { condition: ['network_degraded'] }, { subtle: true }];
for (const c of ALL) for (const size of ['normal', 'compact']) for (const o of OVERLAYS) {
  const s = plan({ ...base, concept: c, ...o }, { size });
  ok(!s.fallbackReason && faceErrors(s.slots).length === 0, `${c} (${size}, ${JSON.stringify(o)}) has no face-like layout: ${s.fallbackReason}`);
}
for (const t of TEMPLATES) ok(facePattern(composeSpec(t.name).spec.elements) === null, `template ${t.name} spec is face-free`);

// ---- rule 2: ONE sanctioned eye rule ----------------------------------------------------------------------------
for (const c of ALL) {
  const s = plan({ ...base, concept: c });
  ok(eyeErrors(s.slots).length === 0, `${c}: sanctioned eye marks (${eyeErrors(s.slots)})`);
}
// Tier B: the mark is the one the energy rule picks for the body and motion, for every combination
for (const body of BODIES) for (const motion of MOTIONS) {
  const scene = finalize(specToComposition(spec([el('ring', 76, 40, 20)], { body, motion, eyes: 'open' })));
  const want = eyeKindFor(body, motion);
  ok(eyeMarkKind(scene.slots.eyeL)?.kind === want && eyeMarkKind(scene.slots.eyeR)?.kind === want, `${body}/${motion}: ${want} eyes`);
  ok(eyeErrors(scene.slots).length === 0 && validateScene(scene).length === 0, `${body}/${motion}: valid scene`);
}
// the energy rule itself (references: idle/curious attentive, thinking/resting closed, working chevrons)
ok(eyeKindFor('present', 'breathe') === 'open' && eyeKindFor('exploring', 'drift') === 'open', 'attentive: vertical strokes');
ok(eyeKindFor('thinking', 'pulse') === 'closed' && eyeKindFor('resting', 'still') === 'closed' && eyeKindFor('present', 'orbit') === 'closed', 'calm: arcs');
ok(eyeKindFor('working', 'drift') === 'working' && eyeKindFor('present', 'converge') === 'working', 'working: chevrons');
// a spec cannot pick an arbitrary mark: the energy rule wins; 'none' only removes the marks
const forced = finalize(specToComposition(spec([el('ring', 76, 40, 20)], { body: 'thinking', motion: 'orbit', eyes: 'focused' })));
ok(eyeMarkKind(forced.slots.eyeL)?.kind === 'closed', 'spec eyes value does not override the energy rule');
ok(!finalize(specToComposition(spec([el('ring', 76, 40, 20)], { eyes: 'none' }))).slots.eyeL, "eyes 'none' removes the marks");

// the marks that used to appear are rejected: a horizontal dash pair, touching arcs (a squiggle), mixed kinds
const sceneWith = (eyes) => {
  const s = finalize({ id: 'x', tier: 'B', slots: { body: { shape: g.circle(30, 60, 18), tone: 'ink', width: 'bold', fill: false, dash: false, anims: [] }, ...eyes } });
  return s.slots;
};
const S = (shape, width = 'bold') => ({ shape, tone: 'ink', width, fill: false, dash: false, anims: [] });
ok(eyeErrors(sceneWith({ eyeL: S(g.line(27, 58, 31, 58)), eyeR: S(g.line(34, 58, 38, 58)) })).length > 0, 'horizontal dashes rejected');
ok(eyeErrors(sceneWith({ eyeL: S(g.arc(29, 58, 3, 15, 165, 2.4), 'fine'), eyeR: S(g.arc(35, 58, 3, 15, 165, 2.4), 'fine') })).length > 0, 'touching arcs (squiggle) rejected');
ok(eyeErrors(sceneWith({ eyeL: S(g.spiral(28, 58, 1, 4, 2)), eyeR: S(g.spiral(38, 58, 1, 4, 2)) })).length > 0, 'squiggles rejected');
ok(eyeErrors(sceneWith({ ...eyeMarks('open', 30, 58), eyeR: eyeMarks('closed', 30, 58).eyeR })).length > 0, 'mixed kinds rejected');
ok(eyeErrors(sceneWith({ eyeL: eyeMarks('working', 30, 58).eyeR, eyeR: eyeMarks('working', 30, 58).eyeL })).length > 0, 'chevrons facing away rejected');
ok(eyeErrors(sceneWith({ ...eyeMarks('open', 80, 20) })).length > 0, 'eyes outside the body rejected');
for (const kind of ['open', 'closed', 'working']) ok(eyeErrors(sceneWith(eyeMarks(kind, 30, 58, 0.7))).length === 0, `${kind} marks accepted at Tier B scale`);

console.log(`presence visual rule checks passed: ${checks}`);
