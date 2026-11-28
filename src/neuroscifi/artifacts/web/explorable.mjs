// NeuroSciFi explorable runtime (OMNIMIND lane G2, DESIGN §6 and §8). One fixed, audited file.
//
// It shows a declared specification as something to explore: a model with sliders and plots
// (explorable.model), a step-through of a recorded trace (explorable.steps), or a solid to turn (solid.view).
// Two settings, one file: inlined into a self-contained document (its SHA-256 is pinned in that document's
// Content-Security-Policy), or imported as a first-party module by a host page that calls mount(element, spec).
//
// Rules this file keeps: expressions are PARSED into a tree and the tree is evaluated (no eval, no Function, no
// dynamic import); no network, no storage, no cookies; text reaches the page only as text nodes and attribute
// values, never as markup; it owns nothing outside the element it is given (no document-wide styles, no global
// state), and unmount() removes every node, listener, timer and observer it added.
// The same expression language lives in Python (artifacts/expr.py); tests prove the two agree.

const SVG = 'http://www.w3.org/2000/svg'; // the SVG namespace name (an identifier, never fetched)

// ------------------------------------------------------------------------------------------------------------
// The expression language (mirrors artifacts/expr.py)

export const LIMITS = Object.freeze({ source: 500, nodes: 256, depth: 32, name: 32, steps: 5000, sweep: 401 });
export const FUNCTIONS = Object.freeze({
  sin: [1, 1], cos: [1, 1], tan: [1, 1], exp: [1, 1], ln: [1, 1], log10: [1, 1], sqrt: [1, 1], abs: [1, 1],
  min: [2, 8], max: [2, 8], clamp: [3, 3], if: [3, 3],
});
const CONSTANTS = Object.freeze({ pi: Math.PI, e: Math.E });
const RESERVED = new Set([...Object.keys(FUNCTIONS), ...Object.keys(CONSTANTS), 't', 'dt']);
const ALIASES = Object.freeze({ '−': '-', '×': '*', '÷': '/', '·': '*', '≤': '<=', '≥': '>=', '≠': '!=' });
const TWO = ['<=', '>=', '==', '!='];
const ONE = '+-*/^()<>,';
const COMPARE = ['<', '<=', '>', '>=', '==', '!='];
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

export class ExprError extends Error {}

export function tokenize(src) {
  if (typeof src !== 'string') throw new ExprError('an expression is text');
  if (src.length > LIMITS.source) throw new ExprError(`an expression is longer than ${LIMITS.source} characters`);
  const number = /(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?/y;
  const name = /[A-Za-z_][A-Za-z0-9_]*/y;
  const out = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === ' ' || ch === '\t') { i += 1; continue; }
    if (has(ALIASES, ch)) { out.push(['op', ALIASES[ch], i]); i += 1; continue; }
    if (TWO.includes(src.slice(i, i + 2))) { out.push(['op', src.slice(i, i + 2), i]); i += 2; continue; }
    if (ONE.includes(ch)) { out.push(['op', ch, i]); i += 1; continue; }
    if (/[0-9.]/.test(ch)) {
      number.lastIndex = i;
      const m = number.exec(src);
      if (!m) throw new ExprError(`a malformed number at position ${i + 1}`);
      if (!Number.isFinite(Number(m[0]))) throw new ExprError(`the number at position ${i + 1} is too large`);
      out.push(['num', Number(m[0]), i]);
      i += m[0].length;
      continue;
    }
    name.lastIndex = i;
    const m = name.exec(src);
    if (m) {
      if (m[0].length > LIMITS.name) throw new ExprError(`a name at position ${i + 1} is longer than ${LIMITS.name} characters`);
      out.push(['name', m[0], i]);
      i += m[0].length;
      continue;
    }
    throw new ExprError(`unexpected character at position ${i + 1}`);
  }
  out.push(['end', null, src.length]);
  return out;
}

class Parser {
  constructor(src, names) { this.tokens = tokenize(src); this.at = 0; this.names = names; this.nodes = 0; }
  peek() { return this.tokens[this.at]; }
  take() { return this.tokens[this.at++]; }
  isOp(...values) { const t = this.tokens[this.at]; return t[0] === 'op' && values.includes(t[1]); }
  node(value) {
    this.nodes += 1;
    if (this.nodes > LIMITS.nodes) throw new ExprError(`an expression has more than ${LIMITS.nodes} parts`);
    return value;
  }
  nest(depth) {
    if (depth > LIMITS.depth) throw new ExprError(`an expression is nested deeper than ${LIMITS.depth}`);
    return depth;
  }
  expr(depth) {
    const left = this.sum(depth);
    if (this.isOp(...COMPARE)) {
      const op = this.take()[1];
      const right = this.sum(depth);
      if (this.isOp(...COMPARE)) throw new ExprError(`only one comparison is allowed (position ${this.peek()[2] + 1})`);
      return this.node(['cmp', op, left, right]);
    }
    return left;
  }
  sum(depth) {
    let left = this.product(depth);
    while (this.isOp('+', '-')) { const op = this.take()[1]; left = this.node(['bin', op, left, this.product(depth)]); }
    return left;
  }
  product(depth) {
    let left = this.unary(depth);
    while (this.isOp('*', '/')) { const op = this.take()[1]; left = this.node(['bin', op, left, this.unary(depth)]); }
    return left;
  }
  unary(depth) {
    if (this.isOp('-', '+')) {
      const op = this.take()[1];
      const inner = this.unary(this.nest(depth + 1));
      return op === '-' ? this.node(['neg', inner]) : inner;
    }
    return this.power(depth);
  }
  power(depth) {
    const base = this.primary(depth);
    if (this.isOp('^')) { this.take(); return this.node(['bin', '^', base, this.unary(this.nest(depth + 1))]); }
    return base;
  }
  primary(depth) {
    const [kind, value, pos] = this.take();
    if (kind === 'num') return this.node(['num', value]);
    if (kind === 'name') {
      if (this.isOp('(')) {
        if (!has(FUNCTIONS, value)) throw new ExprError(`'${value}' at position ${pos + 1} is not a function of the language`);
        this.take();
        const inner = this.nest(depth + 1);
        const args = [this.expr(inner)];
        while (this.isOp(',')) { this.take(); args.push(this.expr(inner)); }
        if (!this.isOp(')')) throw new ExprError(`expected ')' at position ${this.peek()[2] + 1}`);
        this.take();
        const [low, high] = FUNCTIONS[value];
        if (args.length < low || args.length > high) {
          throw new ExprError(`${value} takes ${low === high ? low : `${low} to ${high}`} argument(s)`);
        }
        return this.node(['call', value, args]);
      }
      if (has(FUNCTIONS, value)) throw new ExprError(`the function ${value} at position ${pos + 1} needs its arguments in brackets`);
      if (!has(CONSTANTS, value) && this.names && !this.names.has(value)) {
        throw new ExprError(`unknown name '${value}' at position ${pos + 1}`);
      }
      return this.node(['var', value]);
    }
    if (kind === 'op' && value === '(') {
      const inner = this.expr(this.nest(depth + 1));
      if (!this.isOp(')')) throw new ExprError(`expected ')' at position ${this.peek()[2] + 1}`);
      this.take();
      return inner;
    }
    if (kind === 'end') throw new ExprError('the expression ends too soon');
    throw new ExprError(`unexpected '${value}' at position ${pos + 1}`);
  }
}

/** The tree of `src`; with `names` (an iterable), every name must be one of them or a constant. */
export function parse(src, names) {
  const p = new Parser(src, names ? new Set(names) : null);
  if (p.peek()[0] === 'end') throw new ExprError('an empty expression');
  const tree = p.expr(0);
  const tok = p.peek();
  if (tok[0] !== 'end') throw new ExprError(`unexpected '${tok[1]}' at position ${tok[2] + 1}`);
  return tree;
}

export function namesIn(tree) {
  const out = new Set();
  const stack = [tree];
  while (stack.length) {
    const node = stack.pop();
    if (node[0] === 'var') out.add(node[1]);
    else if (node[0] === 'neg') stack.push(node[1]);
    else if (node[0] === 'bin' || node[0] === 'cmp') stack.push(node[2], node[3]);
    else if (node[0] === 'call') stack.push(...node[2]);
  }
  for (const c of Object.keys(CONSTANTS)) out.delete(c);
  return out;
}

/** The value of a parsed tree; `env` is a Map from names to numbers. */
export function evaluate(tree, env) {
  switch (tree[0]) {
    case 'num': return tree[1];
    case 'var':
      if (env.has(tree[1])) return env.get(tree[1]);
      if (has(CONSTANTS, tree[1])) return CONSTANTS[tree[1]];
      throw new ExprError(`no value for '${tree[1]}'`);
    case 'neg': return -evaluate(tree[1], env);
    case 'bin': {
      const a = evaluate(tree[2], env);
      const b = evaluate(tree[3], env);
      switch (tree[1]) {
        case '+': return a + b;
        case '-': return a - b;
        case '*': return a * b;
        case '/': return a / b;
        default: return a ** b;
      }
    }
    case 'cmp': {
      const a = evaluate(tree[2], env);
      const b = evaluate(tree[3], env);
      switch (tree[1]) {
        case '<': return a < b ? 1 : 0;
        case '<=': return a <= b ? 1 : 0;
        case '>': return a > b ? 1 : 0;
        case '>=': return a >= b ? 1 : 0;
        case '==': return a === b ? 1 : 0;
        default: return a !== b ? 1 : 0;
      }
    }
    default: break;
  }
  const fn = tree[1];
  const args = tree[2];
  if (fn === 'if') {
    const c = evaluate(args[0], env);
    return evaluate(c !== 0 && !Number.isNaN(c) ? args[1] : args[2], env);
  }
  const v = args.map((a) => evaluate(a, env));
  switch (fn) {
    case 'min': return Math.min(...v);
    case 'max': return Math.max(...v);
    case 'clamp': return Math.min(Math.max(v[0], v[1]), v[2]);
    case 'sin': return Math.sin(v[0]);
    case 'cos': return Math.cos(v[0]);
    case 'tan': return Math.tan(v[0]);
    case 'exp': return Math.exp(v[0]);
    case 'ln': return Math.log(v[0]);
    case 'log10': return Math.log10(v[0]);
    case 'sqrt': return Math.sqrt(v[0]);
    case 'abs': return Math.abs(v[0]);
    default: throw new ExprError(`unknown function '${fn}'`);
  }
}

// ------------------------------------------------------------------------------------------------------------
// The model (mirrors expr.model_problems and expr.evaluate_model)

const PLAIN_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function modelProblems(data) {
  const problems = [];
  const known = [];
  const claim = (name, what) => {
    if (typeof name !== 'string' || !PLAIN_NAME.test(name) || name.length > LIMITS.name) problems.push(`${what} name is not a plain name`);
    else if (RESERVED.has(name)) problems.push(`${what} name '${name}' is reserved`);
    else if (known.includes(name)) problems.push(`the name '${name}' is used twice`);
    else known.push(name);
  };
  const params = Array.isArray(data.parameters) ? data.parameters : [];
  for (const p of params) {
    claim(p.name, 'a parameter');
    if (!(isNum(p.min) && isNum(p.max) && p.min < p.max)) problems.push(`parameter '${p.name}': min must be below max`);
    else if (!(isNum(p.step) && p.step > 0 && p.step <= p.max - p.min)) problems.push(`parameter '${p.name}': the step must be positive and fit the range`);
    else if (!(isNum(p.value) && p.value >= p.min && p.value <= p.max)) problems.push(`parameter '${p.name}': the starting value must lie in the range`);
  }
  const dyn = data.dynamics || null;
  const state = dyn && Array.isArray(dyn.state) ? dyn.state.map((s) => s.name) : [];
  const paramNames = [...known];
  const dynamic = new Set();
  for (const q of Array.isArray(data.quantities) ? data.quantities : []) {
    const scope = dyn ? [...known, ...state, 't', 'dt'] : [...known];
    try {
      const tree = parse(q.expr, scope);
      const used = namesIn(tree);
      if ([...used].some((n) => state.includes(n) || n === 't' || n === 'dt' || dynamic.has(n))) dynamic.add(q.name);
    } catch (error) {
      problems.push(`quantity '${q.name}': ${error.message}`);
    }
    claim(q.name, 'a quantity');
  }
  if (dyn) {
    for (const s of state.length ? dyn.state : []) claim(s.name, 'a state variable');
    for (const s of state.length ? dyn.state : []) {
      try { parse(s.initial, paramNames); } catch (error) { problems.push(`state '${s.name}' starting value: ${error.message}`); }
      try { parse(s.update, [...known, 't', 'dt']); } catch (error) { problems.push(`state '${s.name}' update: ${error.message}`); }
    }
    if (!(Number.isInteger(dyn.steps) && dyn.steps >= 1 && dyn.steps <= LIMITS.steps)) problems.push(`dynamics: steps must be a whole number from 1 to ${LIMITS.steps}`);
    if (!(isNum(dyn.dt) && dyn.dt > 0)) problems.push('dynamics: dt must be a positive number');
  }
  (Array.isArray(data.views) ? data.views : []).forEach((view, k) => {
    if (!['plot', 'table', 'bars'].includes(view.kind)) { problems.push(`view ${k + 1}: unknown kind`); return; }
    const items = view.kind === 'plot' ? (view.y || []) : (view.items || []);
    if (!items.length || (view.kind === 'plot' && !view.x)) problems.push(`view ${k + 1}: a plot needs x and y; a table or bars need items`);
    for (const name of items) if (!known.includes(name)) problems.push(`view ${k + 1}: unknown name '${name}'`);
    if (view.kind === 'plot') {
      if (view.x === 'time') {
        if (!dyn) problems.push(`view ${k + 1}: a plot over time needs dynamics`);
      } else if (paramNames.includes(view.x)) {
        if (items.some((n) => dynamic.has(n) || state.includes(n))) problems.push(`view ${k + 1}: a sweep can plot only quantities that do not change over time`);
        const p = params.find((x) => x.name === view.x);
        const lo = has(view, 'from') ? view.from : p.min;
        const hi = has(view, 'to') ? view.to : p.max;
        if (!(isNum(lo) && isNum(hi) && lo < hi)) problems.push(`view ${k + 1}: the sweep's range must rise`);
        const points = has(view, 'points') ? view.points : 101;
        if (!(Number.isInteger(points) && points >= 2 && points <= LIMITS.sweep)) problems.push(`view ${k + 1}: a sweep has 2 to ${LIMITS.sweep} points`);
      } else {
        problems.push(`view ${k + 1}: x must be 'time' or a parameter's name`);
      }
    }
  });
  return problems;
}

/** Run a model at the given parameter values (a Map; starting values for those not given). */
export function evaluateModel(data, values) {
  const params = new Map(data.parameters.map((p) => [p.name, values && values.has(p.name) ? Number(values.get(p.name)) : p.value]));
  const quantities = data.quantities || [];
  const trees = new Map(quantities.map((q) => [q.name, parse(q.expr)]));
  const dyn = data.dynamics || null;
  const stateNames = dyn ? dyn.state.map((s) => s.name) : [];
  const changing = new Set();
  for (const q of quantities) {
    if ([...namesIn(trees.get(q.name))].some((n) => stateNames.includes(n) || n === 't' || n === 'dt' || changing.has(n))) changing.add(q.name);
  }
  const env = new Map(params);
  for (const q of quantities) if (!changing.has(q.name)) env.set(q.name, evaluate(trees.get(q.name), env));
  const outValues = new Map(env);
  const series = new Map();
  const times = [];
  if (dyn) {
    const dt = dyn.dt;
    const init = new Map(dyn.state.map((s) => [s.name, parse(s.initial)]));
    const update = new Map(dyn.state.map((s) => [s.name, parse(s.update)]));
    const state = new Map(stateNames.map((n) => [n, evaluate(init.get(n), params)]));
    for (const n of stateNames) series.set(n, []);
    for (const q of quantities) if (changing.has(q.name)) series.set(q.name, []);
    for (let k = 0; k <= dyn.steps; k += 1) {
      const stepEnv = new Map(env);
      for (const [n, v] of state) stepEnv.set(n, v);
      stepEnv.set('t', k * dt);
      stepEnv.set('dt', dt);
      for (const q of quantities) if (changing.has(q.name)) stepEnv.set(q.name, evaluate(trees.get(q.name), stepEnv));
      times.push(k * dt);
      for (const [n, list] of series) list.push(stepEnv.get(n));
      if (k === dyn.steps) {
        for (const n of series.keys()) outValues.set(n, stepEnv.get(n));
        break;
      }
      for (const n of stateNames) {
        const v = evaluate(update.get(n), stepEnv);
        stepEnv.set(n, v);
        state.set(n, v);
      }
    }
  }
  const sweeps = [];
  for (const view of data.views || []) {
    if (view.kind !== 'plot' || view.x === 'time') continue;
    const p = data.parameters.find((x) => x.name === view.x);
    const lo = has(view, 'from') ? view.from : p.min;
    const hi = has(view, 'to') ? view.to : p.max;
    const n = has(view, 'points') ? view.points : 101;
    const xs = [];
    for (let k = 0; k < n; k += 1) xs.push(lo + (hi - lo) * k / (n - 1));
    const sweep = new Map([['x', xs]]);
    for (const name of view.y) sweep.set(name, []);
    for (const xv of xs) {
      const e = new Map(params);
      e.set(view.x, xv);
      for (const q of quantities) if (!changing.has(q.name)) e.set(q.name, evaluate(trees.get(q.name), e));
      for (const name of view.y) sweep.get(name).push(e.get(name));
    }
    sweeps.push(sweep);
  }
  return { values: outValues, times, series, sweeps };
}

// ------------------------------------------------------------------------------------------------------------
// Specification checks done again in the browser (the server checks first; this is the second, independent check)

const FORMS = ['explorable.model', 'explorable.steps', 'solid.view'];
const isText = (v, max) => typeof v === 'string' && v.length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f‪-‮⁦-⁩]/.test(v);

export function specProblems(spec) {
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) return ['the specification is not an object'];
  if (!FORMS.includes(spec.spec)) return ['this runtime does not show that form'];
  if (spec.version !== 1) return ['unknown specification version'];
  if (!isText(spec.title, 200) || !isText(spec.description, 2000)) return ['the title or description is not plain text'];
  const d = spec.data;
  if (!d || typeof d !== 'object' || Array.isArray(d)) return ['the specification has no data'];
  if (spec.spec === 'explorable.model') {
    const problems = [];
    if (!Array.isArray(d.parameters) || d.parameters.length < 1 || d.parameters.length > 12) problems.push('a model has 1 to 12 parameters');
    if (!Array.isArray(d.views) || d.views.length < 1 || d.views.length > 6) problems.push('a model has 1 to 6 views');
    for (const p of d.parameters || []) if (!isText(p.label, 80) || (p.unit !== undefined && !isText(p.unit, 24))) problems.push('a parameter label is not plain text');
    return problems.length ? problems : modelProblems(d);
  }
  if (spec.spec === 'explorable.steps') {
    const problems = [];
    const code = d.code || [];
    const arrays = d.arrays || [];
    if (!Array.isArray(code) || code.length > 60 || !code.every((l) => isText(l, 120))) problems.push('the code is at most 60 lines of plain text');
    if (!Array.isArray(arrays) || arrays.length < 1 || arrays.length > 4) problems.push('a trace shows 1 to 4 arrays');
    if (!Array.isArray(d.steps) || d.steps.length < 1 || d.steps.length > 1000) return [...problems, 'a trace has 1 to 1000 steps'];
    d.steps.forEach((s, k) => {
      if (!isText(s.note, 300)) problems.push(`step ${k + 1}: the note is not plain text`);
      if (s.line !== null && s.line !== undefined && !(Number.isInteger(s.line) && s.line >= 0 && s.line < code.length)) problems.push(`step ${k + 1}: no such line`);
      if (!Array.isArray(s.arrays) || s.arrays.length !== arrays.length) { problems.push(`step ${k + 1}: one list per array`); return; }
      s.arrays.forEach((list) => { if (!Array.isArray(list) || list.length > 64 || !list.every((v) => isNum(v) || isText(v, 16))) problems.push(`step ${k + 1}: an array holds at most 64 numbers or short words`); });
      for (const p of s.pointers || []) if (!(Number.isInteger(p.array) && p.array >= 0 && p.array < arrays.length && (p.index === null || (Number.isInteger(p.index) && p.index >= 0 && p.index < (s.arrays[p.array] || []).length)))) problems.push(`step ${k + 1}: a pointer points outside its array`);
      for (const m of s.marks || []) if (!(Number.isInteger(m.array) && m.array >= 0 && m.array < arrays.length && Number.isInteger(m.index) && m.index >= 0 && m.index < (s.arrays[m.array] || []).length)) problems.push(`step ${k + 1}: a mark lies outside its array`);
    });
    return problems.slice(0, 20);
  }
  const v = d.vertices;
  const f = d.faces;
  if (!Array.isArray(v) || v.length < 3 || v.length > 20000 || !v.every((p) => Array.isArray(p) && p.length === 3 && p.every(isNum))) return ['a solid has 3 to 20000 vertices of three numbers'];
  if (!Array.isArray(f) || f.length < 1 || f.length > 40000 || !f.every((t) => Array.isArray(t) && t.length === 3 && t.every((i) => Number.isInteger(i) && i >= 0 && i < v.length))) return ['a solid has 1 to 40000 triangles of three vertex numbers'];
  return [];
}

// ------------------------------------------------------------------------------------------------------------
// Numbers in words people read

const SUPER = { '-': '⁻', 0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹' };

export function fmt(v) {
  if (Number.isNaN(v)) return 'undefined';
  if (v === Infinity) return 'infinite';
  if (v === -Infinity) return 'minus infinite';
  if (v === 0) return '0';
  const a = Math.abs(v);
  if (a >= 1e-4 && a < 1e6) {
    let s = v.toFixed(Math.min(10, Math.max(0, 3 - Math.floor(Math.log10(a)))));
    if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '');
    return s === '-0' ? '0' : s.replace('-', '−');
  }
  const [m, e] = v.toExponential(3).split('e');
  const mant = m.includes('.') ? m.replace(/0+$/, '').replace(/\.$/, '') : m;
  return `${mant.replace('-', '−')} × 10${String(Number(e)).split('').map((c) => SUPER[c]).join('')}`;
}

const withUnit = (v, unit) => (unit ? `${fmt(v)} ${unit}` : fmt(v));

// ------------------------------------------------------------------------------------------------------------
// Building the view (text nodes and attributes only)

function h(doc, tag, attrs, ...children) {
  const node = doc.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined && v !== false) node.setAttribute(k, String(v));
  for (const c of children) if (c !== null && c !== undefined) node.append(typeof c === 'string' ? doc.createTextNode(c) : c);
  return node;
}

function s(doc, tag, attrs, text) {
  const node = doc.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  if (text !== undefined) node.append(doc.createTextNode(text));
  return node;
}

function niceStep(span, count) {
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const r = raw / mag;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 5 ? 5 : 10) * mag;
}

/** A linear scale and its ticks: widened to whole ticks (a value axis), or kept to the data's own range with the
 * ticks that fall inside it (`exact`, for the axis of time or of a swept setting). */
export function ticks(lo, hi, count, exact = false) {
  if (!(Number.isFinite(lo) && Number.isFinite(hi))) { lo = 0; hi = 1; }
  if (lo === hi) { const pad = lo === 0 ? 1 : Math.abs(lo) * 0.1; lo -= pad; hi += pad; }
  const step = niceStep(hi - lo, count);
  const start = exact ? Math.ceil(lo / step - 1e-9) * step : Math.floor(lo / step) * step;
  const end = exact ? hi : Math.ceil(hi / step) * step;
  const list = [];
  for (let k = 0; start + k * step <= end + step * 1e-9 && k < 50; k += 1) list.push(Number((start + k * step).toPrecision(12)));
  return { lo: exact ? lo : start, hi: end, list };
}

const DASHES = [null, '8 5', '2 4', '12 4 3 4'];
const MARKS = ['circle', 'square', 'triangle', 'diamond'];

function marker(doc, kind, x, y) {
  const r = 4;
  if (kind === 'circle') return s(doc, 'circle', { class: 'nsf-xp-marker', cx: x, cy: y, r });
  if (kind === 'square') return s(doc, 'rect', { class: 'nsf-xp-marker', x: x - r, y: y - r, width: 2 * r, height: 2 * r });
  const pts = kind === 'triangle' ? [[x, y - r - 1], [x + r + 1, y + r], [x - r - 1, y + r]] : [[x, y - r - 1], [x + r + 1, y], [x, y + r + 1], [x - r - 1, y]];
  return s(doc, 'polygon', { class: 'nsf-xp-marker', points: pts.map((p) => p.map((c) => c.toFixed(1)).join(',')).join(' ') });
}

function drawPlot(doc, width, plot) {
  const W = Math.max(280, Math.round(width));
  const H = Math.round(Math.min(380, Math.max(220, W * 0.55)));
  const m = { l: 64, r: 16, t: 14, b: 46 };
  const finite = (list) => list.filter(Number.isFinite);
  const allY = plot.series.flatMap((ser) => finite(ser.values));
  const ty = ticks(allY.length ? Math.min(...allY) : 0, allY.length ? Math.max(...allY) : 1, 5);
  const tx = ticks(plot.xs[0], plot.xs[plot.xs.length - 1], Math.max(3, Math.floor(W / 110)), true);
  const X = (v) => m.l + (v - tx.lo) / (tx.hi - tx.lo) * (W - m.l - m.r);
  const Y = (v) => H - m.b - (v - ty.lo) / (ty.hi - ty.lo) * (H - m.t - m.b);
  const svg = s(doc, 'svg', { class: 'nsf-xp-chart', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': plot.summary });
  const grid = s(doc, 'g', { class: 'nsf-xp-grid' });
  const axisY = s(doc, 'g', { class: 'axis y' });
  for (const v of ty.list) {
    grid.append(s(doc, 'line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    const tick = s(doc, 'g', { class: 'tick', transform: `translate(${m.l},${Y(v).toFixed(2)})` });
    tick.append(s(doc, 'text', { x: -8, dy: '0.32em', 'text-anchor': 'end' }, fmt(v)));
    axisY.append(tick);
  }
  const axisX = s(doc, 'g', { class: 'axis x' });
  for (const v of tx.list) {
    grid.append(s(doc, 'line', { x1: X(v), x2: X(v), y1: m.t, y2: H - m.b }));
    const tick = s(doc, 'g', { class: 'tick', transform: `translate(${X(v).toFixed(2)},${H - m.b})` });
    tick.append(s(doc, 'line', { y2: 5 }));
    tick.append(s(doc, 'text', { y: 18, 'text-anchor': 'middle' }, fmt(v)));
    axisX.append(tick);
  }
  svg.append(grid, s(doc, 'line', { class: 'nsf-xp-frame', x1: m.l, x2: m.l, y1: m.t, y2: H - m.b }),
    s(doc, 'line', { class: 'nsf-xp-frame', x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b }), axisX, axisY);
  svg.append(s(doc, 'text', { class: 'nsf-xp-axis-label', x: (m.l + W - m.r) / 2, y: H - 8, 'text-anchor': 'middle' }, plot.xLabel));
  svg.append(s(doc, 'text', { class: 'nsf-xp-axis-label', transform: `translate(14,${(m.t + H - m.b) / 2}) rotate(-90)`, 'text-anchor': 'middle' }, plot.yLabel));
  const marks = s(doc, 'g', { class: 'plot' });
  plot.series.forEach((ser, i) => {
    const g = s(doc, 'g', { class: `series s${i}`, 'data-name': ser.name });
    let run = [];
    const flush = () => {
      if (run.length > 1) g.append(s(doc, 'polyline', { class: 'series', points: run.join(' '), 'stroke-dasharray': DASHES[i % 4] }));
      run = [];
    };
    ser.values.forEach((v, k) => {
      if (Number.isFinite(v)) {
        const y = Y(v);
        run.push(`${X(plot.xs[k]).toFixed(2)},${y.toFixed(2)}`);
      } else flush();
    });
    flush();
    const every = Math.max(1, Math.round(ser.values.length / 8));
    for (let k = Math.floor(every / 2) + Math.round(i * every / 4) % every; k < ser.values.length; k += every) {
      if (Number.isFinite(ser.values[k])) g.append(marker(doc, MARKS[i % 4], X(plot.xs[k]), Y(ser.values[k])));
    }
    marks.append(g);
  });
  svg.append(marks);
  return svg;
}

function legend(doc, series) {
  const list = h(doc, 'ul', { class: 'nsf-xp-legend' });
  series.forEach((ser, i) => {
    const key = s(doc, 'svg', { width: 40, height: 14, viewBox: '0 0 40 14', 'aria-hidden': 'true', focusable: 'false' });
    key.append(s(doc, 'line', { class: 'series', x1: 2, x2: 38, y1: 7, y2: 7, 'stroke-dasharray': DASHES[i % 4] }), marker(doc, MARKS[i % 4], 20, 7));
    list.append(h(doc, 'li', {}, key, ser.label));
  });
  return list;
}

function valuesTable(doc, caption, xLabel, xs, series, xIsIndex) {
  const n = xs.length;
  const rows = n <= 101 ? [...Array(n).keys()] : [...Array(101).keys()].map((k) => Math.round(k * (n - 1) / 100));
  const table = h(doc, 'table', { class: 'nsf-xp-values' }, h(doc, 'caption', {}, caption));
  const head = h(doc, 'tr', {}, h(doc, 'th', { scope: 'col' }, xLabel), ...series.map((ser) => h(doc, 'th', { scope: 'col' }, ser.label)));
  const body = h(doc, 'tbody');
  for (const k of rows) {
    body.append(h(doc, 'tr', { 'data-index': k, 'data-x': String(xs[k]) },
      h(doc, 'th', { scope: 'row' }, fmt(xs[k])),
      ...series.map((ser) => h(doc, 'td', { 'data-name': ser.name, 'data-value': String(ser.values[k]) }, fmt(ser.values[k])))));
  }
  table.append(h(doc, 'thead', {}, head), body);
  if (xIsIndex) table.setAttribute('data-x-kind', 'time');
  return h(doc, 'details', { class: 'nsf-xp-details' }, h(doc, 'summary', {}, 'Show the plotted values as a table'), table);
}

function drawBars(doc, width, items) {
  const W = Math.max(280, Math.round(width));
  const row = 30;
  const H = 24 + row * items.length;
  const labelW = Math.min(180, Math.round(W * 0.34));
  const vals = items.map((it) => it.value).filter(Number.isFinite);
  const t = ticks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
  const X = (v) => labelW + (v - t.lo) / (t.hi - t.lo) * (W - labelW - 70);
  const svg = s(doc, 'svg', { class: 'nsf-xp-chart', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': items.map((it) => `${it.label}: ${withUnit(it.value, it.unit)}`).join('; ') });
  svg.append(s(doc, 'line', { class: 'nsf-xp-frame', x1: X(0), x2: X(0), y1: 8, y2: H - 8 }));
  const g = s(doc, 'g', { class: 'plot' });
  items.forEach((it, k) => {
    const y = 12 + k * row;
    g.append(s(doc, 'text', { x: labelW - 8, y: y + 13, 'text-anchor': 'end' }, it.label));
    if (Number.isFinite(it.value)) {
      const x0 = X(Math.min(0, it.value));
      const x1 = X(Math.max(0, it.value));
      g.append(s(doc, 'rect', { class: 'mark nsf-xp-bar', x: x0.toFixed(2), y, width: Math.max(1, x1 - x0).toFixed(2), height: row - 10 }));
      g.append(s(doc, 'text', { x: (x1 + 6).toFixed(2), y: y + 13 }, withUnit(it.value, it.unit)));
    } else {
      g.append(s(doc, 'text', { x: X(0) + 6, y: y + 13 }, withUnit(it.value, it.unit)));
    }
  });
  svg.append(g);
  return svg;
}

// ------------------------------------------------------------------------------------------------------------
// The three views

function header(doc, spec, level) {
  const tag = `h${Math.min(6, Math.max(1, level || 2))}`;
  return [h(doc, tag, { class: 'nsf-xp-title' }, spec.title),
    spec.description ? h(doc, 'p', { class: 'nsf-xp-desc' }, spec.description) : null,
    h(doc, 'p', { class: 'nsf-xp-made' }, 'Made by NeuroSciFi from a specification: a model to explore, not evidence.')];
}

function sub(level) { return `h${Math.min(6, Math.max(2, (level || 2) + 1))}`; }

function modelView(doc, wrap, spec, ctx) {
  const d = spec.data;
  const values = new Map(d.parameters.map((p) => [p.name, p.value]));
  const labels = new Map();
  const units = new Map();
  for (const p of d.parameters) { labels.set(p.name, p.label); units.set(p.name, p.unit || ''); }
  for (const q of d.quantities || []) { labels.set(q.name, q.label || q.name); units.set(q.name, q.unit || ''); }
  for (const st of (d.dynamics && d.dynamics.state) || []) { labels.set(st.name, st.label || st.name); units.set(st.name, st.unit || ''); }
  const unitOf = (n) => units.get(n) || '';
  const named = (n) => (unitOf(n) ? `${labels.get(n)} (${unitOf(n)})` : labels.get(n));
  const timeLabel = d.dynamics ? `${d.dynamics.time_label || 'Time'}${d.dynamics.time_unit ? ` (${d.dynamics.time_unit})` : ''}` : '';

  const controls = h(doc, 'fieldset', { class: 'nsf-xp-controls' }, h(doc, 'legend', {}, 'Settings'));
  const inputs = [];
  for (const p of d.parameters) {
    const input = h(doc, 'input', { type: 'range', min: p.min, max: p.max, step: p.step, value: p.value, 'aria-valuetext': withUnit(p.value, p.unit) });
    const out = h(doc, 'output', { class: 'nsf-xp-out', 'aria-hidden': 'true' }, withUnit(p.value, p.unit));
    const label = h(doc, 'label', { class: 'nsf-xp-param' }, h(doc, 'span', { class: 'nsf-xp-param-name' }, p.label, out), input);
    input.addEventListener('input', () => {
      const v = Number(input.value);
      values.set(p.name, v);
      out.textContent = withUnit(v, p.unit);
      input.setAttribute('aria-valuetext', withUnit(v, p.unit));
      ctx.schedule(p.name);
    });
    inputs.push([p, input, out]);
    controls.append(label);
  }
  const reset = h(doc, 'button', { type: 'button', class: 'nsf-xp-reset' }, 'Back to the starting values');
  reset.addEventListener('click', () => {
    for (const [p, input, out] of inputs) {
      input.value = String(p.value);
      values.set(p.name, p.value);
      out.textContent = withUnit(p.value, p.unit);
      input.setAttribute('aria-valuetext', withUnit(p.value, p.unit));
    }
    ctx.schedule(null);
  });
  controls.append(reset);
  const status = h(doc, 'p', { class: 'nsf-xp-status', role: 'status', 'aria-live': 'polite' });
  const views = h(doc, 'div', { class: 'nsf-xp-views' });
  wrap.append(controls, status, views);

  const slots = d.views.map((view) => {
    const section = h(doc, 'section', { class: `nsf-xp-view nsf-xp-${view.kind}` });
    const title = h(doc, sub(ctx.level), {}, view.title || (view.kind === 'table' ? 'Values' : view.kind === 'bars' ? 'Compared' : 'Plot'));
    const body = h(doc, 'div', { class: 'nsf-xp-view-body' });
    section.append(title, body);
    views.append(section);
    return { view, body };
  });

  function render() {
    const result = evaluateModel(d, values);
    const width = Math.min(900, (views.clientWidth || 640));
    let sweepAt = 0;
    for (const { view, body } of slots) {
      body.replaceChildren();
      if (view.kind === 'table') {
        const end = d.dynamics ? ` at the end (${(d.dynamics.time_label || 'time').toLowerCase()} ${fmt(d.dynamics.steps * d.dynamics.dt)}${d.dynamics.time_unit ? ` ${d.dynamics.time_unit}` : ''})` : '';
        const table = h(doc, 'table', { class: 'nsf-xp-readout' }, h(doc, 'caption', { class: 'nsf-xp-vh' }, view.title || 'Values'));
        const tbody = h(doc, 'tbody');
        for (const n of view.items) {
          const v = result.values.get(n);
          const changes = result.series.has(n);
          tbody.append(h(doc, 'tr', {}, h(doc, 'th', { scope: 'row' }, labels.get(n) + (changes ? end : '')),
            h(doc, 'td', { 'data-name': n, 'data-value': String(v) }, withUnit(v, unitOf(n)))));
        }
        table.append(tbody);
        body.append(table);
      } else if (view.kind === 'bars') {
        const items = view.items.map((n) => ({ label: labels.get(n), value: result.values.get(n), unit: unitOf(n) }));
        body.append(drawBars(doc, width, items));
      } else {
        const overTime = view.x === 'time';
        const xs = overTime ? result.times : result.sweeps[sweepAt].get('x');
        const series = view.y.map((n) => ({ name: n, label: named(n),
          values: overTime ? (result.series.get(n) || xs.map(() => result.values.get(n))) : result.sweeps[sweepAt].get(n) }));
        if (!overTime) sweepAt += 1;
        const xLabel = overTime ? timeLabel : named(view.x);
        const yLabel = view.y_label || (view.y.length === 1 ? named(view.y[0]) : 'Value');
        const span = series.map((ser) => {
          const f = ser.values.filter(Number.isFinite);
          return f.length ? `${ser.label} from ${fmt(Math.min(...f))} to ${fmt(Math.max(...f))}` : `${ser.label} undefined`;
        }).join('; ');
        const summary = `${view.title || 'Plot'}: ${xLabel} from ${fmt(xs[0])} to ${fmt(xs[xs.length - 1])}; ${span}.`;
        body.append(legend(doc, series), drawPlot(doc, width, { xs, series, xLabel, yLabel, summary }),
          valuesTable(doc, view.title || 'Plotted values', xLabel, xs, series, overTime));
      }
    }
    return result;
  }
  return {
    render,
    announce(name, result) {
      const parts = [];
      if (name) parts.push(`${labels.get(name)} set to ${withUnit(values.get(name), unitOf(name))}.`);
      else parts.push('Back to the starting values.');
      const first = d.views.find((v) => v.kind === 'table');
      if (first && first.items.length) parts.push(`${labels.get(first.items[0])}: ${withUnit(result.values.get(first.items[0]), unitOf(first.items[0]))}.`);
      status.textContent = parts.join(' ');
    },
  };
}

function stepsView(doc, wrap, spec, ctx) {
  const d = spec.data;
  const steps = d.steps;
  let at = 0;
  const bar = h(doc, 'div', { class: 'nsf-xp-stepbar', role: 'group', 'aria-label': 'Move through the steps' });
  const button = (text, label) => h(doc, 'button', { type: 'button', 'aria-label': label }, text);
  const first = button('First', 'First step');
  const back = button('Back', 'Previous step');
  const play = button('Play', 'Play the steps');
  const next = button('Next', 'Next step');
  const last = button('Last', 'Last step');
  const slider = h(doc, 'input', { type: 'range', min: 1, max: steps.length, step: 1, value: 1 });
  const counter = h(doc, 'output', { class: 'nsf-xp-out', 'aria-hidden': 'true' });
  const sliderLabel = h(doc, 'label', { class: 'nsf-xp-param nsf-xp-stepslider' }, h(doc, 'span', { class: 'nsf-xp-param-name' }, 'Step', counter), slider);
  bar.append(first, back, play, next, last);
  const status = h(doc, 'p', { class: 'nsf-xp-status nsf-xp-note', role: 'status', 'aria-live': 'polite' });
  const picture = h(doc, 'div', { class: 'nsf-xp-arrays' });
  const columns = h(doc, 'div', { class: 'nsf-xp-columns' });
  const code = h(doc, 'ol', { class: 'nsf-xp-code', 'aria-label': 'The steps of the method' });
  const lines = (d.code || []).map((text) => {
    const li = h(doc, 'li', {}, h(doc, 'span', { class: 'nsf-xp-here', 'aria-hidden': 'true' }), h(doc, 'code', {}, text));
    code.append(li);
    return li;
  });
  const vars = h(doc, 'table', { class: 'nsf-xp-readout nsf-xp-vars' }, h(doc, 'caption', { class: 'nsf-xp-vh' }, 'Values at this step'));
  const varsBody = h(doc, 'tbody');
  vars.append(varsBody);
  if (lines.length) columns.append(h(doc, 'div', {}, h(doc, sub(ctx.level), {}, 'Method'), code));
  columns.append(h(doc, 'div', {}, h(doc, sub(ctx.level), {}, 'Variables'), vars));
  wrap.append(bar, sliderLabel, status, picture, columns);

  function drawArrays(step, width) {
    picture.replaceChildren();
    d.arrays.forEach((arr, ai) => {
      const list = step.arrays[ai];
      const n = Math.max(1, list.length);
      const W = Math.max(280, Math.round(width));
      const cell = Math.max(26, Math.min(56, Math.floor((W - 16) / n)));
      const pointers = (step.pointers || []).filter((p) => p.array === ai && p.index !== null);
      const rowsBelow = new Map();
      for (const p of pointers) rowsBelow.set(p.index, (rowsBelow.get(p.index) || []).concat(p.name));
      const depth = Math.max(0, ...[...rowsBelow.values()].map((v) => v.length));
      const H = 22 + cell + 18 + depth * 16 + 6;
      const marks = new Map((step.marks || []).filter((m) => m.array === ai).map((m) => [m.index, m.kind || 'active']));
      const words = list.map((v, k) => {
        const tags = [];
        if (marks.has(k)) tags.push(marks.get(k) === 'out' ? 'set aside' : marks.get(k));
        if (rowsBelow.has(k)) tags.push(`pointed to by ${rowsBelow.get(k).join(' and ')}`);
        return `${k}: ${typeof v === 'number' ? fmt(v) : v}${tags.length ? ` (${tags.join(', ')})` : ''}`;
      });
      const svg = s(doc, 'svg', { class: 'nsf-xp-chart nsf-xp-array', width: Math.min(W, 16 + cell * n), height: H,
        viewBox: `0 0 ${Math.min(W, 16 + cell * n)} ${H}`, role: 'img', 'aria-label': `${arr.label || arr.name}: ${words.join('; ')}` });
      svg.append(s(doc, 'text', { class: 'nsf-xp-axis-label', x: 8, y: 14 }, arr.label || arr.name));
      list.forEach((v, k) => {
        const x = 8 + k * cell;
        const kind = marks.get(k);
        const g = s(doc, 'g', { class: `nsf-xp-cell${kind ? ` ${kind}` : ''}` });
        g.append(s(doc, 'rect', { x: x + 1, y: 22, width: cell - 2, height: cell - 2, rx: 4 }));
        g.append(s(doc, 'text', { class: 'nsf-xp-value', x: x + cell / 2, y: 22 + cell / 2, dy: '0.35em', 'text-anchor': 'middle' }, typeof v === 'number' ? fmt(v) : v));
        g.append(s(doc, 'text', { class: 'nsf-xp-index', x: x + cell / 2, y: 22 + cell + 12, 'text-anchor': 'middle' }, String(k)));
        (rowsBelow.get(k) || []).forEach((name, r) => {
          g.append(s(doc, 'text', { class: 'nsf-xp-pointer', x: x + cell / 2, y: 22 + cell + 28 + r * 16, 'text-anchor': 'middle' }, `↑ ${name}`));
        });
        svg.append(g);
      });
      picture.append(svg);
    });
  }

  function render() {
    const step = steps[at];
    slider.value = String(at + 1);
    slider.setAttribute('aria-valuetext', `Step ${at + 1} of ${steps.length}`);
    counter.textContent = `${at + 1} of ${steps.length}`;
    first.disabled = back.disabled = at === 0;
    next.disabled = last.disabled = at === steps.length - 1;
    status.textContent = `Step ${at + 1} of ${steps.length}: ${step.note}`;
    lines.forEach((li, k) => {
      const here = step.line === k;
      li.classList.toggle('nsf-xp-current', here);
      if (here) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
      li.firstChild.textContent = here ? '▶' : '';
    });
    varsBody.replaceChildren(...(step.vars || []).map((v) => h(doc, 'tr', {}, h(doc, 'th', { scope: 'row' }, v.name),
      h(doc, 'td', { 'data-name': v.name, 'data-value': String(v.value) }, typeof v.value === 'number' ? fmt(v.value) : String(v.value ?? '—')))));
    drawArrays(step, Math.min(900, picture.clientWidth || 640));
  }
  const go = (k) => { at = Math.max(0, Math.min(steps.length - 1, k)); render(); };
  const stop = () => { ctx.clearTimer(); play.textContent = 'Play'; play.setAttribute('aria-label', 'Play the steps'); };
  first.addEventListener('click', () => { stop(); go(0); });
  back.addEventListener('click', () => { stop(); go(at - 1); });
  next.addEventListener('click', () => { stop(); go(at + 1); });
  last.addEventListener('click', () => { stop(); go(steps.length - 1); });
  slider.addEventListener('input', () => { stop(); go(Number(slider.value) - 1); });
  play.addEventListener('click', () => {
    if (ctx.hasTimer()) { stop(); return; }
    if (at === steps.length - 1) go(0);
    play.textContent = 'Pause';
    play.setAttribute('aria-label', 'Pause');
    ctx.setTimer(() => { if (at >= steps.length - 1) stop(); else go(at + 1); }, ctx.reducedMotion() ? 1600 : 900);
  });
  return { render, announce() {} };
}

function solidView(doc, wrap, spec, ctx) {
  const d = spec.data;
  const V = d.vertices;
  const F = d.faces;
  const unit = d.unit && d.unit !== 'unitless' ? ` ${d.unit}` : '';
  const lo = [0, 1, 2].map((k) => Math.min(...V.map((p) => p[k])));
  const hi = [0, 1, 2].map((k) => Math.max(...V.map((p) => p[k])));
  const centre = lo.map((v, k) => (v + hi[k]) / 2);
  const radius = Math.max(1e-12, Math.sqrt(V.reduce((m, p) => Math.max(m, (p[0] - centre[0]) ** 2 + (p[1] - centre[1]) ** 2 + (p[2] - centre[2]) ** 2), 0)));
  const size = hi.map((v, k) => fmt(v - lo[k])).join(' by ');
  const defaults = { yaw: 35, pitch: 25 };
  let view = { ...defaults };
  const words = `${F.length} triangles, ${size}${unit} (width by depth by height).`;
  const described = h(doc, 'p', { class: 'nsf-xp-desc' }, `A solid of ${words} Use the arrow keys on the picture, or the buttons, to turn it.`);
  const canvas = h(doc, 'canvas', { class: 'nsf-xp-canvas', tabindex: '0', role: 'img' });
  const bar = h(doc, 'div', { class: 'nsf-xp-stepbar', role: 'group', 'aria-label': 'Turn the solid' });
  const turn = (text, dy, dp) => {
    const b = h(doc, 'button', { type: 'button' }, text);
    b.addEventListener('click', () => { view.yaw += dy; view.pitch = Math.max(-89, Math.min(89, view.pitch + dp)); draw(); });
    return b;
  };
  const resetBtn = h(doc, 'button', { type: 'button' }, 'Reset view');
  resetBtn.addEventListener('click', () => { view = { ...defaults }; draw(); });
  const edges = h(doc, 'input', { type: 'checkbox' });
  edges.addEventListener('change', () => draw());
  bar.append(turn('Turn left', -15, 0), turn('Turn right', 15, 0), turn('Tilt up', 0, 10), turn('Tilt down', 0, -10), resetBtn,
    h(doc, 'label', { class: 'nsf-xp-check' }, edges, 'Edges only'));
  const status = h(doc, 'p', { class: 'nsf-xp-status', role: 'status', 'aria-live': 'polite' });
  wrap.append(described, canvas, bar, status);
  canvas.addEventListener('keydown', (ev) => {
    const step = { ArrowLeft: [-15, 0], ArrowRight: [15, 0], ArrowUp: [0, 10], ArrowDown: [0, -10] }[ev.key];
    if (!step) return;
    ev.preventDefault();
    view.yaw += step[0];
    view.pitch = Math.max(-89, Math.min(89, view.pitch + step[1]));
    draw();
  });
  let drag = null;
  canvas.addEventListener('pointerdown', (ev) => { drag = [ev.clientX, ev.clientY]; canvas.setPointerCapture(ev.pointerId); });
  canvas.addEventListener('pointermove', (ev) => {
    if (!drag) return;
    view.yaw += (ev.clientX - drag[0]) * 0.5;
    view.pitch = Math.max(-89, Math.min(89, view.pitch + (ev.clientY - drag[1]) * 0.5));
    drag = [ev.clientX, ev.clientY];
    draw();
  });
  const end = () => { drag = null; };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);

  function colour(name, fallback) {
    const probe = ctx.root.ownerDocument.defaultView;
    const value = probe && probe.getComputedStyle ? probe.getComputedStyle(wrap).getPropertyValue(name).trim() : '';
    const m = /^#([0-9a-f]{6})$/i.exec(value);
    return m ? [0, 2, 4].map((k) => parseInt(m[1].slice(k, k + 2), 16)) : fallback;
  }

  function draw() {
    const width = Math.max(280, Math.min(720, wrap.clientWidth || 480));
    const height = Math.round(width * 0.75);
    const ratio = (doc.defaultView && doc.defaultView.devicePixelRatio) || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const yaw = view.yaw * Math.PI / 180;
    const pitch = view.pitch * Math.PI / 180;
    canvas.setAttribute('aria-label', `A solid of ${words} Seen turned ${Math.round(((view.yaw % 360) + 360) % 360)} degrees and tilted ${Math.round(view.pitch)} degrees.`);
    status.textContent = `Turned ${Math.round(((view.yaw % 360) + 360) % 360)} degrees, tilted ${Math.round(view.pitch)} degrees.`;
    const g = canvas.getContext && canvas.getContext('2d');
    if (!g) return;
    const ink = colour('--xp-ink', [20, 20, 20]);
    const bg = colour('--xp-bg', [255, 255, 255]);
    const cy = Math.cos(yaw); const sy = Math.sin(yaw); const cp = Math.cos(pitch); const sp = Math.sin(pitch);
    const scale = Math.min(width, height) * 0.42 / radius;
    const P = V.map((p) => {
      const x = p[0] - centre[0]; const y = p[1] - centre[1]; const z = p[2] - centre[2];
      const x1 = cy * x - sy * y; const y1 = sy * x + cy * y;      // turn about the vertical (z) axis
      const depth = cp * y1 - sp * z; const up = sp * y1 + cp * z;   // tilt: look down onto the solid
      return [width / 2 + x1 * scale, height / 2 - up * scale, depth];
    });
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.fillStyle = `rgb(${bg.join(',')})`;
    g.fillRect(0, 0, width, height);
    const order = F.map((f, k) => [k, (P[f[0]][2] + P[f[1]][2] + P[f[2]][2]) / 3]).sort((a, b) => b[1] - a[1]);
    const light = [-0.4, -0.5, 0.77];
    const wire = edges.checked;
    g.lineWidth = 1;
    for (const [k] of order) {
      const [a, b, c] = F[k].map((i) => P[i]);
      const ux = b[0] - a[0]; const uy = b[1] - a[1]; const vx = c[0] - a[0]; const vy = c[1] - a[1];
      const facing = ux * vy - uy * vx;          // screen y points down: a face toward the viewer winds clockwise here
      if (!wire && facing >= 0) continue;
      g.beginPath();
      g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.lineTo(c[0], c[1]); g.closePath();
      if (wire) {
        g.strokeStyle = `rgba(${ink.join(',')},0.55)`;
        g.stroke();
        continue;
      }
      const ax = [V[F[k][1]][0] - V[F[k][0]][0], V[F[k][1]][1] - V[F[k][0]][1], V[F[k][1]][2] - V[F[k][0]][2]];
      const bx = [V[F[k][2]][0] - V[F[k][0]][0], V[F[k][2]][1] - V[F[k][0]][1], V[F[k][2]][2] - V[F[k][0]][2]];
      const n = [ax[1] * bx[2] - ax[2] * bx[1], ax[2] * bx[0] - ax[0] * bx[2], ax[0] * bx[1] - ax[1] * bx[0]];
      const len = Math.hypot(...n) || 1;
      const nx = (cy * n[0] - sy * n[1]) / len; const ny1 = (sy * n[0] + cy * n[1]) / len; const nz0 = n[2] / len;
      const toward = -(cp * ny1 - sp * nz0); const up = sp * ny1 + cp * nz0;
      const lit = Math.max(0, nx * light[0] + up * -light[1] + toward * light[2]);
      const gray = Math.round(48 + 182 * (0.12 + 0.88 * lit));   // lit faces lighter, in either theme
      g.fillStyle = `rgb(${gray},${gray},${gray})`;
      g.strokeStyle = g.fillStyle;
      g.fill();
      g.stroke();
    }
  }
  return { render: draw, announce() {} };
}

// ------------------------------------------------------------------------------------------------------------
// Mounting

/**
 * Show `spec` inside `element`. Returns {unmount()}. Nothing outside `element` is touched; the element's own
 * children are kept aside and put back by unmount().
 */
export function mount(element, spec, options = {}) {
  const doc = element.ownerDocument;
  const win = doc.defaultView;
  const kept = [...element.childNodes];
  const wrap = h(doc, 'div', { class: 'nsf-xp' });
  let frame = 0;
  let timer = 0;
  let announceTimer = 0;
  let observer = null;
  let media = null;
  const problems = specProblems(spec);
  element.replaceChildren(wrap);
  if (problems.length) {
    wrap.append(h(doc, 'p', { class: 'nsf-xp-error', role: 'alert' }, `This could not be shown: ${problems[0]}.`));
    return { unmount() { element.replaceChildren(...kept); } };
  }
  const level = options.heading || 2;
  wrap.append(...header(doc, spec, level).filter(Boolean));
  const ctx = {
    root: element,
    level,
    reducedMotion: () => !!(win && win.matchMedia && win.matchMedia('(prefers-reduced-motion: reduce)').matches),
    setTimer(fn, ms) { if (win) timer = win.setInterval(fn, ms); },
    clearTimer() { if (timer && win) win.clearInterval(timer); timer = 0; },
    hasTimer: () => timer !== 0,
    schedule(name) {
      // name: the parameter just moved, null for a reset, undefined for a redraw (a new width) that says nothing
      if (!win) { const r = view.render(); if (name !== undefined) view.announce(name, r); return; }
      if (frame) win.cancelAnimationFrame(frame);
      frame = win.requestAnimationFrame(() => {
        frame = 0;
        const r = view.render();
        if (name === undefined) return;
        if (announceTimer) win.clearTimeout(announceTimer);
        announceTimer = win.setTimeout(() => { announceTimer = 0; view.announce(name, r); }, 500);
      });
    },
  };
  const build = spec.spec === 'explorable.model' ? modelView : spec.spec === 'explorable.steps' ? stepsView : solidView;
  const view = build(doc, wrap, spec, ctx);
  view.render();
  let lastWidth = wrap.clientWidth;
  if (win && win.ResizeObserver) {
    observer = new win.ResizeObserver(() => {
      if (wrap.clientWidth !== lastWidth) { lastWidth = wrap.clientWidth; ctx.schedule(undefined); }
    });
    observer.observe(wrap);
  }
  if (win && win.matchMedia && spec.spec === 'solid.view') {
    media = win.matchMedia('(prefers-color-scheme: dark)');
    media.addEventListener('change', view.render);
  }
  return {
    unmount() {
      ctx.clearTimer();
      if (frame && win) win.cancelAnimationFrame(frame);
      if (announceTimer && win) win.clearTimeout(announceTimer);
      if (observer) observer.disconnect();
      if (media) media.removeEventListener('change', view.render);
      element.replaceChildren(...kept);
    },
  };
}

// The self-contained document: it holds a root element marked for start-up and the specification as JSON data.
// A host page that imports this module has neither, and nothing happens until it calls mount() itself.
if (typeof document !== 'undefined') {
  const root = document.getElementById('nsf-xp-root');
  const data = document.getElementById('nsf-xp-spec');
  if (root && data && root.getAttribute('data-autoboot') === '1') {
    let spec = null;
    try { spec = JSON.parse(data.textContent); } catch (error) { spec = null; }
    mount(root, spec, { heading: 1 });
  }
}
