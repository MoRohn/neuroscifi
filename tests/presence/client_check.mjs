// Presence client modules under node (no browser): geometry, planner tiers, validator, interpreter arbitration,
// motion engine continuity. Run by tests/presence/test_client_js.py.
import assert from 'node:assert/strict';
import * as g from '../../src/neuroscifi/presence/web/geometry.mjs';
import { plan, nearest } from '../../src/neuroscifi/presence/web/planner.mjs';
import { CANONICAL_NAMES, SYSTEM_NAMES } from '../../src/neuroscifi/presence/web/canonical.mjs';
import { TEMPLATES, composeSpec } from '../../src/neuroscifi/presence/web/composer.mjs';
import { validateSpec, validateScene, paletteContrastErrors } from '../../src/neuroscifi/presence/web/validator.mjs';
import { interpret, ACTIVITY_CONCEPT } from '../../src/neuroscifi/presence/web/interpreter.mjs';
import { MotionEngine, LIFE } from '../../src/neuroscifi/presence/web/motion.mjs';
import { ACTIVITIES, BUDGET, SLOTS, pointsFor, sizeClass } from '../../src/neuroscifi/presence/web/contracts.mjs';

let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks++; };
const base = { condition: [], others: [], secondary: [], priority: 'foreground', interaction: [] };

// ---- geometry
const sq = g.resample(g.poly([[0, 0], [10, 0], [10, 10], [0, 10]], true), 28);
ok(sq.length === 56 && sq[0] === sq[54] && sq[1] === sq[55], 'closed shapes repeat the first point');
for (const corner of [[10, 0], [10, 10], [0, 10]]) {
  let hit = false; for (let i = 0; i < 56; i += 2) if (sq[i] === corner[0] && sq[i + 1] === corner[1]) hit = true;
  ok(hit, `corner ${corner} preserved`);
}
const circ = g.resample(g.circle(50, 50, 20), 72);
ok(Math.abs(g.area(circ)) > 1200, 'circle area');
const shifted = new Float64Array(circ.length);
for (let i = 0; i <= 71; i++) { const j = (i + 17) % 71; shifted[2 * i] = circ[2 * j]; shifted[2 * i + 1] = circ[2 * j + 1]; }
const aligned = g.align(circ, shifted, true);
ok(Math.abs(aligned[0] - circ[0]) < 1e-9 && Math.abs(aligned[1] - circ[1]) < 1e-9, 'align finds the matching start');
ok(/^M[\d.\- ]+(Q[\d.\- ]+)+$/.test(g.toPath(circ, true)), 'smooth closed path syntax');
ok(sizeClass(20) === 'indicator' && sizeClass(40) === 'compact' && sizeClass(96) === 'normal' && sizeClass(200) === 'expanded', 'size classes');

// ---- planner: every canonical and system composition is Tier A and valid; templates are Tier B
for (const c of [...CANONICAL_NAMES, ...SYSTEM_NAMES]) {
  const s = plan({ ...base, concept: c });
  ok(s.tier.startsWith('A') && !s.fallbackReason, `${c} canonical`);
  ok(validateScene(s).length === 0, `${c} valid`);
  for (const [name, slot] of Object.entries(s.slots)) ok(slot.pts.length === pointsFor(name) * 2, `${c}.${name} resampled`);
}
for (const t of TEMPLATES) {
  const s = plan({ ...base, concept: t.name });
  ok(s.tier === 'B' && s.template === t.name, `template ${t.name} -> Tier B (${s.fallbackReason})`);
}
for (const a of ACTIVITIES) ok(plan({ ...base, concept: ACTIVITY_CONCEPT[a] }).tier !== 'fallback', `activity ${a} has a composition`);
const novel = plan({ ...base, concept: 'harmonising tidal folklore' });
ok(novel.tier === 'B' && novel.template === null && validateScene(novel).length === 0, 'novel concept: seeded procedural doodle');
ok(nearest('reading the manual') === 'reading' && nearest('qqq') === 'present', 'nearest canonical');

// ---- validator: specs are data, bounded, palette-only
const good = composeSpec('evaluating evidence').spec;
ok(validateSpec(good).length === 0, 'template spec valid');
ok(validateSpec({ ...good, extra: '<script>' }).length > 0, 'unknown key rejected');
ok(validateSpec({ ...good, elements: [{ ...good.elements[0], tone: '#ff00ff' }] }).length > 0, 'colour outside palette rejected');
ok(validateSpec({ ...good, elements: [{ ...good.elements[0], primitive: 'path', d: 'M0 0' }] }).length > 0, 'path strings rejected');
ok(validateSpec({ ...good, elements: [{ ...good.elements[0], x: 500 }] }).length > 0, 'out of bounds rejected');
ok(validateSpec({ ...good, elements: Array(7).fill(good.elements[0]) }).length > 0, 'element count bounded');
ok(validateSpec({ ...good, schema: 'nsf.presence.scene/9' }).length > 0, 'schema version checked');
ok(validateSpec('<svg/>').length > 0, 'markup rejected');
const badSpecScene = plan({ ...base, concept: 'odd thing' }, { spec: { ...good, elements: [{ ...good.elements[0], tone: 'red' }] } });
ok(badSpecScene.tier !== 'C' && /model spec rejected/.test(badSpecScene.fallbackReason), 'invalid Tier C spec never displayed');
const tierC = plan({ ...base, concept: 'odd thing' }, { spec: { ...good, concept: 'odd thing' } });
ok(tierC.tier === 'C', 'valid Tier C spec displayed');
ok(paletteContrastErrors().length === 0, 'ink/secondary contrast in both themes');

// ---- interpreter: arbitration from real signals only
const research = { schema: 'nsf.presence.state/1', primary: null, condition: ['healthy'], interaction: [], intensity: 0.5,
  activities: [{ activity: 'searching', priority: 'foreground', rank: 3, since: 10, subsystem: 'web_cortex', secondary: ['web_research'], step: { index: 1, of: 4 }, label: 'Zephyr' }] };
research.primary = research.activities[0];
let i = interpret(research, { connection: 'ok' }, 1000);
ok(i.activity === 'searching' && i.concept === 'researching' && /step 1 of 4/.test(i.text) && !/%/.test(i.text), 'research intent');
const now = 100000;
i = interpret(research, { connection: 'ok', voice: { mic_capturing: true, speaking: false, output_muted: false, mode: 'voice_text', level: 0.4, at: now } }, now);
ok(i.activity === 'listening' && i.level === 0.4 && i.audio === 'mic', 'mic capture outranks foreground research');
i = interpret(research, { connection: 'ok', voice: { mic_capturing: false, speaking: true, output_muted: true, mode: 'voice_text', level: 0.4, at: now } }, now);
ok(i.activity === 'searching' && i.interaction.includes('voice_muted'), 'muted output never shown as speech');
i = interpret(research, { connection: 'ok', voice: { mic_capturing: true, speaking: true, output_muted: false, mode: 'text_only', level: 0.9, at: now } }, now);
ok(i.activity === 'searching' && i.level === null && i.interaction.includes('text_only'), 'text-only: no audio-driven motion');
i = interpret(research, { connection: 'disconnected' }, now);
ok(i.concept === 'disconnected' && /unknown/.test(i.text), 'disconnected claims no activity');
const bg = { ...research, activities: [{ ...research.activities[0], priority: 'background', rank: 1 }] };
i = interpret(bg, { connection: 'ok' }, now);
ok(i.subtle && i.concept === 'exploring', 'background research shown subtly');
ok(plan(i).slots.a3?.anims?.[0]?.k === 'orbit', 'background research orbits as a dot');
const both = { ...research, activities: [research.activities[0], { activity: 'reading', priority: 'background', rank: 1, since: 5, subsystem: 'web_cortex', secondary: [] }] };
i = interpret(both, { connection: 'ok' }, now);
ok(i.activity === 'searching' && plan(i).slots.a3?.anims?.[0]?.k === 'orbit', 'secondary background activity as orbit accent');
const asking = { ...research, condition: ['awaiting_permission'], activities: [{ activity: 'waiting', concept: 'asking', priority: 'user_critical', rank: 5, since: 1, secondary: [] }, research.activities[0]] };
i = interpret(asking, { connection: 'ok' }, now);
ok(i.concept === 'asking' && i.assertive, 'permission is user-critical and assertive');
ok(interpret(null, { connection: 'connecting' }).unknown, 'no state yet: unknown, not idle claims');
// R2-08: unreachable speech engines are named, not reported as failing search providers (nor as healthy)
const speechDown = { ...research, condition: ['network_degraded'], sources: { voice: 'engines_unavailable', voice_engines_down: 'asr,tts' } };
i = interpret(speechDown, { connection: 'ok' }, now);
ok(/speech recognition and speech output unavailable/.test(i.text) && !/search providers/.test(i.text), 'speech engines down named');
i = interpret({ ...speechDown, sources: { ...speechDown.sources, search: 'degraded' } }, { connection: 'ok' }, now);
ok(/speech recognition/.test(i.text) && /search providers are failing/.test(i.text), 'both causes named');
i = interpret({ ...research, condition: ['network_degraded'] }, { connection: 'ok' }, now);
ok(/search providers are failing/.test(i.text), 'provider failure keeps its words');

// ---- motion: continuity, limits, interruption, reduced motion, settling
const e = new MotionEngine();
e.setScene(plan({ ...base, concept: 'present' }), { t: 0, instant: true });
let t = 0;
const dt = 1 / 60;
const snapshot = () => new Map([...e.slots].filter(([, s]) => s.pos).map(([n, s]) => [n, Float64Array.from(s.pos)]));
let maxJump = 0;
const stepTrack = (frames) => {
  for (let f = 0; f < frames; f++) {
    const before = snapshot();
    e.step(dt, t += dt);
    for (const [n, p] of before) { const q = e.slots.get(n).pos; if (!q) continue; for (let k = 0; k < p.length; k++) maxJump = Math.max(maxJump, Math.abs(q[k] - p[k])); }
  }
};
e.setScene(plan({ ...base, concept: 'thinking' }), { t });
stepTrack(9);                                  // interrupt mid-morph, three times in a row
e.setScene(plan({ ...base, concept: 'researching' }), { t });
stepTrack(5);
e.setScene(plan({ ...base, concept: 'responding' }), { t });
stepTrack(4);
e.setScene(plan({ ...base, concept: 'resting' }), { t });
stepTrack(240);
ok(maxJump <= BUDGET.maxVelocity * dt + 1e-6, `no snapping: max per-frame displacement ${maxJump.toFixed(2)} <= ${BUDGET.maxVelocity * dt}`);
ok(e.maxSpeed <= BUDGET.maxVelocity + 1e-6, 'velocity limit holds');
let n = 0;
while (e.step(dt, t += dt) && n < 6000) n++;
ok(n < 6000, `resting decays to stillness and the loop may stop (${(n * dt).toFixed(1)} s)`);
const r = new MotionEngine();
r.reduced = true;
r.setScene(plan({ ...base, concept: 'thinking' }), { t: 0, instant: true });
ok(!r.step(dt, 1), 'reduced motion: static, no frame loop');
ok(!r.alive && [...r.slots.values()].every((s) => !s.pos || r.view(s) === s.pos), 'reduced motion: no life layer, the physics is drawn');

// ---- life layer: always alive on screen, calm at rest, driven only by real inputs, never jumps
const offset = (en, name) => {   // largest distance between what is drawn and where the physics is
  const s = en.slots.get(name), v = en.view(s);
  let m = 0; for (let k = 0; k < v.length; k++) m = Math.max(m, Math.abs(v[k] - s.pos[k]));
  return m;
};
const meanX = (p) => { let x = 0; for (let k = 0; k < p.length; k += 2) x += p[k]; return x / (p.length / 2); };
const life = (setup = () => {}, seconds = 4, concept = 'present', h = dt, each = null) => {
  const en = new MotionEngine();
  en.setScene(plan({ ...base, concept }), { t: 0, instant: true });
  en.step(h, 0);
  setup(en);
  let tt = 0, maxOff = 0, settledAll = true, maxStep = 0;
  for (let f = 0; f < seconds / h; f++) {
    each?.(en, f);
    const before = Float64Array.from(en.view(en.slots.get('body')));
    settledAll = !en.step(h, tt += h) && settledAll;
    const after = en.view(en.slots.get('body'));
    for (let k = 0; k < after.length; k++) maxStep = Math.max(maxStep, Math.abs(after[k] - before[k]));
    if (f * h > 2) maxOff = Math.max(maxOff, offset(en, 'body'));
  }
  return { en, maxOff, settledAll, maxStep };
};
const idle = life();
const rb = idle.en.env.rb;
ok(!e.step(dt, t += dt) && e.alive && offset(e, 'body') > 0.02, 'settled physics (resting, after its decay) is still drawn alive');
ok(idle.maxOff > 0.05, `idle: the entity is never frozen (offset ${idle.maxOff.toFixed(2)})`);
ok(idle.maxOff < (LIFE.contourIdle + LIFE.breathIdle) * rb * 2.2 + 0.2, `idle: calm, ${idle.maxOff.toFixed(2)} units (body radius ${rb.toFixed(1)})`);
const seated = life(() => {}, 2, 'evaluating evidence');
ok(seated.en.slots.get('ground').pos && offset(seated.en, 'ground') === 0 && offset(seated.en, 'm0') > 0, 'the ground stays put; props drift');
const busy = life((en) => { en.activity = 1; });
ok(busy.maxOff > idle.maxOff * 1.6, `real activity deepens the motion (${busy.maxOff.toFixed(2)} vs ${idle.maxOff.toFixed(2)})`);
const loud = life((en) => { en.level = 0.9; }, 4, 'listening');
const quiet = life(() => {}, 4, 'listening');
ok(loud.maxOff > quiet.maxOff * 1.5, `the measured audio level morphs the body (${loud.maxOff.toFixed(2)} vs ${quiet.maxOff.toFixed(2)})`);
const gaze = life((en) => en.look(1, 0), 2);
const eye = gaze.en.slots.get('eyeL');
ok(meanX(gaze.en.view(eye)) - meanX(eye.pos) > rb * LIFE.gazeEye * 0.8, 'the eyes follow the pointer');
for (const [h, label] of [[1 / 60, '60 fps'], [1 / 30, '30 fps']]) {
  // a storm of input: three scroll events per frame for half a second (both directions), keystrokes, a stage change
  const storm = (sign) => life((en) => { en.tap(1); en.pulse(); en.look(-1, 1); }, 3, 'present', h,
    (en, f) => { if (f * h < 0.5) for (let k = 0; k < 3; k++) en.kick(sign * 320); });
  for (const sign of [1, -1]) {
    const run = storm(sign);
    // the physics budget is 260 units/s; input through the life layer stays far below it
    ok(run.maxStep / h < 40, `${label}, scroll ${sign > 0 ? 'down' : 'up'}: input never snaps (${(run.maxStep / h).toFixed(0)} units/s at most)`);
    const sw = run.en._sway();
    ok(sw > -LIFE.swayUp && sw < LIFE.swayDown, `${label}: scroll inertia stays inside its soft limits`);
  }
}
const kicked = life((en) => en.kick(-320), 4);
ok(Math.abs(kicked.en._sway()) < 0.05, 'scroll inertia settles back to rest, nothing sticks at a limit');
const off = new MotionEngine();
off.reduced = true;
off.setScene(plan({ ...base, concept: 'present' }), { t: 0, instant: true });
off.kick(-320); off.tap(1); off.pulse(); off.look(1, 1);
ok(off.env.swayT === 0 && off.env.squashV === 0 && off.env.rippleA === 0 && off.env.tx === 0, 'with the life layer off, input is ignored, not stored');
const paused = life((en) => { en.kick(-320); en.pulse(); en.rest(); }, 1);
ok(paused.en.env.swayT === 0 && paused.en.env.rippleA === 0, 'rest() (loop restart) drops impulses from a moment nobody saw');
const broken = life((en) => { en.activity = NaN; en.vigor = Infinity; en.level = NaN; en.look(NaN, 0); en.kick(NaN); }, 1);
ok([...broken.en.view(broken.en.slots.get('body'))].every(Number.isFinite), 'a bad input never takes the figure away');
const dimmed = life((en) => { en.dim = true; en.look(1, 0); }, 4);
ok(dimmed.maxOff < idle.maxOff && dimmed.en.env.gx === 0 && dimmed.en.glow() < idle.en.glow(),
  'disconnected or unknown: fainter, no attention, a lower light');
const away = life((en) => en.focus(false), 6);
ok(away.maxOff < idle.maxOff, 'a background window calms the entity');
const still = life((en) => { en.vigor = 0; }, 4);
ok(still.maxOff < 0.15, 'with no vigour (e.g. motion intensity at zero) the life layer adds nothing');
const c = plan({ ...base, concept: 'reading' }, { size: 'compact' });
ok(!c.slots.m3 && c.slots.a2?.visible === false && c.slots.body.visible, 'compact size drops fine detail');
const ind = plan({ ...base, concept: 'reading' }, { size: 'indicator' });
ok(Object.entries(ind.slots).every(([k, s]) => s.visible === ['body', 'eyeL', 'eyeR'].includes(k)), 'indicator keeps body and eyes');
const indObj = plan({ ...base, concept: 'listening' }, { size: 'indicator' });
ok(Object.entries(indObj.slots).every(([k, s]) => s.visible === ['body', 'm0', 'm1'].includes(k)), 'indicator of an object doodle keeps its first marks');
ok(SLOTS.length === 14, 'fixed slot pool');

console.log(`presence client checks passed: ${checks}`);
