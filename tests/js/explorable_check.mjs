// The explorable runtime (src/neuroscifi/artifacts/web/explorable.mjs) on its own, under node: the expression
// language, the model's semantics against an independent loop, the browser-side spec checks, number words, and
// mount/unmount of every form into a small fake DOM (no window: no timers, observers or listeners outside the
// element), leaving the element as it was and the global object untouched.
import assert from 'node:assert/strict';

const before = new Set(Object.getOwnPropertyNames(globalThis));
const X = await import('../../src/neuroscifi/artifacts/web/explorable.mjs');
let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks += 1; };
const eq = (a, b, msg) => { assert.deepEqual(a, b, msg); checks += 1; };

// ---- the language ----
const env = new Map([['a', 2], ['b', 3], ['x', -1.5]]);
const value = (src) => X.evaluate(X.parse(src), env);
for (const [src, want] of [['1 + 2 * 3', 7], ['-2^2', -4], ['2^-1', 0.5], ['2^3^2', 512], ['a * b - x', 7.5],
  ['a ≤ b', 1], ['a >= b', 0], ['3 × 4 ÷ 2 − 1', 5], ['if(a < b, 10, 20)', 10], ['if(0/0, 1, 2)', 2],
  ['min(3, a, b)', 2], ['clamp(x, 0, 1)', 0], ['sqrt(16)', 4], ['log10(1000)', 3], ['.5e1', 5], ['--a', 2]]) {
  eq(value(src), want, src);
}
ok(Number.isNaN(value('0/0')) && value('1/0') === Infinity && value('ln(0)') === -Infinity, 'IEEE conventions');
ok(Number.isNaN(value('1^(1/0)')) && Object.is(value('1/min(0, -0)'), -Infinity), 'pow and signed zero');
eq(X.parse('a + 2*b'), ['bin', '+', ['var', 'a'], ['bin', '*', ['num', 2], ['var', 'b']]], 'tree shape');
for (const bad of ['', '1 +', '(1', 'foo(1)', 'sin 1', 'a < b < c', '1 = 2', 'min(1)', '1e400', 'a.b',
  '('.repeat(33) + '1' + ')'.repeat(33), '1' + '+1'.repeat(300), 'constructor(1)', '__proto__ + 1']) {
  assert.throws(() => X.parse(bad, ['a', 'b']), X.ExprError, bad);
  checks += 1;
}
assert.throws(() => X.evaluate(X.parse('toString'), new Map()), X.ExprError, 'no value from the prototype');
checks += 1;

// ---- the model, against an independent loop ----
const spring = {
  parameters: [{ name: 'm', label: 'Mass', min: 0.1, max: 5, step: 0.1, value: 1 },
    { name: 'k', label: 'Stiffness', min: 1, max: 50, step: 1, value: 10 },
    { name: 'c', label: 'Damping', min: 0, max: 5, step: 0.1, value: 0.4 }],
  quantities: [{ name: 'omega', expr: 'sqrt(k/m)' }, { name: 'energy', expr: '0.5*m*v^2 + 0.5*k*x^2' }],
  dynamics: { state: [{ name: 'v', initial: '0', update: 'v + (-(k*x) - c*v)/m*dt' },
    { name: 'x', initial: '0.5', update: 'x + v*dt' }], steps: 400, dt: 0.01 },
  views: [{ kind: 'plot', x: 'time', y: ['x'] }, { kind: 'table', items: ['omega', 'x'] },
    { kind: 'plot', x: 'k', y: ['omega'], points: 5 }, { kind: 'bars', items: ['omega', 'energy'] }],
};
eq(X.modelProblems(spring), [], 'the spring is a valid model');
const run = X.evaluateModel(spring, new Map([['c', 1]]));
let [x, v] = [0.5, 0];
const xs = [];
for (let s = 0; s <= 400; s += 1) { xs.push(x); if (s === 400) break; v += (-(10 * x) - 1 * v) / 1 * 0.01; x += v * 0.01; }
eq(run.series.get('x'), xs, 'the updates run in order, as the independent loop does');
eq(run.sweeps[0].get('x'), [1, 13.25, 25.5, 37.75, 50], 'the sweep points');
ok(run.values.get('omega') === Math.sqrt(10), 'a constant quantity');
const broken = structuredClone(spring);
broken.views.push({ kind: 'plot', x: 'k', y: ['energy'] });
ok(X.modelProblems(broken).some((p) => p.includes('sweep')), 'a sweep of a changing quantity is refused');

// ---- the browser-side spec check ----
const envelope = (form, data, title = 'T') => ({ spec: form, version: 1, title, description: 'D', data, options: {}, sources: [], made_by: { generator: 'g2.explorable', version: 1 } });
eq(X.specProblems(envelope('explorable.model', spring)), [], 'model spec');
ok(X.specProblems(envelope('chart_line', spring)).length === 1, 'a form this runtime does not show');
ok(X.specProblems(envelope('explorable.model', spring, 'a‮b')).length === 1, 'bidirectional override refused');
ok(X.specProblems(null).length === 1 && X.specProblems([]).length === 1, 'not an object');
const trace = { code: ['a', 'b'], arrays: [{ name: 'a', label: 'A' }],
  steps: [{ note: 'one', line: 0, arrays: [[1, 2, 3]], pointers: [{ name: 'lo', array: 0, index: 0 }], marks: [{ array: 0, index: 2, kind: 'found' }], vars: [{ name: 'n', value: 3 }] },
    { note: 'two', line: 1, arrays: [[1, 2, 3]] }] };
eq(X.specProblems(envelope('explorable.steps', trace)), [], 'trace spec');
const badTrace = structuredClone(trace);
badTrace.steps[0].pointers[0].index = 7;
ok(X.specProblems(envelope('explorable.steps', badTrace)).some((p) => p.includes('outside')), 'pointer outside');
const solid = { vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], faces: [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], unit: 'mm' };
eq(X.specProblems(envelope('solid.view', solid)), [], 'solid spec');
ok(X.specProblems(envelope('solid.view', { ...solid, faces: [[0, 1, 9]] })).length === 1, 'face outside');

// ---- numbers in words ----
for (const [n, want] of [[0, '0'], [-0, '0'], [3.16227766, '3.162'], [1234.5678, '1235'], [0.000123456, '0.0001235'],
  [-2.5, '−2.5'], [1e7, '1 × 10⁷'], [-1.5e-7, '−1.5 × 10⁻⁷'], [NaN, 'undefined'], [Infinity, 'infinite']]) {
  eq(X.fmt(n), want, `fmt(${n})`);
}
const t = X.ticks(0.13, 9.7, 5);
ok(t.lo === 0 && t.hi === 10 && t.list.join() === '0,2,4,6,8,10', 'value ticks widen to whole steps');
const tx = X.ticks(0.5, 50, 3, true);
ok(tx.lo === 0.5 && tx.hi === 50 && tx.list.join() === '20,40', 'an exact axis keeps its range');

// ---- mount and unmount into a fake DOM ----
class Node {
  constructor(tag, doc) { this.tagName = tag; this.ownerDocument = doc; this.childNodes = []; this.attributes = new Map(); this.listeners = new Map(); this.classList = { toggle() {} }; this.clientWidth = 600; this.disabled = false; this.style = {}; }
  get firstChild() { return this.childNodes[0] || null; }
  append(...kids) { for (const k of kids) this.childNodes.push(k); }
  replaceChildren(...kids) { this.childNodes = [...kids]; }
  setAttribute(k, val) { assert.notEqual(k.toLowerCase(), 'style', 'no style attribute'); assert.ok(!/^on/i.test(k), 'no event attribute'); this.attributes.set(k, String(val)); }
  getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
  removeAttribute(k) { this.attributes.delete(k); }
  addEventListener(type, fn) { this.listeners.set(type, fn); }
  set textContent(text) { this.childNodes = [{ text: String(text) }]; }
  get textContent() { return this.childNodes.map((c) => c.text ?? c.textContent ?? '').join(''); }
}
const doc = { defaultView: null };
doc.createElement = (tag) => { const n = new Node(tag, doc); if (tag === 'canvas') n.getContext = () => null; return n; };
doc.createElementNS = (ns, tag) => { assert.equal(ns, 'http://www.w3.org/2000/svg'); return new Node(tag, doc); };
doc.createTextNode = (text) => ({ text });
const count = (n) => 1 + (n.childNodes || []).reduce((s, c) => s + count(c), 0);
const texts = (n) => (n.text !== undefined ? [n.text] : (n.childNodes || []).flatMap(texts));
for (const [form, data] of [['explorable.model', spring], ['explorable.steps', trace], ['solid.view', solid]]) {
  const host = new Node('div', doc);
  const kept = { text: 'fallback' };
  host.append(kept);
  const handle = X.mount(host, envelope(form, data, 'A title with <b>tags</b> & signs'), { heading: 2 });
  ok(host.childNodes.length === 1 && host.childNodes[0].attributes.get('class') === 'nsf-xp', `${form}: one wrapper`);
  ok(count(host) > 20, `${form}: a view was built`);
  ok(texts(host).includes('A title with <b>tags</b> & signs'), `${form}: the title is text, not markup`);
  ok(texts(host).some((s) => s.includes('not evidence')), `${form}: labelled as made by the system`);
  handle.unmount();
  ok(host.childNodes.length === 1 && host.childNodes[0] === kept, `${form}: unmount puts the element back`);
}
const host = new Node('div', doc);
X.mount(host, envelope('explorable.model', broken));
ok(texts(host).some((s) => s.startsWith('This could not be shown')), 'an invalid spec shows a plain message');
// the steps view moves
const stepsHost = new Node('div', doc);
X.mount(stepsHost, envelope('explorable.steps', trace));
const find = (n, pred) => (pred(n) ? n : (n.childNodes || []).map((c) => find(c, pred)).find(Boolean));
const next = find(stepsHost, (n) => n.attributes && n.attributes.get('aria-label') === 'Next step');
next.listeners.get('click')();
ok(texts(stepsHost).some((s) => s === 'Step 2 of 2: two'), 'Next moves to step 2');

const added = Object.getOwnPropertyNames(globalThis).filter((k) => !before.has(k));
eq(added, [], 'the runtime adds nothing to the global object');
console.log(`explorable runtime: ${checks} checks passed`);
