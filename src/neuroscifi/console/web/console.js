// NeuroSciFi owner console. Vanilla ES module, no framework, CSP-compatible (no inline code or styles in markup).
// The session token arrives in the URL fragment, is kept in this module only and removed from the address bar.

const VIEWS = ['overview', 'teach', 'evidence', 'questions', 'research', 'knowledge', 'graphs', 'checkpoints', 'memory'];
const CHANNEL_ROOT = { camera: 'Camera root', word: 'Partner (word root)', reference: 'Reference root' };
const MAP_TEXT = { 0: 'as declared', 1: 'inverted', null: 'tied' };
const CHECK_LABELS = {
  independent_heldout: 'Independent held-out data',
  heldout_gain: 'Beats the incumbent on held-out data',
  retention: 'Retains older held-out performance',
  byte_budget: 'Fits the byte budget',
  lineage_intact: 'Trained only on data that still exists',
};
const MAX_BODY = 4096;

const state = { token: null, view: null, epoch: '', epochs: [], semantics: null, datasets: [], graphs: [],
                selectedGraph: null, graph: null, seq: {}, busy: new Set() };
const $ = (id) => document.getElementById(id);

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// ---- DOM helpers ---------------------------------------------------------------------------------------

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

const SVG = 'http://www.w3.org/2000/svg';
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  for (const kid of kids) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

function fill(slot, ...nodes) {
  slot.replaceChildren(...nodes.flat().filter((n) => n !== null && n !== undefined && n !== false));
  slot.removeAttribute('aria-busy');
}

function empty(text) { return h('p', { class: 'empty', text }); }

// Captions are visually hidden unless they add to the card heading; screen readers always get them.
function table(caption, headers, rows, { showCaption = false } = {}) {
  const head = h('tr', {}, headers.map((col) => h('th', { scope: 'col', class: col.num ? 'num' : null, text: col.label ?? col })));
  const body = rows.map((cells) => h('tr', {}, cells.map((cell, i) => {
    const cls = [headers[i]?.num ? 'num' : null, cell?.actions ? 'actions' : null].filter(Boolean).join(' ') || null;
    const content = cell?.actions ? cell.actions : cell;
    return h('td', { class: cls }, content instanceof Node || Array.isArray(content) ? content : String(content ?? '—'));
  })));
  const t = h('table', { class: headers.length >= 5 ? 'many' : null }, h('caption', { class: showCaption ? null : 'vh', text: caption }), h('thead', {}, head), h('tbody', {}, body));
  return h('div', { class: 'tablewrap', tabindex: '0', role: 'region', 'aria-label': caption }, t);
}

function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).flatMap(([k, v]) => [h('dt', { text: k }), h('dd', {}, v instanceof Node ? v : String(v ?? '—'))]));
}

function badge(text, tone) { return h('span', { class: `badge ${tone}`, text }); }

function fmtTime(t) {
  if (t === null || t === undefined) return '—';
  return new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

function fmtNum(x, digits = 4) {
  if (x === null || x === undefined) return '—';
  if (typeof x !== 'number') return String(x);
  if (x !== 0 && Math.abs(x) < 0.01) return x.toExponential(3);
  return x.toFixed(digits);
}

function fmtBytes(n) {
  if (n === null || n === undefined) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / 1024 / 1024).toFixed(2)} MiB`;
}

function statusTone(status) {
  return { active: 'ok', valid: 'ok', candidate: 'info', archived: 'neutral', rejected: 'warn', invalidated: 'warn',
           stale: 'warn', deleted: 'neutral', missing: 'warn' }[status] ?? 'neutral';
}

function say(el, text, tone = 'ok') {
  el.textContent = text;
  el.className = `msg ${tone}`;
}

let toastTimer = null;
// The floating notice only appears when the request's own status line is scrolled out of view.
function announce(text, tone = 'ok', anchor = null) {
  const el = $('announce');
  if (anchor?.isConnected) {
    const box = anchor.getBoundingClientRect();
    if (box.bottom > 0 && box.top < window.innerHeight) { el.textContent = ''; return; }
  }
  el.textContent = text;
  el.className = `toast ${tone}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.textContent = ''; }, 6000);
}

function errorText(error) {
  if (error instanceof ApiError) return error.status ? `${error.message} (HTTP ${error.status})` : error.message;
  return 'Unexpected error in the console page.';
}

function newEventId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
  const x = [...b].map((v) => v.toString(16).padStart(2, '0')).join('');
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}

// ---- API -----------------------------------------------------------------------------------------------

// The number of requests in flight is exposed on <html data-inflight> so tests and styles can tell when the view settled.
let inflight = 0;
function track(delta) {
  inflight += delta;
  document.documentElement.dataset.inflight = String(inflight);
}

async function api(method, path, body) {
  track(1);
  try {
    return await request(method, path, body);
  } finally {
    track(-1);
  }
}

async function request(method, path, body) {
  const options = { method, cache: 'no-store', credentials: 'omit', headers: { Authorization: `Bearer ${state.token}` } };
  if (method === 'POST') {
    options.body = JSON.stringify(body ?? {});
    if (new TextEncoder().encode(options.body).length > MAX_BODY) throw new ApiError(0, `Request is larger than ${MAX_BODY} bytes; shorten the text fields.`);
    options.headers['Content-Type'] = 'application/json';
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(0, 'The console server is not reachable. Is `nsf console` still running?');
  }
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    if (response.status === 403) lock('The session token was rejected. Open the URL printed by `nsf console`; each start issues a new token.');
    throw new ApiError(response.status, data?.error ?? response.statusText);
  }
  return data;
}

const enc = encodeURIComponent;

// Run one mutation: disable its control, mark the region busy, report the outcome, never double-submit.
async function act(control, statusEl, work) {
  if (control?.disabled || state.busy.has(control)) return;
  const region = control?.closest('.card, .view');
  if (control) { state.busy.add(control); control.disabled = true; }
  region?.setAttribute('aria-busy', 'true');
  say(statusEl, 'Working…', 'busy');
  try {
    const text = await work();
    if (text) { say(statusEl, text, 'ok'); announce(text, 'ok', statusEl); }
    else statusEl.textContent = '';
  } catch (error) {
    say(statusEl, errorText(error), 'error');
    announce(errorText(error), 'error', statusEl);
  } finally {
    if (control) { state.busy.delete(control); control.disabled = false; }
    region?.removeAttribute('aria-busy');
    syncControls();
  }
}

// Loads keep only the newest response per slot so slow replies never overwrite fresher ones.
async function load(key, slots, work) {
  const ticket = (state.seq[key] = (state.seq[key] ?? 0) + 1);
  for (const slot of slots) {
    slot.setAttribute('aria-busy', 'true');
    if (!slot.childElementCount) slot.replaceChildren(empty('Loading…'));
  }
  try {
    const result = await work();
    if (ticket === state.seq[key]) return result;
  } catch (error) {
    if (ticket === state.seq[key]) for (const slot of slots) fill(slot, h('p', { class: 'msg error', text: errorText(error) }));
  }
  return undefined;
}
const fresh = (key, ticket) => ticket === state.seq[key];

function confirmAction(title, text, okLabel) {
  const dialog = $('confirmDialog');
  $('confirmTitle').textContent = title;
  $('confirmText').textContent = text;
  $('confirmOk').textContent = okLabel;
  return new Promise((resolve) => {
    const done = (value) => {
      $('confirmOk').removeEventListener('click', ok);
      $('confirmCancel').removeEventListener('click', cancel);
      dialog.removeEventListener('cancel', cancel);
      if (dialog.open) dialog.close();
      resolve(value);
    };
    const ok = () => done(true);
    const cancel = (event) => { event?.preventDefault?.(); done(false); };
    $('confirmOk').addEventListener('click', ok);
    $('confirmCancel').addEventListener('click', cancel);
    dialog.addEventListener('cancel', cancel);
    dialog.showModal();
    $('confirmCancel').focus();
  });
}

// ---- header and overview -------------------------------------------------------------------------------

async function loadHeader() {
  const inv = await api('GET', '/api/inventory');
  state.semantics = inv.birth.action_semantics;
  $('instanceId').textContent = inv.birth.instance_id.slice(0, 8);
  $('instanceId').title = inv.birth.instance_id;
  $('stateVersion').textContent = String(inv.state_version);
  const writes = $('writes');
  writes.textContent = inv.regulation.writes_allowed ? 'allowed' : `paused: ${inv.regulation.reason}`;
  writes.className = `badge ${inv.regulation.writes_allowed ? 'ok' : 'warn'}`;
  const open = inv.memory.open_questions;
  $('openCount').textContent = open ? `${open} open` : '';
  renderSemantics();
  return inv;
}

async function loadOverview() {
  const slots = ['birth', 'regulation', 'inventory', 'development'].map($);
  const inv = await load('overview', slots, loadHeader);
  if (!inv) return;
  const b = inv.birth;
  fill($('birth'), kv([
    ['Instance id', h('code', { text: b.instance_id })],
    ['Born', fmtTime(b.created_at)],
    ['Software', `${b.software_version} (schema ${b.schema_version})`],
    ['Action 0', b.action_semantics['0']],
    ['Action 1', b.action_semantics['1']],
    ['Encoders', b.encoders.map((e) => `${e.name} (${e.channels.join(', ')}; ${e.externally_trained ? 'externally trained' : 'not externally trained'})`).join('; ')],
    ['Initial graph', b.initial_graph ? `revision ${b.initial_graph.revision_id} (${b.initial_graph.context})` : 'none'],
    ['Note', b.note ?? '—'],
  ]), h('h3', { text: 'Priors' }), kv(Object.entries(b.priors).map(([k, v]) => [k.replaceAll('_', ' '), v])));
  const r = inv.regulation;
  fill($('regulation'), h('p', {}, badge(r.writes_allowed ? 'Writes allowed' : 'Writes paused', r.writes_allowed ? 'ok' : 'warn'),
    r.reason ? ` ${r.reason}` : ''), kv([
    ['Evidence rows', `${r.evidence_rows} of ${r.config.max_evidence_rows}`],
    ['Open questions', r.open_questions],
    ['Evidence written', r.write_volume],
    ['Evidence lifetime', `${(r.config.evidence_ttl_seconds / 86400).toFixed(1)} days unless promoted`],
    ['Free-space reserve', fmtBytes(r.config.min_free_bytes)],
    ['Ask budget per epoch', r.config.ask_budget_per_epoch],
  ]));
  const mem = inv.memory;
  fill($('inventory'), table('Memory inventory', ['Record', { label: 'Count', num: true }],
    Object.entries(mem).map(([k, v]) => [k.replaceAll('_', ' '), v])));
  const dev = Object.entries(inv.development);
  fill($('development'), dev.length
    ? table('Development counters', ['Counter', { label: 'Value', num: true }], dev.map(([k, v]) => [k.replaceAll('_', ' '), v]))
    : empty('No development yet: record evidence or run a graph job.'));
}

// ---- epochs ---------------------------------------------------------------------------------------------

async function loadEpochs() {
  state.epochs = await api('GET', '/api/epochs');
  if (!state.epochs.some((e) => e.name === state.epoch)) state.epoch = state.epochs.at(-1)?.name ?? '';
  for (const select of [$('epochSelect'), $('evEpoch')]) {
    const options = state.epochs.length
      ? state.epochs.map((e) => h('option', { value: e.name, text: `${e.name} (${e.evidence} evidence)` }))
      : [h('option', { value: '', text: 'No epochs yet' })];
    select.replaceChildren(...options);
    select.value = state.epoch;
  }
  const current = state.epochs.find((e) => e.name === state.epoch);
  $('epochSummary').textContent = current
    ? `Started ${fmtTime(current.created_at)} · ${current.evidence} stored (${current.learning} learning, ${current.memory_only} memory-only, ${current.promoted} promoted) · ${current.forgotten} forgotten or expired · panel context ${current.context ?? 'none'} · ${current.streams ? `${current.streams} population stream${current.streams === 1 ? '' : 's'} attached` : 'no population stream'}${current.note ? ` · ${current.note}` : ''}`
    : 'Start an epoch to begin teaching.';
  syncControls();
}

function chooseEpoch(name) {
  state.epoch = name;
  $('epochSelect').value = name;
  $('evEpoch').value = name;
  refreshView();
}

function renderSemantics() {
  if (!state.semantics) return;
  for (const [group, name, prefix] of [['actionGroup', 'action', ''], ['bitGroup', 'bit', 'observed as ']]) {
    const fieldset = $(group);
    if (fieldset.querySelector('input')) continue;
    for (const value of ['0', '1']) {
      fieldset.append(h('label', {}, h('input', { type: 'radio', name, value, checked: value === '0' }),
        `${value} · ${prefix}${state.semantics[value]}`));
    }
  }
}

// ---- teach & beliefs ------------------------------------------------------------------------------------

async function loadTeach() {
  const slots = ['beliefs', 'change', 'decision', 'explain', 'revisions'].map($);
  const ticket = (state.seq.teachEpochs = (state.seq.teachEpochs ?? 0) + 1);
  try { await loadEpochs(); } catch (error) { for (const slot of slots) fill(slot, h('p', { class: 'msg error', text: errorText(error) })); return; }
  if (!fresh('teachEpochs', ticket)) return;
  if (!state.epoch) {
    fill($('beliefs'), empty('No epoch yet. Start one, then record camera readings, partner words and references.'));
    for (const id of ['change', 'decision', 'explain', 'revisions']) fill($(id), empty('No epoch selected.'));
    renderMatch(null);
    return;
  }
  const epoch = state.epoch;
  const result = await load('teach', slots, () => Promise.all([
    api('GET', `/api/epochs/${enc(epoch)}/beliefs`), api('GET', `/api/epochs/${enc(epoch)}/decision`),
    api('GET', `/api/epochs/${enc(epoch)}/evidence?limit=500`),
    api('GET', `/api/epochs/${enc(epoch)}/match?last=12`).catch(() => null)]));
  if (!result) return;
  const [explain, decision, events, match] = result;
  renderMatch(match);
  const learning = events.filter((e) => e.consent === 'learn');
  renderBeliefs(explain.beliefs);
  renderChange(explain.change, learning);
  renderDecision(decision);
  renderExplain(explain.factors, learning);
  renderRevisions(explain.revisions);
  const roots = new Set([...Object.keys(explain.beliefs.sensors), ...Object.keys(explain.beliefs.partners)]);
  $('rootList').replaceChildren(...[...roots].map((r) => h('option', { value: r })));
  $('partnerList').replaceChildren(...Object.keys(explain.beliefs.partners).map((r) => h('option', { value: r })));
}

function probCard(label, scope, p, identifiable, detail, suspect) {
  const bar = h('div', { class: 'bar', 'aria-hidden': 'true' }, h('span', { class: 'zone' }), h('span', { class: 'fill' }), h('span', { class: 'mid' }));
  bar.querySelector('.fill').style.width = `${(p * 100).toFixed(1)}%`;
  return h('div', { class: 'prob', 'data-scope': scope },
    h('div', { class: 'probhead' }, h('span', { class: 'probname', text: label }),
      badge(identifiable ? 'Identifiable' : 'Unidentifiable', identifiable ? 'ok' : 'warn')),
    suspect?.suspected_change ? h('p', { class: 'suspect' }, badge(`change suspected (llr ${suspect.llr.toFixed(2)})`, 'warn'),
      ` since evidence #${suspect.suspected_since_seq}${suspect.ambiguous ? ', ambiguous' : ''}`) : null,
    bar,
    h('p', { class: 'probnum', text: `P(inverted) = ${p.toFixed(3)} · P(as declared) = ${(1 - p).toFixed(3)}${detail}` }));
}

function renderBeliefs(b) {
  const unid = new Set(b.unidentifiable.map((u) => u.scope));
  const c = b.evidence;
  const cards = [probCard('Mechanism', 'mechanism', b.mechanism.p_inverted, b.mechanism.identifiable, ` · ${b.reference_roots} reference root(s)`, b.mechanism)];
  for (const [root, v] of Object.entries(b.sensors)) cards.push(probCard(`Camera ${root}`, `camera:${root}`, v.p_inverted, !unid.has(`camera:${root}`), ` · ${v.n} reading(s)`, v));
  for (const [root, v] of Object.entries(b.partners)) cards.push(probCard(`Partner ${root}`, `partner:${root}`, v.p_inverted, !unid.has(`partner:${root}`), ` · ${v.n} word(s)`, v));
  fill($('beliefs'),
    h('ul', { class: 'counts', 'aria-label': 'Evidence counts' },
      h('li', { text: `${c.total} stored` }), h('li', { text: `${c.learning} learning` }),
      h('li', { text: `${c.memory_only} memory-only` }), h('li', { text: `${c.duplicates_ignored} duplicate copies ignored` }),
      h('li', { text: `state version ${b.state_version}` })),
    b.linked_from
      ? h('p', { id: 'linkedFrom' }, badge('Continues an earlier setup', 'info'),
          ` ${b.linked_from.epoch} (${Object.values(b.linked_from.segments).join(', ')}): ${b.linked_from.learning} earlier learning reading(s) pooled with this epoch's own.`)
      : null,
    b.confirmed_changes?.length
      ? h('ul', { class: 'counts', id: 'confirmedChanges', 'aria-label': 'Confirmed changes' },
          b.confirmed_changes.map((c) => h('li', { text: `${changeName(c.scope)} since #${c.since_seq}` })))
      : null,
    h('div', { class: 'probs', id: 'probs' }, cards),
    h('p', { class: 'sub', text: 'Bars show P(inverted). The hatched band (0.13 to 0.87) is where a factor counts as unidentifiable.' }),
    b.unidentifiable.length
      ? [h('h3', { text: 'Why some factors are unidentifiable' }), h('ul', { class: 'reasons', id: 'unidentifiable' },
          b.unidentifiable.map((u) => h('li', {}, h('strong', { text: `${u.scope}: ` }), u.why)))]
      : h('p', { id: 'unidentifiable', text: 'Every factor is identifiable from the current evidence.' }));
}

function changeName(scope) {
  const [kind, ...root] = scope.split(':');
  return kind === 'camera' ? root.join(':') : kind === 'partner' ? `partner ${root.join(':')}` : scope;
}

function renderMatch(m) {
  const card = $('matchCard');
  card.hidden = !m?.best;
  if (!m?.best) { $('matchPanel').replaceChildren(); return; }
  fill($('matchPanel'),
    h('p', { id: 'matchBest' }, h('strong', { text: m.best }), ` explains this epoch's last ${m.events} learning reading(s) with posterior ${m.best_posterior.toFixed(3)}.`),
    table('Earlier epochs ranked by fit', ['Epoch', 'Segments', { label: 'Log-likelihood', num: true }, { label: 'Posterior', num: true }, { label: 'Readings', num: true }],
      m.ranking.map((r) => [r.epoch, Object.values(r.segments).join(', '), r.log_likelihood.toFixed(2), r.posterior.toFixed(3), r.events])),
    h('p', { class: 'sub', text: `A new setup: posterior ${m.new_setup_posterior.toFixed(3)}. The executive asks only when the best earlier setup reaches ${m.ask_posterior} (3:1 against everything else).` }));
}

function inScope(e, scope) {
  if (scope === 'mechanism') return true;
  const [kind, ...root] = scope.split(':');
  return e.channel === (kind === 'partner' ? 'word' : kind) && e.root === root.join(':');
}

function renderChange(c, events) {
  const threshold = Math.log(19);
  const parts = [];
  if (!c.window) parts.push(empty('No learning evidence to test yet.'));
  else if (c.suspected) {
    parts.push(h('p', {}, badge(c.ambiguous ? 'Change suspected · ambiguous' : 'Change suspected', 'warn')));
    parts.push(kv([['Scope', h('code', { id: 'changeScope', text: c.scope })], ['Since', `evidence #${c.since_seq}`],
      ['Penalised log-likelihood gain', `${c.llr.toFixed(2)} nats (threshold ${threshold.toFixed(2)})`],
      ['Ambiguous', c.ambiguous ? 'yes: the runner-up is within 1 nat' : 'no']]));
    const after = events.filter((e) => e.seq >= c.since_seq && inScope(e, c.scope));
    parts.push(h('h3', { text: `Experience behind the change (${after.length})` }),
      h('p', { class: 'sub', text: `${c.scope} readings from evidence #${c.since_seq} on, which fit a flipped mapping better than the old one.` }),
      h('ul', { class: 'events', id: 'changeEvents', tabindex: '0', 'aria-label': 'Readings after the changepoint' }, after.slice().reverse().map(eventLine)));
  } else {
    parts.push(h('p', {}, badge('No change suspected', 'ok')));
    parts.push(kv([['Best gain', `${c.llr.toFixed(2)} nats (threshold ${threshold.toFixed(2)})`]]));
  }
  if (c.window) {
    parts.push(h('p', { class: 'sub', text: `Tested ${c.tried} changepoints over the last ${c.window} learning events.` }));
    if (c.alternatives.length) parts.push(table('Alternative scopes', ['Scope', { label: 'Gain (nats)', num: true }], c.alternatives.map((a) => [a.scope, a.llr.toFixed(2)])));
  }
  fill($('change'), parts);
}

function renderDecision(d, recorded = false) {
  fill($('decision'),
    h('p', { class: 'verdict', id: 'decisionValue', text: d.decision.replace('_', ' ') }),
    kv([['Scope', d.scope ?? '—'], ['Reason', d.reason], ['Questions asked', `${d.asks_used} of ${d.ask_budget} in this epoch`],
      ['Open question', d.pending_id ? `#${d.pending_id}` : 'none'],
      d.closed_pending?.length ? ['Closed by evidence', d.closed_pending.map((i) => `#${i}`).join(', ')] : null,
      ['Status', recorded ? 'recorded in the decision log' : 'preview; not yet recorded']]));
}

function eventLine(e) {
  return h('li', { 'data-id': e.id }, h('code', { text: e.id }), ` · #${e.seq} · ${e.channel} · ${e.root} · action ${e.action} → reading ${e.bit}`);
}

function splitNote(f, events) {
  if (f.supporting_total + f.counterevidence_total > 0) return null;
  const own = events.filter((e) => (f.scope === 'mechanism' ? e.channel === 'reference' : inScope(e, f.scope)));
  if (!own.length) return null;
  const ones = own.filter((e) => (e.bit ^ e.action) === 1).length;
  return h('p', { class: 'sub split', text: `Its ${own.length} reading(s) are split evenly (${own.length - ones} read the declared mapping, ${ones} the flipped one), ` +
    'so there is no preferred reading for any of them to support or contradict.' });
}

function renderExplain(factors, events) {
  fill($('explain'), factors.map((f) => h('section', { class: 'factor', 'data-scope': f.scope, 'aria-label': `Factor ${f.scope}` },
    h('div', { class: 'factorhead' }, h('h3', { text: f.scope }), h('span', { class: 'sub', text: `P(inverted) = ${f.p_inverted.toFixed(3)}` })),
    h('p', { class: 'uncertainty' }, h('strong', { text: 'Remaining uncertainty: ' }), f.uncertainty),
    splitNote(f, events),
    h('div', { class: 'evlists' },
      h('div', {}, h('h4', { text: `Supporting evidence (${f.supporting_total})` }),
        f.supporting.length ? h('ul', { class: 'events supporting', tabindex: '0', 'aria-label': `Supporting evidence for ${f.scope}` }, f.supporting.slice().reverse().map(eventLine)) : empty('None')),
      h('div', {}, h('h4', { text: `Counterevidence (${f.counterevidence_total})` }),
        f.counterevidence.length ? h('ul', { class: 'events counter', tabindex: '0', 'aria-label': `Counterevidence for ${f.scope}` }, f.counterevidence.slice().reverse().map(eventLine)) : empty('None'))))));
}

function causeText(cause) {
  const [kind, ...rest] = cause.split(':');
  const target = rest.join(':');
  if (cause === 'evidence:#forgotten') return 'evidence that has since been forgotten';
  if (kind === 'resolve') return `your answer to question #${target}`;
  return { evidence: `new evidence ${target}`, forget: `forgot ${target}`, expired: 'expiry sweep' }[kind] ?? cause;
}

function mapText(m) {
  if (!m) return '—';
  const parts = [`mechanism ${MAP_TEXT[m.mechanism]}${m.identifiable ? ' (identifiable)' : ' (unidentifiable)'}`];
  for (const [r, v] of Object.entries(m.sensors ?? {})) parts.push(`camera ${r} ${MAP_TEXT[v]}`);
  for (const [r, v] of Object.entries(m.partners ?? {})) parts.push(`partner ${r} ${MAP_TEXT[v]}`);
  return parts.join('; ');
}

function renderRevisions(revs) {
  fill($('revisions'), revs.length
    ? table('Belief revisions, oldest first (last 20)', ['Revision', { label: 'State', num: true }, 'Time', 'Caused by', 'Beliefs after the change'],
        revs.map((r) => [`#${r.id}`, r.state_version, fmtTime(r.created_at), causeText(r.cause), mapText(r.map)]), { showCaption: true })
    : empty('No revisions yet. A revision is recorded whenever a belief or its identifiability changes.'));
}

// ---- evidence ------------------------------------------------------------------------------------------

async function loadEvidence() {
  const slot = $('evidenceTable');
  try { await loadEpochs(); } catch (error) { fill(slot, h('p', { class: 'msg error', text: errorText(error) })); return; }
  if (!state.epoch) { fill(slot, empty('No epoch yet. Start one under Teach & beliefs.')); return; }
  const query = new URLSearchParams();
  if ($('evChannel').value) query.set('channel', $('evChannel').value);
  if ($('evRoot').value.trim()) query.set('root', $('evRoot').value.trim());
  const rows = await load('evidence', [slot], () => api('GET', `/api/epochs/${enc(state.epoch)}/evidence?${query}`));
  if (!rows) return;
  fill(slot, rows.length
    ? table(`Evidence of epoch ${state.epoch}, oldest first`, [{ label: 'Seq', num: true }, 'Event id', 'Channel', 'Root', 'Action → reading', 'Consent', 'Retention', 'Expires', 'Actions'],
        rows.map((e) => [e.seq, h('code', { text: e.id }), e.channel, e.root, `${e.action} → ${e.bit}`, e.consent,
          e.retention, e.expires_at ? fmtTime(e.expires_at) : 'never', { actions: [
            h('button', { type: 'button', class: 'danger', 'data-forget': e.id, 'aria-label': `Forget evidence ${e.id}`, text: 'Forget', onclick: (ev) => forgetEvidence(ev.currentTarget, e) }),
            h('button', { type: 'button', 'data-promote': e.id, disabled: e.retention === 'promoted', 'aria-label': `Promote evidence ${e.id}`,
              text: e.retention === 'promoted' ? 'Promoted' : 'Promote', onclick: (ev) => promoteEvidence(ev.currentTarget, e) })] }]))
    : empty($('evChannel').value || $('evRoot').value ? 'No evidence matches these filters.' : 'No evidence in this epoch yet. Record observations under Teach & beliefs.'));
}

function forgetSummary(result) {
  const e = result.effects ?? {};
  if (result.status === 'absent') return 'Nothing to forget: no matching rows remain.';
  const parts = [];
  if (e.deleted !== undefined) parts.push(`${e.deleted} evidence row(s) deleted`);
  if (e.dataset !== undefined) parts.push(`dataset #${e.dataset} deleted`);
  if (e.invalidated?.length) parts.push(`graph revision(s) ${e.invalidated.join(', ')} invalidated`);
  if (e.reactivated) parts.push(`revision ${e.reactivated} re-activated`);
  if (e.pending_superseded) parts.push(`${e.pending_superseded} open question(s) superseded`);
  if (e.checkpoints_stale?.length) parts.push(`checkpoint(s) ${e.checkpoints_stale.join(', ')} now stale`);
  if (result.failure) parts.push(`warning: ${result.failure}`);
  return parts.join('; ') + '.';
}

async function forgetEvidence(button, e) {
  const ok = await confirmAction(`Forget evidence ${e.id}?`,
    `The ${e.channel} reading from ${e.root} (action ${e.action} → reading ${e.bit}) is deleted. Only a payload-free tombstone remains, ` +
    'so this id can never be recorded again. Beliefs are recomputed without it, checkpoints that contain it become stale, ' +
    'and open questions of this epoch are superseded.', 'Forget evidence');
  if (!ok) return;
  await act(button, $('evidenceStatus'), async () => {
    const result = await api('POST', '/api/forget', { kind: 'evidence', target: e.id });
    await Promise.all([loadEvidence(), loadHeader()]);
    return `Forgot ${e.id}: ${forgetSummary(result)}`;
  });
}

async function promoteEvidence(button, e) {
  const ok = await confirmAction(`Keep evidence ${e.id}?`,
    `Promoting keeps this ${e.channel} reading beyond its default lifetime: it will no longer expire. You can still forget it later.`, 'Promote evidence');
  if (!ok) return;
  await act(button, $('evidenceStatus'), async () => {
    await api('POST', '/api/promote', { id: e.id });
    await Promise.all([loadEvidence(), loadHeader()]);
    return `Promoted ${e.id}: it no longer expires.`;
  });
}

// ---- questions -----------------------------------------------------------------------------------------

async function loadQuestions() {
  const slot = $('pendingList');
  const all = $('showResolved').checked;
  const items = await load('questions', [slot], () => api('GET', `/api/pending${all ? '?all=1' : ''}`));
  if (!items) return;
  if (!items.length) { fill(slot, empty(all ? 'No questions have been asked yet.' : 'No open questions. The executive asks when a change is suspected or a meaning stays ambiguous.')); return; }
  fill(slot, h('div', { class: 'grid' }, items.map((q) => {
    const input = h('input', { id: `answer-${q.id}`, maxlength: '1024', autocomplete: 'off', required: true });
    const button = h('button', { class: 'primary', id: `resolve-${q.id}`, text: `Resolve question ${q.id}` });
    const status = h('p', { class: 'msg', role: 'status' });
    const form = h('form', { novalidate: true, onsubmit: (ev) => { ev.preventDefault(); resolveQuestion(q, input, button, status); } },
      h('label', { for: input.id, text: `Your answer to question ${q.id}` }), input, button);
    const open = !q.resolved_at;
    const epochTarget = q.scope.startsWith('epoch:') ? q.scope.slice(6) : null;
    const change = open && q.kind === 'calibrate' && !epochTarget;
    const quick = (id, text, work) => h('button', { type: 'button', id: `${id}-${q.id}`, 'aria-label': `${text} (question ${q.id})`, text,
      onclick: (ev) => answerQuestion(q, ev.currentTarget, status, work) });
    const shortcuts = !open ? null : change
      ? h('div', { class: 'buttons', role: 'group', 'aria-label': `Quick answers to question ${q.id}` },
          quick('yes', 'Yes, it changed — relearn only that', { resolution: `Confirmed: ${q.scope} changed`, confirm: true }),
          quick('no', 'No, nothing changed', { resolution: `Rejected: ${q.scope} did not change`, confirm: false }))
      : epochTarget
        ? h('div', { class: 'buttons', role: 'group', 'aria-label': `Quick answers to question ${q.id}` },
            quick('continue', 'Continue there', { resolution: `Continue with the calibration of epoch ${epochTarget}`, confirm: true }),
            quick('new', 'No, this is new', { resolution: `This is a new setup, not epoch ${epochTarget}`, confirm: false }))
        : null;
    return h('article', { class: 'card', 'data-pending': q.id, 'aria-label': `Question ${q.id}` },
      h('p', {}, badge(q.resolved_at ? 'Resolved' : 'Open', q.resolved_at ? 'neutral' : 'info'), ` #${q.id} · ${q.kind.replace('_', ' ')} · ${q.scope}`),
      h('p', { class: 'question', text: q.question }),
      kv([['Epoch', q.epoch ?? '—'], q.context ? ['Context', q.context] : null, ['Asked', fmtTime(q.created_at)],
        q.resolved_at ? ['Answered', `${fmtTime(q.resolved_at)}: ${q.resolution}`] : null]),
      shortcuts, q.resolved_at ? null : form, status);
  })));
}

async function answerQuestion(q, button, status, body) {
  const continued = body.confirm === true && q.scope.startsWith('epoch:');
  const text = `Question ${q.id} resolved and recorded as a teacher intervention.` + (continued
    ? ` Epoch ${q.epoch} now continues the calibration of ${q.scope.slice(6)}.`
    : body.confirm === true ? ' Only that factor is relearned.' : '');
  await act(button, status, async () => {
    await api('POST', `/api/pending/${q.id}/resolve`, body);
    if (continued) { state.epoch = q.epoch; $('epochSelect').value = q.epoch; $('evEpoch').value = q.epoch; }
    await Promise.all([loadQuestions(), loadHeader()]);
    return text;
  });
  if (!status.isConnected) say($('questionStatus'), text);
}

async function resolveQuestion(q, input, button, status) {
  const answer = input.value.trim();
  if (!answer) { say(status, 'Write an answer before resolving.', 'error'); input.focus(); return; }
  await act(button, status, async () => {
    await api('POST', `/api/pending/${q.id}/resolve`, { resolution: answer });
    await Promise.all([loadQuestions(), loadHeader()]);
    return `Question ${q.id} resolved and recorded as a teacher intervention.`;
  });
  if (!status.isConnected) say($('questionStatus'), `Question ${q.id} resolved and recorded as a teacher intervention.`);
}

// ---- graphs --------------------------------------------------------------------------------------------

async function loadGraphs() {
  const slots = [$('datasetTable'), $('revisionGroups')];
  const result = await load('graphs', slots, () => Promise.all([api('GET', '/api/datasets'), api('GET', '/api/graphs')]));
  if (!result) return;
  [state.datasets, state.graphs] = result;
  renderDatasets();
  renderRevisionGroups();
  renderLearnOptions();
  renderRecallOptions();
  renderMapOptions();
  if (state.selectedGraph !== null) await showGraph(state.selectedGraph);
}

function renderDatasets() {
  fill($('datasetTable'), state.datasets.length
    ? table('Datasets', [{ label: 'Id', num: true }, 'Context', 'Split', 'Provenance', 'Shape', { label: 'Size', num: true }, 'Created', 'Status', 'Actions'],
        state.datasets.map((d) => [d.id, d.context, d.split, d.provenance, `${d.rows} steps × ${d.cols} populations`, fmtBytes(d.bytes),
          fmtTime(d.created_at), d.deleted_at ? badge('deleted', 'neutral') : badge('stored', 'ok'), { actions: [
            h('button', { type: 'button', class: 'danger', disabled: !!d.deleted_at, 'aria-label': `Forget dataset ${d.id}`, text: 'Forget',
              onclick: (ev) => forgetDataset(ev.currentTarget, d) })] }]))
    : empty('No datasets yet. Simulate one to give the learner train, validation and held-out data.'));
}

function renderRevisionGroups() {
  if (!state.graphs.length) { fill($('revisionGroups'), empty('No graph revisions yet. Learn a candidate from a training and a validation dataset.')); return; }
  const contexts = [...new Set(state.graphs.map((g) => g.context))];
  fill($('revisionGroups'), contexts.map((ctx) => h('div', { class: 'ctxgroup', 'data-context': ctx },
    h('h3', { text: `Context ${ctx}` }),
    table(`Revisions of context ${ctx}`, [{ label: 'Revision', num: true }, 'Status', 'Encoding', { label: 'Bytes', num: true }, { label: 'Validation MSE', num: true }, { label: 'Held-out MSE', num: true }, 'Created', 'Actions'],
      state.graphs.filter((g) => g.context === ctx).map((g) => [g.id, badge(g.status, statusTone(g.status)),
        g.origin === 'founding' ? 'founding' : `${g.bits}-bit, degree ${g.degree}`, g.byte_size, fmtNum(g.metrics?.validation_mse), fmtNum(g.metrics?.heldout_mse),
        fmtTime(g.created_at), { actions: [h('button', { type: 'button', 'aria-label': `View revision ${g.id}`, 'data-view-graph': g.id, text: 'View',
          onclick: (ev) => { state.selectedGraph = g.id; showGraph(g.id, ev.currentTarget); } })] }])))));
}

function option(value, text) { return h('option', { value, text }); }

function renderLearnOptions() {
  const live = state.datasets.filter((d) => !d.deleted_at);
  const contexts = [...new Set(live.map((d) => d.context))];
  const select = $('learnContext');
  const previous = select.value;
  select.replaceChildren(...(contexts.length ? contexts.map((c) => option(c, c)) : [option('', 'No datasets yet')]));
  select.value = contexts.includes(previous) ? previous : contexts.at(-1) ?? '';
  const ctx = select.value;
  for (const [id, split] of [['learnTrain', 'train'], ['learnValidation', 'validation']]) {
    const rows = live.filter((d) => d.context === ctx && d.split === split);
    $(id).replaceChildren(...(rows.length ? rows.map((d) => option(d.id, `#${d.id} · ${d.rows} steps · ${d.note ?? d.provenance}`)) : [option('', `No ${split} data`)]));
    if (rows.length) $(id).value = String(rows.at(-1).id);
  }
  syncControls();
}

function renderRecallOptions() {
  const live = state.datasets.filter((d) => !d.deleted_at);
  const select = $('recallDataset');
  const previous = select.value;
  select.replaceChildren(...(live.length ? live.map((d) => option(d.id, `#${d.id} · ${d.context} ${d.split}`)) : [option('', 'No datasets yet')]));
  const heldout = live.filter((d) => d.split === 'heldout').at(-1);
  select.value = live.some((d) => String(d.id) === previous) ? previous : String(heldout?.id ?? live.at(-1)?.id ?? '');
  const active = state.graphs.filter((g) => g.status === 'active').map((g) => g.context);
  const fieldset = $('recallContexts');
  const checked = new Set([...fieldset.querySelectorAll('input:checked')].map((i) => i.value));
  fieldset.replaceChildren(fieldset.querySelector('legend'), ...(active.length
    ? active.map((c) => h('label', {}, h('input', { type: 'checkbox', value: c, checked: checked.has(c) }), c))
    : [empty('No active graphs yet. Promote a candidate first.')]));
  syncControls();
}

function renderMapOptions() {
  const live = state.datasets.filter((d) => !d.deleted_at);
  const select = $('fmapDataset');
  const previous = select.value;
  select.replaceChildren(...(live.length ? live.map((d) => option(d.id, `#${d.id} · ${d.context} ${d.split}`)) : [option('', 'No datasets yet')]));
  select.value = live.some((d) => String(d.id) === previous) ? previous : String(live.at(-1)?.id ?? '');
  syncControls();
}

function renderFunctionalMap(r) {
  fill($('fmapResult'),
    kv([['Dataset', `#${r.dataset_id} (context ${r.context}, active revision ${r.revision_id})`], ['Lags', r.lags.join(', ')],
      ['Pairs scored', r.pairs], ['Edges in the active graph', r.edges_in_graph]]),
    r.routes.length
      ? table('Proposed routes not in the active graph, strongest first', ['Route', { label: 'Lag', num: true }, { label: 'Correlation', num: true }, { label: 'Steps', num: true }],
          r.routes.map((x) => [`${x.src} → ${x.dst}`, x.lag, x.correlation.toFixed(3), x.n]))
      : empty('No route outside the active graph to propose.'),
    h('p', { class: 'sub', id: 'fmapCaveat', text: `Caveat: ${r.caveat}.` }));
}

async function showGraph(id, button) {
  const ticket = (state.seq.graph = (state.seq.graph ?? 0) + 1);
  const slot = $('graphDetail');
  slot.setAttribute('aria-busy', 'true');
  if (button) button.disabled = true;
  try {
    const g = await api('GET', `/api/graphs/${id}`);
    if (!fresh('graph', ticket)) return;
    state.graph = g;
    renderGraph(g);
  } catch (error) {
    if (fresh('graph', ticket)) fill(slot, h('p', { class: 'msg error', text: errorText(error) }));
  } finally {
    if (button?.isConnected) button.disabled = false;
    syncControls();
  }
  if (button) $('h-gdetail').scrollIntoView({ block: 'start' });
}

function renderGraph(g) {
  const m = g.metrics ?? {};
  fill($('graphDetail'),
    h('p', {}, h('strong', { text: `Revision #${g.id} · context ${g.context} ` }), badge(g.status, statusTone(g.status))),
    kv([['Origin', g.origin], ['Scoring rule', g.rule_version], ['Encoding', `${g.bits}-bit, degree ${g.degree}, ${g.rows}×${g.cols}`],
      ['Snapshot size', `${g.byte_size} bytes`], ['Edges', g.edges],
      ['Validation MSE', `${fmtNum(m.validation_mse)} (dense ${fmtNum(m.dense_validation_mse)})`],
      ['Held-out MSE', m.heldout_mse === undefined ? 'not evaluated yet' : fmtNum(m.heldout_mse)],
      ['Lineage', g.lineage.length ? g.lineage.map((l) => `${l.role} #${l.dataset_id}${l.deleted ? ' (deleted)' : ''}`).join(', ') : 'none'],
      ['Created', fmtTime(g.created_at)], ['Decided', fmtTime(g.decided_at)]]));
  $('graphActions').hidden = false;
  renderDecisionChecklist(g.decision);
  renderEvaluation(m.evaluation);
  renderHeatmap(g);
}

function checkDetail(name, c) {
  if (c.why && c.passed === null) return c.why;
  switch (name) {
    case 'independent_heldout':
      return c.datasets.length ? `held-out dataset(s) ${c.datasets.map((d) => `#${d}`).join(', ')} are outside the training lineage${c.excluded?.length ? `; excluded ${c.excluded.join(', ')}` : ''}` : 'no held-out dataset outside the training lineage';
    case 'heldout_gain':
      return c.incumbent_mse === null || c.incumbent_mse === undefined
        ? `candidate MSE ${fmtNum(c.candidate_mse)}; ${c.why ?? 'no incumbent'}`
        : `candidate MSE ${fmtNum(c.candidate_mse)} vs incumbent ${fmtNum(c.incumbent_mse)}; must be below ${fmtNum(c.threshold)} (minimum gain ${c.min_heldout_gain})`;
    case 'retention':
      return c.datasets?.length
        ? c.datasets.map((r) => `#${r.dataset_id}: candidate ${fmtNum(r.candidate_mse)} ≤ limit ${fmtNum(r.limit)}? ${r.passed ? 'yes' : 'no'}`).join('; ')
        : `${c.why ?? 'no older held-out data'} (tolerance ${c.retention_tolerance})`;
    case 'byte_budget':
      return `${c.byte_size} of ${c.byte_budget} bytes`;
    case 'lineage_intact':
      return c.deleted?.length ? `training or validation data was forgotten: ${c.deleted.map((d) => `#${d}`).join(', ')}` : `dataset(s) ${c.datasets.map((d) => `#${d}`).join(', ')} still exist`;
    default:
      return JSON.stringify(c);
  }
}

function renderDecisionChecklist(d) {
  const slot = $('graphDecision');
  if (!d) { fill(slot, h('h3', { text: 'Gate decision' }), empty('No decision yet. Evaluate, then run the promotion gate.')); return; }
  if (!d.checks) {
    const text = d.action === 'reject' ? `Rejected by ${d.by ?? 'owner'}: ${d.reason}` : `${d.action} at ${fmtTime(d.decided_at)}`;
    fill(slot, h('h3', { text: 'Decision' }), h('p', { id: 'gateSummary', text }), h('details', {}, h('summary', { text: 'Decision record' }), h('pre', { text: JSON.stringify(d, null, 2) })));
    return;
  }
  const mark = (p) => (p === true ? ['✓', 'Passed', 'ok'] : p === false ? ['✗', 'Failed', 'warn'] : ['–', 'Not evaluated', 'neutral']);
  fill(slot, h('h3', { text: 'Gate decision' }),
    h('p', { id: 'gateSummary' }, badge(d.promoted ? 'Promoted' : 'Rejected', d.promoted ? 'ok' : 'warn'),
      d.promoted ? ` All ${d.order.length} checks passed under ${d.rule_version}.` : ` Failed: ${d.failed.map((n) => CHECK_LABELS[n] ?? n).join(', ')} (rule ${d.rule_version}).`),
    h('ul', { class: 'checklist', id: 'gateChecklist' }, d.order.map((name) => {
      const [symbol, word, tone] = mark(d.checks[name].passed);
      return h('li', { 'data-check': name, 'data-passed': String(d.checks[name].passed) },
        h('span', { class: `mark badge ${tone}`, 'aria-hidden': 'true', text: symbol }),
        h('span', {}, h('strong', { text: CHECK_LABELS[name] ?? name }), ` · ${word}`),
        h('span', { class: 'detail', text: checkDetail(name, d.checks[name]) }));
    })),
    d.recommendation ? h('p', { class: 'msg error', text: `Recommendation: ${d.recommendation}` }) : null,
    h('p', { class: 'sub', text: `Incumbent: ${d.incumbent === null ? 'none' : `revision ${d.incumbent}`} · decided ${fmtTime(d.decided_at)}` }));
}

function renderEvaluation(e) {
  const slot = $('graphEvaluation');
  if (!e) { slot.replaceChildren(); return; }
  fill(slot, h('h3', { text: 'Held-out evaluation' }), e.heldout.length
    ? table('Held-out scores', [{ label: 'Dataset', num: true }, { label: 'MSE', num: true }], e.heldout.map((r) => [`#${r.dataset_id}`, fmtNum(r.mse)]))
    : empty('No independent held-out data for this context.'),
    h('p', { class: 'sub', text: `Mean ${fmtNum(e.heldout_mse)} · evaluated ${fmtTime(e.evaluated_at)}` }));
}

function mix(t, rgb) {
  const c = rgb.map((v) => Math.round(255 + (v - 255) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
const BLUE = [29, 95, 163];
const RUST = [178, 70, 38];
function weightColor(w, max) {
  if (w === 0) return '#ffffff';
  const t = 0.2 + 0.8 * Math.min(1, Math.abs(w) / max);
  return mix(t, w > 0 ? BLUE : RUST);
}

function renderHeatmap(g) {
  const slot = $('graphMatrix');
  const matrix = g.matrix;
  const rows = matrix.length, cols = matrix[0]?.length ?? 0;
  const values = matrix.flat();
  const max = Math.max(...values.map(Math.abs), 1e-12);
  const cell = 16, pad = 26;
  const svg = s('svg', { class: 'heatmap', id: 'heatmap', viewBox: `0 0 ${pad + cols * cell + 2} ${pad + rows * cell + 2}`, role: 'img', 'aria-labelledby': 'heatTitle heatSummary' });
  svg.append(s('title', { id: 'heatTitle' }, `Weight matrix of revision ${g.id}`));
  svg.append(s('rect', { x: pad, y: pad, width: cols * cell, height: rows * cell, fill: '#ffffff', stroke: '#c6d2cb' }));
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      const w = matrix[i][j];
      const rect = s('rect', { class: 'cell', x: pad + j * cell, y: pad + i * cell, width: cell, height: cell,
        fill: weightColor(w, max), stroke: '#e3e9e5', 'stroke-width': 0.5, 'data-w': w });
      rect.append(s('title', {}, `target ${i} ← source ${j}: ${w}`));
      svg.append(rect);
    }
  }
  for (let k = 0; k < Math.max(rows, cols); k += 4) {
    if (k < cols) svg.append(s('text', { x: pad + k * cell + cell / 2, y: pad - 6, 'text-anchor': 'middle' }, k));
    if (k < rows) svg.append(s('text', { x: pad - 5, y: pad + k * cell + cell / 2 + 3, 'text-anchor': 'end' }, k));
  }
  const steps = 9;
  const legend = s('svg', { class: 'legend', viewBox: '0 0 300 46', role: 'img', 'aria-label': `Colour legend from ${(-max).toFixed(3)} (rust) through 0 (white) to +${max.toFixed(3)} (blue)` });
  for (let k = 0; k < steps; k++) {
    const w = -max + (2 * max * k) / (steps - 1);
    legend.append(s('rect', { x: 10 + k * 31, y: 4, width: 31, height: 16, fill: weightColor(Math.abs(w) < 1e-12 ? 0 : w, max), stroke: '#c6d2cb' }));
  }
  legend.append(s('text', { x: 10, y: 36 }, (-max).toFixed(3)), s('text', { x: 150, y: 36, 'text-anchor': 'middle' }, '0'), s('text', { x: 289, y: 36, 'text-anchor': 'end' }, `+${max.toFixed(3)}`));
  const edges = [];
  matrix.forEach((row, i) => row.forEach((w, j) => { if (w !== 0) edges.push({ i, j, w }); }));
  const pos = edges.filter((e) => e.w > 0), neg = edges.filter((e) => e.w < 0);
  const inDegree = Math.max(0, ...matrix.map((row) => row.filter((w) => w !== 0).length));
  const strongest = (list) => list.length ? list.reduce((a, b) => (Math.abs(b.w) > Math.abs(a.w) ? b : a)) : null;
  const sp = strongest(pos), sn = strongest(neg);
  const summary = `${rows}×${cols} weight matrix (rows are target populations, columns are source populations). ` +
    `${edges.length} non-zero edges: ${pos.length} positive (blue) and ${neg.length} negative (rust); ${rows * cols - edges.length} cells are zero (white). ` +
    `Each target reads at most ${inDegree} sources.` +
    (sp ? ` Strongest positive: ${sp.w.toFixed(3)} from source ${sp.j} to target ${sp.i}.` : '') +
    (sn ? ` Strongest negative: ${sn.w.toFixed(3)} from source ${sn.j} to target ${sn.i}.` : '');
  fill(slot, h('h3', { text: 'Weight matrix' }),
    h('figure', { class: 'heatwrap' }, svg,
      h('div', {}, legend, h('p', { class: 'sub', text: 'Blue: positive weight. Rust: negative weight. White: no edge. Darker means larger magnitude.' }),
        h('figcaption', { id: 'heatSummary', text: summary }),
        h('details', {}, h('summary', { text: `Edge list (${edges.length})` }),
          h('ul', { class: 'edges' }, edges.map((e) => h('li', { text: `${e.j} → ${e.i}: ${e.w.toFixed(4)}` })))))));
}

async function forgetDataset(button, d) {
  const ok = await confirmAction(`Forget dataset #${d.id}?`,
    `The ${d.split} data of context ${d.context} is erased. Graph revisions trained or validated on it are invalidated ` +
    '(an older intact revision is re-activated when possible) and every existing checkpoint becomes stale.', 'Forget dataset');
  if (!ok) return;
  await act(button, $('learnStatus'), async () => {
    const result = await api('POST', '/api/forget', { kind: 'dataset', target: d.id });
    await Promise.all([loadGraphs(), loadHeader()]);
    return `Forgot dataset #${d.id}: ${forgetSummary(result)}`;
  });
}

// ---- checkpoints ---------------------------------------------------------------------------------------

async function loadCheckpoints() {
  const rows = await load('checkpoints', [$('ckTable')], () => api('GET', '/api/checkpoints'));
  if (!rows) return;
  fill($('ckTable'), rows.length
    ? table('Checkpoints', [{ label: 'Id', num: true }, 'Status', 'Policy', { label: 'Size', num: true }, { label: 'State', num: true }, 'Created', 'Last verified', 'File', 'Actions'],
        rows.slice().reverse().map((c) => [c.id, badge(c.status, statusTone(c.status)), c.policy, fmtBytes(c.bytes), c.state_version, fmtTime(c.created_at),
          c.verified_at ? fmtTime(c.verified_at) : 'never', h('code', { text: c.path.split('/').at(-1) }), { actions: [
            h('button', { type: 'button', 'aria-label': `Verify checkpoint ${c.id}`, 'data-verify': c.id, text: 'Verify', disabled: c.status === 'deleted',
              onclick: (ev) => verifyCheckpoint(ev.currentTarget, c) })] }]))
    : empty('No checkpoints yet. Create one to keep a verified archive of this individual.'));
}

async function verifyCheckpoint(button, c) {
  await act(button, $('ckStatus'), async () => {
    const result = await api('POST', `/api/checkpoints/${c.id}/verify`);
    await loadCheckpoints();
    if (result.ok === false) throw new ApiError(0, `Checkpoint ${c.id} failed verification: ${result.error ?? result.status}`);
    return `Checkpoint ${c.id} verified: full decode matches its footer hash (status ${result.status}).`;
  });
}

function keepValue(status) {
  const text = $('ckKeep').value.trim();
  if (!/^\d+$/.test(text) || Number(text) < 1) { say(status, 'Keep must be a whole number of at least 1.', 'error'); $('ckKeep').focus(); return null; }
  return Number(text);
}

async function pruneCheckpoints(button) {
  const status = $('ckKeepStatus');
  const keep = keepValue(status);
  if (keep === null) return;
  const ok = await confirmAction(`Prune checkpoints, keeping ${keep}?`,
    `The archive files of every valid checkpoint beyond the newest ${keep} are deleted from disk. This cannot be undone.`, 'Prune checkpoints');
  if (!ok) return;
  await act(button, status, async () => {
    const r = await api('POST', '/api/checkpoints/prune', { keep });
    await Promise.all([loadCheckpoints(), loadHeader()]);
    return `Kept ${r.kept.length} checkpoint(s); deleted ${r.deleted.length}${r.missing.length ? `; ${r.missing.length} already missing` : ''}.`;
  });
}

async function rebuildCheckpoints(button) {
  const status = $('ckKeepStatus');
  const keep = keepValue(status);
  if (keep === null) return;
  const ok = await confirmAction('Rebuild stale checkpoints?',
    `A fresh checkpoint is written, the stale archive files are deleted from disk, then valid checkpoints beyond the newest ${keep} are pruned.`, 'Rebuild');
  if (!ok) return;
  await act(button, status, async () => {
    const r = await api('POST', '/api/checkpoints/rebuild', { keep, policy: $('ckRebuildPolicy').value });
    await Promise.all([loadCheckpoints(), loadHeader()]);
    return `Checkpoint ${r.new_checkpoint.id} written; ${r.deleted.length} stale archive(s) deleted; ${r.pruned.deleted.length} pruned.`;
  });
}

// ---- memory log ----------------------------------------------------------------------------------------

async function loadMemory() {
  const result = await load('memory', [$('deletionLog'), $('decisionLog')], () => Promise.all([
    api('GET', '/api/memory?kind=deletion&limit=100'), api('GET', '/api/memory?kind=decision&limit=100')]));
  if (!result) return;
  const [deletions, decisions] = result;
  fill($('deletionLog'), deletions.items.length
    ? table('Deletion log, newest first', [{ label: 'Id', num: true }, 'Time', 'Target', 'Effects', 'Failure'],
        deletions.items.map((d) => [d.id, fmtTime(d.time), `${d.payload.target_kind} ${d.payload.target_id}`,
          forgetSummary({ status: 'forgotten', effects: d.payload.effects ?? {} }), d.payload.failure ?? 'none']))
    : empty('Nothing has been forgotten or expired.'));
  fill($('decisionLog'), decisions.items.length
    ? table('Recorded decisions, newest first', [{ label: 'Id', num: true }, 'Time', 'Epoch', 'Decision', 'Scope', 'Reason'],
        decisions.items.map((d) => [d.id, fmtTime(d.time), d.scope.epoch, d.payload.decision.replace('_', ' '), d.payload.scope ?? '—', d.payload.reason]))
    : empty('No decisions recorded yet. Use “Record decision” under Teach & beliefs.'));
}

// ---- research (Web Cortex) ---------------------------------------------------------------------------------
// Everything a source supplies (titles, quotes, claims, URLs) is untrusted: it is set with textContent only, and a
// URL becomes a link only when it parses as http(s).

const MODE_LABELS = {
  auto: 'Automatic (chosen from the question)', quick_lookup: 'Quick lookup', deep_research: 'Deep research',
  continuous: 'Continuous', comparative: 'Comparison of two things', scientific: 'Scientific literature',
  technical: 'Technical (packages, releases, advisories)', change_detection: 'Change detection',
  fact_verification: 'Fact verification', gap_resolution: 'Gap resolution',
};
const STAGES = [['discovering', 'Discovering'], ['reading', 'Reading'], ['evaluating', 'Evaluating'], ['synthesizing', 'Synthesizing']];
const TASK_LABELS = { queued: 'Queued', discovering: 'Discovering', reading: 'Reading', evaluating: 'Evaluating',
                      synthesizing: 'Synthesizing', completed: 'Completed', partial: 'Partial', failed: 'Failed', cancelled: 'Cancelled' };
const TERMINAL = ['completed', 'partial', 'failed', 'cancelled'];
const POLL_MS = 2000;
const web = { status: null, tasks: [], taskId: null, task: null, taskSig: '', listSig: '', seen: new Map(), timer: null, polling: false };

const modeLabel = (m) => MODE_LABELS[m] ?? m ?? '—';
const short = (text, n = 70) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);
const isActive = (t) => !TERMINAL.includes(t.status) && !t.proposed;
const pct = (x) => (typeof x === 'number' ? `${Math.round(x * 100)}%` : '—');
const fmtDay = (t) => (t === null || t === undefined ? 'undated' : new Date(t * 1000).toISOString().slice(0, 10));

function taskLabel(t) {
  if (t.proposed) return 'Proposed (awaiting your approval)';
  const label = TASK_LABELS[t.status] ?? t.status;
  return t.cancel_requested && isActive(t) ? `${label} · cancelling` : label;
}

function taskTone(t) {
  if (t.status === 'completed') return 'ok';
  if (t.status === 'partial' || t.status === 'failed') return 'warn';
  if (t.status === 'cancelled' || t.status === 'queued' || t.proposed) return 'neutral';
  return 'info';
}

function originLabel(t) {
  const o = t.origin ?? '';
  if (t.family || o.startsWith('pocket')) return 'Pocket device';
  if (o.startsWith('monitor:')) return `Monitor #${o.slice(8)}`;
  if (o.startsWith('refresh:')) return `Refresh of knowledge #${o.slice(8)}`;
  return { console: 'Owner console', cli: 'Command line', mcp: 'MCP client', curiosity: 'Curiosity (autonomous)' }[o] ?? o;
}

function safeLink(url, text) {
  let parsed = null;
  try { parsed = new URL(url); } catch { parsed = null; }
  if (!parsed || !['http:', 'https:'].includes(parsed.protocol)) return h('span', { class: 'url', text: String(url ?? '') });
  return h('a', { href: parsed.href, rel: 'noopener noreferrer', target: '_blank', class: 'url', text: text ?? parsed.href });
}

function citeLinks(numbers) {
  return (numbers ?? []).map((n) => h('a', { href: `#src-${n}`, class: 'cite', 'aria-label': `source ${n}`, text: `[${n}]`,
    onclick: (ev) => { ev.preventDefault(); const el = $(`src-${n}`); if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); } } }));
}

function fillOptions(select, values, labels) {
  if (select.options.length) return;
  select.replaceChildren(...values.map((v) => h('option', { value: v, text: labels(v) })));
}

// Keep keyboard focus and open disclosures across a re-render of a live region of the page.
function rerender(slot, nodes) {
  const focused = slot.contains(document.activeElement) ? document.activeElement.id : null;
  const open = [...slot.querySelectorAll('details[open][id]')].map((d) => d.id);
  fill(slot, nodes);
  for (const id of open) { const d = $(id); if (d) d.open = true; }
  if (focused && $(focused)) $(focused).focus();
}

async function loadResearch() {
  const result = await load('research', ['webSummary', 'taskList', 'monitorList', 'providerTable', 'curiosityList'].map($),
    () => Promise.all([api('GET', '/api/web/status'), loadTaskList(), api('GET', '/api/web/monitors')]));
  if (!result) return;
  const [status, , monitors] = result;
  renderWebStatus(status);
  renderMonitors(monitors);
  if (web.taskId) await loadTask(web.taskId);
  schedulePoll();
}

async function loadWebStatus() {
  renderWebStatus(await api('GET', '/api/web/status'));
}

function renderWebStatus(s) {
  web.status = s;
  const c = s.config;
  fillOptions($('askMode'), ['auto', ...s.modes], modeLabel);
  fillOptions($('monMode'), ['continuous', ...s.modes.filter((m) => m !== 'continuous')], modeLabel);
  $('webOff').hidden = c.enabled;
  for (const box of $('webSwitches').querySelectorAll('input[data-switch]')) {
    if (!state.busy.has(box)) box.checked = !!c[box.dataset.switch];
  }
  const onOff = (on) => badge(on ? 'On' : 'Off', on ? 'ok' : 'neutral');
  const counts = Object.entries(s.tasks).map(([k, v]) => `${v} ${TASK_LABELS[k]?.toLowerCase() ?? k}`).join(', ');
  fill($('webSummary'), kv([
    ['Web access', onOff(c.enabled)], ['Autonomous research', onOff(c.autonomous)],
    ['Pocket research', onOff(c.pocket_research)], ['JavaScript rendering', onOff(c.browser_rendering)],
    ['Task runner', s.worker.embedded ? (s.worker.running ? 'this console (embedded worker, --web-worker)' : 'embedded worker stopped') : 'external: the neuroscifi-webcortex service (nsf web work)'],
    ['Tasks', counts || 'none yet'],
    ['Public cache', s.cache ? `${s.cache.documents} documents, ${fmtBytes(s.cache.bytes)}` : 'empty'],
  ]));
  fill($('providerTable'), s.providers.length
    ? table('Search provider and engine health', ['Provider / engine', 'State', 'Calls', 'Last error'],
        s.providers.map((p) => [p.name, badge(p.state === 'closed' ? 'healthy' : p.state === 'open' ? 'cooling down' : p.state.replace('_', ' '),
          p.state === 'closed' ? 'ok' : p.state === 'open' ? 'warn' : 'neutral'), `${p.successes ?? 0} ok, ${p.failures ?? 0} failed`, p.last_error ?? 'none']))
    : empty('No providers configured.'));
  const cur = s.curiosity;
  fill($('curiosityList'), cur === null
    ? empty('Curiosity is shown while web access is on.')
    : cur.length
      ? h('ol', { class: 'reasons' }, cur.map((x) => h('li', {}, h('strong', { text: x.target.statement ?? x.target.key ?? '—' }),
          h('br'), `${x.kind} · ${modeLabel(x.mode)} · utility ${fmtNum(x.utility, 3)} · gain ${fmtNum(x.info_gain, 3)} bits`,
          h('br'), h('span', { class: 'sub', text: x.reason }))))
      : empty('Nothing is worth investigating now: what is known is fresh or well supported.'));
  $('activeCount').textContent = s.active ? `${s.active} active` : '';
  syncControls();
}

async function loadTaskList() {
  const filter = $('taskStatus').value;
  const query = filter && filter !== 'active' ? `?status=${enc(filter)}&limit=100` : '?limit=100';
  const tasks = await api('GET', `/api/web/tasks${query}`);
  noteChanges(tasks);
  web.tasks = tasks;
  renderTaskList();
  return tasks;
}

// aria-live: one polite line when a task finishes, and stage changes of the task whose report is open.
function noteChanges(tasks) {
  const lines = [];
  for (const t of tasks) {
    const before = web.seen.get(t.id);
    if (before && before !== t.status && (TERMINAL.includes(t.status) || t.id === web.taskId)) {
      lines.push(`Research “${short(t.objective, 50)}”: ${taskLabel(t)}.`);
    }
    web.seen.set(t.id, t.status);
  }
  if (lines.length) $('researchLive').textContent = lines.join(' ');
}

function renderTaskList() {
  const filter = $('taskStatus').value;
  const needle = $('taskText').value.trim().toLowerCase();
  const shown = web.tasks.filter((t) => (filter !== 'active' || isActive(t)) && (!needle || t.objective.toLowerCase().includes(needle)));
  const sig = JSON.stringify([filter, needle, shown.map((t) => [t.id, t.status, t.cancel_requested, t.updated_at]), web.taskId]);
  if (sig === web.listSig && $('taskList').querySelector('table, .empty')) { $('taskList').removeAttribute('aria-busy'); return; }
  web.listSig = sig;
  rerender($('taskList'), shown.length
    ? table('Research tasks, newest first', ['Status', 'Mode', 'Objective', 'Created', 'Origin', 'Actions'], shown.map((t) => [
        badge(taskLabel(t), taskTone(t)), modeLabel(t.mode), h('span', { class: 'objective', text: t.objective }), fmtTime(t.created_at),
        originLabel(t), { actions: taskActions(t, $('taskStatusMsg')) }]))
    : empty(web.tasks.length ? 'No task matches the filter.' : 'No research yet. Ask a question above.'));
}

function taskActions(t, statusEl) {
  const what = short(t.objective, 60);
  const out = [h('button', { type: 'button', id: `open-${t.id}`, text: t.id === web.taskId ? 'Shown' : 'Open',
    'aria-label': `Open research task: ${what}`, onclick: () => openTask(t.id) })];
  if (isActive(t) && !t.cancel_requested) {
    out.push(h('button', { type: 'button', class: 'danger', id: `cancel-${t.id}`, text: 'Cancel', 'aria-label': `Cancel research task: ${what}`,
      onclick: (ev) => act(ev.currentTarget, statusEl, async () => {
        const done = await api('POST', `/api/web/tasks/${t.id}/cancel`, {});
        await refreshTasks();
        return done.status === 'cancelled' ? `Cancelled “${what}”.` : `Cancel requested for “${what}”; it stops before its next network request.`;
      }) }));
  }
  if (['failed', 'partial', 'cancelled'].includes(t.status)) {
    out.push(h('button', { type: 'button', id: `resume-${t.id}`, text: 'Resume', 'aria-label': `Resume research task: ${what}`,
      onclick: (ev) => act(ev.currentTarget, statusEl, async () => {
        const next = await api('POST', `/api/web/tasks/${t.id}/resume`, {});
        web.taskId = next.id;
        await refreshTasks();
        return next.id === t.id ? `Queued “${what}” again; it continues from its last checkpoint.`
          : `Continuing “${what}” as a new linked task; sources read before are reused from the cache where possible.`;
      }) }));
  }
  return out;
}

async function refreshTasks() {
  await Promise.all([loadTaskList(), web.taskId ? loadTask(web.taskId) : null, loadWebStatus()]);
  schedulePoll();
}

async function openTask(id) {
  web.taskId = id;
  web.taskSig = '';
  renderTaskList();
  await loadTask(id);
  $('h-report').focus();
}

async function loadTask(id) {
  let task;
  try { task = await api('GET', `/api/web/tasks/${id}`); } catch (error) { say($('reportStatus'), errorText(error), 'error'); return; }
  if (id !== web.taskId) return;
  web.task = task;
  const sig = JSON.stringify([task.id, task.status, task.cancel_requested, task.updated_at]);
  if (sig === web.taskSig) return;
  web.taskSig = sig;
  rerender($('report'), renderReport(task));
}

function stageList(t) {
  const done = new Set(t.stages_done ?? []);
  const terminal = TERMINAL.includes(t.status);
  const items = STAGES.map(([key, label]) => {
    const step = done.has(key) ? 'done' : t.status === key ? 'current' : 'todo';
    return h('li', { class: step, 'aria-current': step === 'current' ? 'step' : null },
      h('span', { class: 'mark', 'aria-hidden': 'true', text: step === 'done' ? '✓' : step === 'current' ? '›' : '·' }),
      label, h('span', { class: 'vh', text: step === 'done' ? ' (done)' : step === 'current' ? ' (in progress)' : ' (not reached)' }));
  });
  const end = terminal ? taskLabel(t) : 'Completed';
  items.push(h('li', { class: terminal ? `done end-${t.status}` : 'todo' },
    h('span', { class: 'mark', 'aria-hidden': 'true', text: terminal ? (t.status === 'completed' ? '✓' : '!') : '·' }), end,
    h('span', { class: 'vh', text: terminal ? ' (finished)' : ' (not reached)' })));
  return h('ol', { class: 'stages', 'aria-label': 'Progress' }, items);
}

function renderReport(t) {
  const r = t.result;
  const head = [
    h('h3', { class: 'question', text: t.objective }),
    h('p', { class: 'badges' }, badge(taskLabel(t), taskTone(t)), r ? badge(r.live ? 'Live' : 'From cache', r.live ? 'info' : 'neutral') : null,
      r?.memory?.answered_from_memory ? badge('Answered from memory', 'neutral') : null,
      ` ${modeLabel(t.mode)} · ${originLabel(t)} · created ${fmtTime(t.created_at)}${t.finished_at ? ` · finished ${fmtTime(t.finished_at)}` : ''}`),
    stageList(t),
    t.cancel_requested && isActive(t) ? h('p', { class: 'msg busy', text: 'Cancel requested: the run stops before its next network request.' }) : null,
    t.error ? h('p', { class: 'msg error', text: `Error: ${t.error}` }) : null,
  ];
  if (!r) {
    return [...head, empty(isActive(t) ? 'The report appears when the run finishes. The stage above is the one the worker reports; nothing is estimated.'
      : 'This task ended without a report.')];
  }
  return [...head, ...reportBody(r), usageBlock(t), evidenceBlock(t)];
}

function answerItem(a) {
  const meta = [a.status && a.status !== 'summary' ? badge(a.status.replace('_', ' '), a.status === 'corroborated' ? 'ok' : a.status === 'contested' ? 'warn' : 'neutral') : null,
    typeof a.confidence === 'number' ? badge(`confidence ${pct(a.confidence)}`, 'neutral') : null];
  const body = a.type === 'quote' ? h('blockquote', { class: 'quote', text: a.text }) : h('p', { class: 'template', text: a.text });
  return h('li', {}, body, h('p', { class: 'answermeta' }, citeLinks(a.citations), ' ', meta));
}

function side(label, x) {
  return h('div', { class: 'side' }, h('p', { class: 'sidehead' }, `${label} `, citeLinks([x.citation]), ` ${x.domain} · ${fmtDay(x.published_at)} · ${x.source_type}`),
    x.type === 'quote' ? h('blockquote', { class: 'quote', text: x.text }) : h('p', { text: x.text }));
}

function reportBody(r) {
  const out = [h('h4', { text: 'Answer' }), h('ol', { class: 'answers' }, (r.answer ?? []).map(answerItem))];
  if (r.verdict) {
    out.push(h('h4', { text: 'Verdict' }), h('p', {}, badge(r.verdict.label, r.verdict.label === 'supported' ? 'ok' : r.verdict.label === 'insufficient' ? 'neutral' : 'warn'),
      ` confidence ${pct(r.verdict.confidence)}`), h('ul', { class: 'reasons' }, (r.verdict.reasons ?? []).map((x) => h('li', { text: x }))));
  }
  if (r.comparison?.rows?.length) {
    const [a, b] = r.comparison.items;
    const cell = (x) => (x ? h('span', {}, x.text, ' ', citeLinks(x.citations)) : '—');
    out.push(h('h4', { text: 'Comparison' }), table(`Comparison of ${a} and ${b}`, ['Aspect', a, b], r.comparison.rows.map((row) => [row.aspect, cell(row.A), cell(row.B)])));
  }
  if (r.methodology?.length) {
    out.push(h('h4', { text: 'Methodology' }), table('Methodology of the scientific sources', ['Source', 'Kind', 'Design', 'Sample', { label: 'Year', num: true }],
      r.methodology.map((m) => [h('span', {}, citeLinks([m.source])), m.kind, m.design ?? 'not stated', m.sample ?? 'not stated', m.year ?? '—'])));
  }
  if (r.contradictions?.length) {
    out.push(h('h4', { text: 'Contradictions' }), ...r.contradictions.map((c) => h('div', { class: 'contradiction' },
      h('p', { class: 'reason', text: `Disagreement: ${c.reason}` }), h('div', { class: 'sides' }, side('A', c.a), side('B', c.b)),
      h('p', { class: 'sub', text: c.assessment }))));
  }
  const listed = (title, items) => (items?.length ? [h('h4', { text: title }), h('ul', { class: 'reasons' }, items.map((x) => h('li', { text: x })))] : []);
  out.push(...listed('Uncertainties', r.uncertainties), ...listed('Limitations', r.limitations));
  const f = r.freshness;
  if (f) {
    out.push(h('h4', { text: 'Freshness' }), kv([['Required', `${f.class}${f.window_seconds ? ` (within ${(f.window_seconds / 86400).toFixed(f.window_seconds < 86400 ? 2 : 0)} days)` : ''}`],
      ['Satisfied', badge(f.satisfied ? 'yes' : 'no', f.satisfied ? 'ok' : 'warn')], ['Newest source', fmtDay(f.newest_source_at)]]));
  }
  out.push(h('h4', { text: 'Sources' }), (r.sources ?? []).length ? h('ol', { class: 'sources' }, r.sources.map(sourceItem)) : empty('No sources were read.'));
  const k = r.consolidation;
  if (k?.provisional?.length) out.push(h('p', { class: 'msg ok', text: `${k.provisional.length} new provisional knowledge item(s) await your review under Knowledge.` }));
  return out;
}

function sourceFlags(s) {
  const f = s.flags ?? {};
  const out = [];
  if (f.injection?.length) out.push(badge(`injection quarantined (${f.quarantined_sentences ?? 0} sentence${f.quarantined_sentences === 1 ? '' : 's'})`, 'warn'));
  if (f.truncated) out.push(badge('truncated', 'warn'));
  if (s.published_at === null || s.published_at === undefined) out.push(badge('undated', 'neutral'));
  if (f.js_suspected) out.push(badge('needs JavaScript', 'neutral'));
  if (f.hidden_removed) out.push(badge(`${f.hidden_removed} hidden element${f.hidden_removed === 1 ? '' : 's'} removed`, 'neutral'));
  if (s.cited === false) out.push(badge('read, not cited', 'neutral'));
  return out;
}

function sourceItem(s) {
  return h('li', { id: `src-${s.n}`, tabindex: '-1', value: String(s.n), 'data-source': s.n },
    h('p', { class: 'srctitle' }, h('strong', { text: s.title || s.domain || 'Untitled' }), ` · ${s.domain}`),
    h('p', { class: 'srcurl' }, safeLink(s.url)),
    h('p', { class: 'sub', text: `Published ${fmtDay(s.published_at)} · retrieved ${fmtTime(s.retrieved_at)} · ${s.retrieval_mode === 'cache' ? 'from cache' : s.retrieval_mode} · ${s.kind}` }),
    h('p', { class: 'flags' }, sourceFlags(s)));
}

function usageBlock(t) {
  const u = t.usage ?? {};
  const b = t.budget ?? {};
  return h('details', { id: `usage-${t.id}` }, h('summary', { text: 'Budget and usage' }), kv([
    ['Requests', `${u.requests ?? 0} of ${b.max_requests ?? '—'}`], ['Documents', `${u.pages ?? 0} of ${b.max_pages ?? '—'}`],
    ['Search queries', `${u.queries ?? 0} of ${b.max_queries ?? '—'}`], ['Downloaded', fmtBytes(u.bytes ?? 0)],
    ['Time', `${fmtNum(u.seconds ?? 0, 1)} s of ${b.max_seconds ?? '—'} s`]]));
}

function evidenceBlock(t) {
  const slot = h('div', { class: 'slot' }, empty('Open to load the claims extracted from each source.'));
  const details = h('details', { id: `evidence-${t.id}`, class: 'evidence' }, h('summary', { text: 'Evidence: claims per source' }), slot);
  let loaded = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || loaded) return;
    loaded = true;
    try {
      const e = await api('GET', `/api/web/tasks/${t.id}/evidence`);
      const bySource = new Map(e.sources.map((s) => [s.id, { source: s, claims: [] }]));
      const orphans = [];
      for (const c of e.claims) (bySource.get(c.source_id)?.claims ?? orphans).push(c);
      const groups = [...bySource.values()].filter((g) => g.claims.length);
      fill(slot, groups.length || orphans.length ? [
        ...groups.map((g) => h('div', { class: 'evsource' }, h('p', { class: 'srctitle' }, h('strong', { text: g.source.title || g.source.domain }), ` · ${g.source.domain} · `, safeLink(g.source.url)),
          h('ul', { class: 'claims' }, g.claims.map((c) => h('li', {}, h('span', { text: c.text }), ' ',
            badge(c.cluster_status ? c.cluster_status.replace('_', ' ') : c.kind, 'neutral')))))),
        orphans.length ? h('ul', { class: 'claims' }, orphans.map((c) => h('li', { text: c.text }))) : null,
      ] : empty('No claims were extracted.'));
    } catch (error) {
      loaded = false;
      fill(slot, h('p', { class: 'msg error', text: errorText(error) }));
    }
  });
  return details;
}

// A monitor finding (meaningful change) in one line; keys and values are untrusted web text, set as text only.
function findingText(f) {
  const parts = (f.changed_values || []).slice(0, 2).map((c) =>
    `${short(String(c.key), 60)}: ${(c.before || []).join(', ') || '—'} → ${(c.after || []).join(', ') || '—'}`);
  if (f.new_claims?.length) parts.push(`${f.new_claims.length} new corroborated subject${f.new_claims.length === 1 ? '' : 's'}`);
  if (f.superseded?.length) parts.push(`${f.superseded.length} knowledge item${f.superseded.length === 1 ? '' : 's'} superseded`);
  return parts.join('; ') || 'change detected';
}

function monitorFindings(m) {
  const found = m.findings || [];
  if (!found.length) return h('span', { class: 'sub', text: 'none yet' });
  return h('div', { class: 'findings' }, found.slice(0, 3).map((f) => h('div', {},
    h('span', { text: fmtTime(f.at) }), h('br'),
    h('span', { class: 'sub', text: findingText(f) + (f.pending_id ? ` (question #${f.pending_id})` : '') }))));
}

function renderMonitors(monitors) {
  fill($('monitorList'), monitors.length
    ? table('Monitors', ['Topic', 'Mode', 'Every', 'Status', 'Last run', { label: 'Runs', num: true }, 'Recent findings', 'Actions'], monitors.map((m) => [
        h('span', { text: m.topic }), modeLabel(m.mode), `${fmtNum(m.interval_seconds / 3600, 0)} h`, badge(m.status, m.status === 'active' ? 'ok' : m.status === 'proposed' ? 'info' : 'neutral'),
        fmtTime(m.last_run_at), m.runs, monitorFindings(m), { actions: monitorActions(m) }]))
    : empty('No monitors. Add a topic below to re-check it on a schedule.'));
}

function monitorActions(m) {
  const what = short(m.topic, 50);
  const button = (verb, label, cls) => h('button', { type: 'button', id: `mon-${verb}-${m.id}`, class: cls, text: label,
    'aria-label': `${label} monitor: ${what}`, onclick: async (ev) => {
      const control = ev.currentTarget;
      if (verb === 'stop' && !(await confirmAction('Stop this monitor?', `“${what}” will no longer be re-checked. Stopped monitors cannot be restarted; add a new one instead.`, 'Stop monitor'))) return;
      act(control, $('monitorStatus'), async () => {
        const out = await api('POST', `/api/web/monitors/${m.id}/${verb}`, {});
        renderMonitors(await api('GET', '/api/web/monitors'));
        return `Monitor “${what}” is ${out.status}.`;
      });
    } });
  return [m.status === 'active' ? button('pause', 'Pause') : null, m.status === 'paused' ? button('resume', 'Resume') : null,
    m.status !== 'stopped' ? button('stop', 'Stop', 'danger') : null].filter(Boolean);
}

function schedulePoll() {
  clearTimeout(web.timer);
  web.timer = null;
  if (!state.token || state.view !== 'research') return;
  const enabled = web.status?.config?.enabled || web.status?.active;
  if (enabled && (web.tasks.some(isActive) || (web.task && isActive(web.task)))) web.timer = setTimeout(poll, POLL_MS);
}

async function poll() {
  web.timer = null;
  if (web.polling || !state.token || state.view !== 'research') return;
  web.polling = true;
  try {
    await Promise.all([loadTaskList(), web.taskId ? loadTask(web.taskId) : null]);
    if (!web.tasks.some(isActive)) await loadWebStatus();
  } catch {
    // a failed poll is retried on the next tick; errors of explicit actions are reported where they happen
  } finally {
    web.polling = false;
    schedulePoll();
  }
}

// ---- knowledge -------------------------------------------------------------------------------------------

const KNOWLEDGE_TONE = { accepted: 'ok', provisional: 'info', stale: 'warn', archived: 'neutral', rejected: 'neutral', superseded: 'neutral' };

async function loadKnowledge() {
  const topic = $('kTopic').value.trim();
  const out = await load('knowledge', [$('knowledgeList')], () => api('GET', `/api/web/knowledge${topic ? `?topic=${enc(topic)}` : ''}`));
  if (!out) return;
  fill($('knowledgeList'), out.items.length ? h('div', { class: 'grid' }, out.items.map(knowledgeCard))
    : empty(topic ? `Nothing known about “${topic}”. Research it under Research.` : 'Nothing learned from the web yet.'));
}

function knowledgeCard(k) {
  const status = h('p', { class: 'msg', role: 'status' });
  const what = short(k.statement, 60);
  const action = (verb, label, cls, work) => h('button', { type: 'button', id: `k-${verb}-${k.id}`, class: cls, text: label,
    'aria-label': `${label}: ${what}`, onclick: (ev) => work(ev.currentTarget) });
  const run = (control, path, body, text) => act(control, status, async () => {
    const out = await api('POST', `/api/web/knowledge/${k.id}/${path}`, body);
    const message = text(out);
    await Promise.all([loadKnowledge(), loadHeader()]);
    say($('knowledgeStatus'), message);
    return message;
  });
  const live = ['provisional', 'accepted', 'stale'].includes(k.status);
  const buttons = [
    k.status === 'provisional' || k.status === 'stale' ? action('accept', 'Accept', 'primary', (c) => run(c, 'review', { accept: true }, () => `Accepted knowledge #${k.id}.`)) : null,
    k.status === 'provisional' || k.status === 'stale' ? action('reject', 'Reject', null, (c) => run(c, 'review', { accept: false }, () => `Rejected knowledge #${k.id}.`)) : null,
    live ? action('refresh', 'Refresh', null, (c) => run(c, 'refresh', {}, (t) => `Re-checking knowledge #${k.id}: research task queued (${modeLabel(t.mode)}).`)) : null,
    live ? action('archive', 'Archive', null, (c) => run(c, 'archive', {}, () => `Archived knowledge #${k.id}; it is kept for history only.`)) : null,
    action('forget', 'Forget', 'danger', async (c) => {
      if (!(await confirmAction('Forget this knowledge?', `“${what}” and every earlier version of it are deleted from memory. This cannot be undone.`, 'Forget'))) return;
      run(c, 'forget', {}, () => `Forgot knowledge #${k.id} and its version history.`);
    }),
  ].filter(Boolean);
  const superseded = (k.versions ?? []).filter((v) => v.id !== k.id && (v.status === 'superseded' || v.superseded_by));
  return h('article', { class: 'card', 'data-knowledge': k.id, 'aria-labelledby': `k-title-${k.id}` },
    h('h2', { id: `k-title-${k.id}`, class: 'kstatement', text: k.statement }),
    h('p', { class: 'badges' }, badge(k.status, KNOWLEDGE_TONE[k.status] ?? 'neutral'), k.stale && k.status !== 'stale' ? badge('stale', 'warn') : null,
      k.contested ? badge('contested', 'warn') : null, badge(`confidence ${pct(k.confidence)}`, 'neutral'), ` #${k.id} · version ${k.version}`),
    kv([['Freshness class', k.freshness_class], ['Last verified', fmtTime(k.last_verified_at)], ['First verified', fmtTime(k.first_verified_at)],
      k.stale ? ['Why stale', k.stale_reason] : null, k.note ? ['Note', k.note] : null, k.pending_id ? ['Open question', `#${k.pending_id}`] : null]),
    h('h3', { text: 'Sources' }), k.sources.length ? h('ul', { class: 'reasons' }, k.sources.map((s) => h('li', {}, h('span', { text: `${s.title || s.domain} · ${fmtDay(s.published_at)} · ` }), safeLink(s.url))))
      : empty('No sources recorded.'),
    h('details', { id: `k-versions-${k.id}` }, h('summary', { text: `Version history (${(k.versions ?? []).length})` }),
      table('Version history', [{ label: 'Version', num: true }, 'Status', 'Statement', 'Valid from', 'Valid until'],
        (k.versions ?? []).map((v) => [v.version, v.status, h('span', { text: v.statement }), fmtTime(v.valid_from), fmtTime(v.valid_until)]))),
    superseded.length || k.feedback?.length ? h('div', { class: 'changed' }, h('h3', { text: 'What changed' }),
      superseded.length ? h('ul', { class: 'reasons' }, superseded.map((v) => h('li', { text: `Version ${v.version} (${v.status}) said: ${v.statement}` }))) : null,
      k.feedback?.length ? h('ul', { class: 'reasons' }, k.feedback.map((f) => h('li', {
        text: `${fmtTime(f.created_at)}: ${f.effect} (confidence ${pct(f.before_confidence)} → ${pct(f.after_confidence)}, ${fmtNum(f.info_gain ?? 0, 3)} bits)` }))) : null) : null,
    h('div', { class: 'buttons', role: 'group', 'aria-label': `Actions for knowledge #${k.id}` }, buttons), status);
}

// ---- controls ------------------------------------------------------------------------------------------

// A control stays disabled while its own request is in flight, whatever the data says.
function enable(id, allowed) {
  const el = $(id);
  el.disabled = state.busy.has(el) || !allowed;
}

function syncControls() {
  const hasEpoch = !!state.epoch;
  for (const id of ['observeBtn', 'recordDecision', 'forgetEpoch']) enable(id, hasEpoch);
  $('forgetEpoch').textContent = hasEpoch ? `Forget epoch ${state.epoch}` : 'Forget epoch';
  enable('learnBtn', $('learnTrain').value && $('learnValidation').value);
  enable('recallBtn', $('recallDataset').value && state.graphs.some((g) => g.status === 'active'));
  const g = state.graph;
  const candidate = g?.status === 'candidate';
  enable('gPromote', candidate);
  enable('gReject', candidate);
  enable('gRejectReason', candidate);
  enable('gEvaluate', !!g);
  enable('fmapBtn', !!$('fmapDataset').value);
  $('ckPrune').textContent = `Prune (keep ${$('ckKeep').value.trim() || 'N'})`;
  $('ckRebuild').textContent = `Rebuild stale (keep ${$('ckKeep').value.trim() || 'N'})`;
  $('obsRootLabel').textContent = CHANNEL_ROOT[$('obsChannel').value];
  enable('askBtn', !!web.status?.config?.enabled);
}

function wire() {
  $('refreshOverview').addEventListener('click', loadOverview);
  $('refreshTeach').addEventListener('click', loadTeach);
  $('refreshQuestions').addEventListener('click', loadQuestions);
  $('refreshGraphs').addEventListener('click', loadGraphs);
  $('refreshCheckpoints').addEventListener('click', loadCheckpoints);
  $('refreshMemory').addEventListener('click', loadMemory);
  $('epochSelect').addEventListener('change', (e) => chooseEpoch(e.target.value));
  $('evEpoch').addEventListener('change', (e) => chooseEpoch(e.target.value));
  $('obsChannel').addEventListener('change', syncControls);
  $('newId').addEventListener('click', () => { $('obsId').value = newEventId(); });
  $('showResolved').addEventListener('change', loadQuestions);
  $('ckKeep').addEventListener('input', syncControls);
  $('ckPrune').addEventListener('click', (ev) => pruneCheckpoints(ev.currentTarget));
  $('ckRebuild').addEventListener('click', (ev) => rebuildCheckpoints(ev.currentTarget));
  $('ckKeepForm').addEventListener('submit', (ev) => ev.preventDefault());
  $('fmapDataset').addEventListener('change', syncControls);
  $('learnContext').addEventListener('change', renderLearnOptions);
  $('obsId').value = newEventId();

  $('epochForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const name = $('epochName').value.trim();
    if (!name) { say($('epochStatus'), 'Name the epoch first.', 'error'); $('epochName').focus(); return; }
    const note = $('epochNote').value.trim(), context = $('epochContext').value.trim();
    act($('epochStart'), $('epochStatus'), async () => {
      const result = await api('POST', '/api/epochs', { name, ...(note ? { note } : {}), ...(context ? { context } : {}) });
      state.epoch = result.epoch;
      $('epochName').value = '';
      $('epochNote').value = '';
      $('epochContext').value = '';
      await Promise.all([loadTeach(), loadHeader()]);
      return result.created ? `Started epoch ${result.epoch}.` : `Epoch ${result.epoch} already exists; selected it.`;
    });
  });

  $('observeForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const root = $('obsRoot').value.trim(), id = $('obsId').value.trim();
    if (!state.epoch) { say($('observeStatus'), 'Start or choose an epoch first.', 'error'); return; }
    if (!root) { say($('observeStatus'), 'Enter the root that produced this reading.', 'error'); $('obsRoot').focus(); return; }
    if (!id) { say($('observeStatus'), 'An event id is required; use “New id”.', 'error'); $('obsId').focus(); return; }
    const obs = { id, epoch: state.epoch, channel: $('obsChannel').value, root,
                  action: Number(document.querySelector('input[name=action]:checked')?.value ?? 0),
                  bit: Number(document.querySelector('input[name=bit]:checked')?.value ?? 0) };
    const sample = $('obsSample').value.trim();
    if (sample) obs.sample_id = sample;
    if ($('obsMemoryOnly').checked) obs.consent = 'memory_only';
    act($('observeBtn'), $('observeStatus'), async () => {
      const result = await api('POST', '/api/observe', obs);
      if (result.status === 'created') $('obsId').value = newEventId();
      await Promise.all([loadTeach(), loadHeader()]);
      return result.status === 'created'
        ? `Recorded ${id} as evidence #${result.seq} (${obs.channel} ${root}, action ${obs.action} → reading ${obs.bit})` +
          (result.belief_revision ? `; beliefs changed (revision #${result.belief_revision}).` : '; beliefs kept their MAP reading.')
        : `Duplicate of evidence #${result.seq}: the same id and payload were already stored, nothing changed.`;
    });
  });

  $('recordDecision').addEventListener('click', (ev) => {
    act(ev.currentTarget, $('decisionStatus'), async () => {
      const epoch = state.epoch;
      const d = await api('POST', `/api/epochs/${enc(epoch)}/decide`);
      await Promise.all([loadTeach(), loadHeader()]);
      renderDecision(d, true);
      const closed = d.closed_pending?.length ? ` Evidence settled question(s) ${d.closed_pending.map((i) => `#${i}`).join(', ')}, now closed.` : '';
      return `Recorded decision “${d.decision.replace('_', ' ')}” for ${epoch}` + (d.pending_id ? `; question #${d.pending_id} is open under Questions.` : '.') + closed;
    });
  });

  $('evFilter').addEventListener('submit', (ev) => { ev.preventDefault(); loadEvidence(); });

  $('forgetEpoch').addEventListener('click', async (ev) => {
    const button = ev.currentTarget, epoch = state.epoch;
    const ok = await confirmAction(`Forget every evidence row of epoch ${epoch}?`,
      'All camera, word and reference evidence of this epoch is deleted and tombstoned. Beliefs return to their priors, ' +
      'open questions of the epoch are superseded and checkpoints that contain the rows become stale. The epoch name stays.', 'Forget epoch');
    if (!ok) return;
    act(button, $('evidenceStatus'), async () => {
      const result = await api('POST', '/api/forget', { kind: 'epoch', target: epoch });
      await Promise.all([loadEvidence(), loadHeader()]);
      return `Forgot epoch ${epoch}: ${forgetSummary(result)}`;
    });
  });

  $('forgetPartnerForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const root = $('partnerRoot').value.trim();
    if (!root) { say($('evidenceStatus'), 'Enter the partner root to forget.', 'error'); $('partnerRoot').focus(); return; }
    const ok = await confirmAction(`Forget partner ${root}?`,
      `Every word evidence row from ${root}, in every epoch, is deleted and tombstoned. Beliefs about this partner's convention are dropped.`, 'Forget partner');
    if (!ok) return;
    act($('forgetPartner'), $('evidenceStatus'), async () => {
      const result = await api('POST', '/api/forget', { kind: 'partner', target: root });
      $('partnerRoot').value = '';
      await Promise.all([loadEvidence(), loadHeader()]);
      return `Forgot partner ${root}: ${forgetSummary(result)}`;
    });
  });

  $('simForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const context = $('simContext').value.trim(), seedText = $('simSeed').value.trim();
    const seed = Number(seedText);
    if (!context) { say($('simStatus'), 'Name the context first.', 'error'); return; }
    if (!/^\d+$/.test(seedText) || !Number.isSafeInteger(seed)) { say($('simStatus'), 'The seed must be a non-negative whole number.', 'error'); return; }
    act($('simBtn'), $('simStatus'), async () => {
      const ids = await api('POST', '/api/datasets/simulate', { context, seed, world: $('simWorld').value });
      $('learnContext').value = context;
      await Promise.all([loadGraphs(), loadHeader()]);
      $('learnContext').value = context;
      renderLearnOptions();
      return `Simulated context ${context}: train #${ids.train}, validation #${ids.validation}, held-out #${ids.heldout}.`;
    });
  });

  $('learnForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const body = { context: $('learnContext').value, train: Number($('learnTrain').value), validation: Number($('learnValidation').value) };
    act($('learnBtn'), $('learnStatus'), async () => {
      const result = await api('POST', '/api/graphs/learn', body);
      state.selectedGraph = result.revision_id;
      await Promise.all([loadGraphs(), loadHeader()]);
      return `Learned candidate revision #${result.revision_id} for ${body.context}. Inspect it under Revision detail, then run the promotion gate.`;
    });
  });

  $('gEvaluate').addEventListener('click', (ev) => {
    const id = state.graph.id;
    act(ev.currentTarget, $('graphStatus'), async () => {
      const result = await api('POST', `/api/graphs/${id}/evaluate`);
      await Promise.all([loadGraphs(), loadHeader()]);
      return result.heldout_mse === null
        ? `Revision #${id} has no independent held-out data to evaluate on.`
        : `Revision #${id} held-out MSE ${fmtNum(result.heldout_mse)} on ${result.heldout.length} dataset(s).`;
    });
  });

  $('gPromote').addEventListener('click', (ev) => {
    const id = state.graph.id;
    act(ev.currentTarget, $('graphStatus'), async () => {
      const result = await api('POST', `/api/graphs/${id}/promote`);
      await Promise.all([loadGraphs(), loadHeader()]);
      return result.promoted
        ? `Gate passed: revision #${id} is now the active graph of ${result.decision.context}.`
        : `Gate rejected revision #${id}: failed ${result.decision.failed.join(', ')}.`;
    });
  });

  $('gRejectForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const id = state.graph.id, reason = $('gRejectReason').value.trim();
    if (!reason) { say($('graphStatus'), 'Give a reason for the rejection; it is kept in the decision record.', 'error'); $('gRejectReason').focus(); return; }
    act($('gReject'), $('graphStatus'), async () => {
      await api('POST', `/api/graphs/${id}/reject`, { reason });
      $('gRejectReason').value = '';
      await Promise.all([loadGraphs(), loadHeader()]);
      return `Rejected revision #${id}: ${reason}`;
    });
  });

  $('recallForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const datasetId = Number($('recallDataset').value);
    const contexts = [...$('recallContexts').querySelectorAll('input:checked')].map((i) => i.value);
    act($('recallBtn'), $('recallStatus'), async () => {
      const r = await api('POST', '/api/recall', contexts.length ? { dataset_id: datasetId, contexts } : { dataset_id: datasetId });
      renderRecall(r);
      await loadHeader();
      return `Recall verdict on dataset #${datasetId}: ${r.verdict}` + (r.pending_id ? `; question #${r.pending_id} opened for a new context.` : '.');
    });
  });

  $('fmapForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const lagsText = $('fmapLags').value.trim(), topText = $('fmapTop').value.trim();
    if (!/^\d+(\s*,\s*\d+)*$/.test(lagsText) || lagsText.split(',').some((x) => Number(x) < 1)) { say($('fmapStatus'), 'Lags are whole numbers of at least 1, separated by commas.', 'error'); $('fmapLags').focus(); return; }
    if (!/^\d+$/.test(topText) || Number(topText) < 1) { say($('fmapStatus'), 'Routes to list must be a whole number of at least 1.', 'error'); $('fmapTop').focus(); return; }
    const body = { dataset_id: Number($('fmapDataset').value), lags: lagsText.split(',').map(Number), top: Number(topText) };
    act($('fmapBtn'), $('fmapStatus'), async () => {
      const r = await api('POST', '/api/graphs/coactivation', body);
      renderFunctionalMap(r);
      await loadHeader();
      return `Functional map of dataset #${body.dataset_id}: ${r.routes.length} proposed route(s). Coactivation is not connection.`;
    });
  });

  $('ckForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    act($('ckCreate'), $('ckStatus'), async () => {
      const c = await api('POST', '/api/checkpoints', { policy: $('ckPolicy').value });
      await Promise.all([loadCheckpoints(), loadHeader()]);
      return `Checkpoint ${c.id} created (${fmtBytes(c.bytes)}, ${c.policy}) at state version ${c.state_version}.`;
    });
  });

  wireResearch();
  window.addEventListener('hashchange', () => { if (!takeToken()) route(true); });
}

const SWITCH_LABELS = { enabled: 'Web access', autonomous: 'Autonomous research', pocket_research: 'Pocket research',
                        browser_rendering: 'JavaScript rendering' };

function wireResearch() {
  $('refreshResearch').addEventListener('click', loadResearch);
  $('refreshKnowledge').addEventListener('click', loadKnowledge);
  $('webEnable').addEventListener('click', (ev) => act(ev.currentTarget, $('webSettingsStatus'), async () => {
    await api('POST', '/api/web/config', { enabled: true });
    await loadWebStatus();
    $('askObjective').focus();
    return 'Web access is on. Research tasks may now contact the public web.';
  }));
  for (const box of $('webSwitches').querySelectorAll('input[data-switch]')) {
    box.addEventListener('change', (ev) => {
      const control = ev.currentTarget;
      const key = control.dataset.switch;
      const value = control.checked;
      act(control, $('webSettingsStatus'), async () => {
        try {
          await api('POST', '/api/web/config', { [key]: value });
        } catch (error) {
          control.checked = !value;
          throw error;
        }
        await loadWebStatus();
        schedulePoll();
        return `${SWITCH_LABELS[key]} is ${value ? 'on' : 'off'}.`;
      });
    });
  }
  $('askForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const objective = $('askObjective').value.trim();
    if (!objective) { say($('askStatus'), 'Write a question or objective first.', 'error'); $('askObjective').focus(); return; }
    act($('askBtn'), $('askStatus'), async () => {
      const t = await api('POST', '/api/web/tasks', { objective, mode: $('askMode').value || 'auto' });
      $('askObjective').value = '';
      web.taskId = t.id;
      web.taskSig = '';
      await refreshTasks();
      return `Research queued (${modeLabel(t.mode)}). Its progress and report appear under Report.`;
    });
  });
  $('taskFilter').addEventListener('submit', (ev) => ev.preventDefault());
  $('taskStatus').addEventListener('change', () => loadTaskList().catch((error) => say($('taskStatusMsg'), errorText(error), 'error')));
  $('taskText').addEventListener('input', renderTaskList);
  $('monitorForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const topic = $('monTopic').value.trim();
    const hours = Number($('monHours').value);
    if (!topic) { say($('monitorStatus'), 'Enter a topic to monitor.', 'error'); $('monTopic').focus(); return; }
    if (!Number.isFinite(hours) || hours < 1) { say($('monitorStatus'), 'The interval must be at least 1 hour.', 'error'); $('monHours').focus(); return; }
    act($('monitorBtn'), $('monitorStatus'), async () => {
      const m = await api('POST', '/api/web/monitors', { topic, interval_hours: hours, mode: $('monMode').value || 'continuous' });
      $('monTopic').value = '';
      renderMonitors(await api('GET', '/api/web/monitors'));
      return `Monitoring “${short(m.topic, 60)}” every ${fmtNum(m.interval_seconds / 3600, 0)} h.`;
    });
  });
  $('kSearch').addEventListener('submit', (ev) => { ev.preventDefault(); loadKnowledge(); });
}

function renderRecall(r) {
  fill($('recallResult'),
    h('p', { class: 'verdict', id: 'recallVerdict', text: r.verdict.startsWith('known:') ? `known context: ${r.verdict.slice(6)}` : 'unknown context' }),
    kv([['Steps replayed', r.steps], ['Selected-graph MSE', fmtNum(r.selected_mse)], ['Latest-graph-only MSE', fmtNum(r.latest_only_mse)],
      ['Unknown fraction', `${(r.unknown_fraction * 100).toFixed(1)}% of steps`], ['Scoring rule', `${r.rule_version} (history ${r.history}, unknown ratio ${r.unknown_context_ratio})`],
      ['Question opened', r.pending_id ? `#${r.pending_id}` : 'none']]),
    table('Choice segments', ['Context', { label: 'Revision', num: true }, { label: 'From step', num: true }, { label: 'To step', num: true }],
      r.choices.map((c) => [c.context, c.revision_id ?? '—', c.from, c.to])));
}

// ---- routing and session ----------------------------------------------------------------------------------

function lock(text) {
  state.token = null;
  $('lockedText').textContent = text;
  for (const id of VIEWS) $(id).hidden = true;
  $('nav').hidden = true;
  $('locked').hidden = false;
}

function takeToken() {
  const match = /^#token=([A-Za-z0-9_-]{16,128})$/.exec(location.hash);
  if (!match) return false;
  state.token = match[1];
  history.replaceState(null, '', location.pathname + location.search);
  start();
  return true;
}

const LOADERS = { overview: loadOverview, teach: loadTeach, evidence: loadEvidence, questions: loadQuestions,
                  research: loadResearch, knowledge: loadKnowledge, graphs: loadGraphs, checkpoints: loadCheckpoints,
                  memory: loadMemory };

function refreshView() { if (state.token) LOADERS[state.view]?.(); }

function route(moveFocus) {
  if (!state.token) return;
  const name = location.hash.slice(1);
  const view = VIEWS.includes(name) ? name : 'overview';
  state.view = view;
  document.documentElement.dataset.view = view;
  $('locked').hidden = true;
  $('nav').hidden = false;
  for (const id of VIEWS) $(id).hidden = id !== view;
  for (const a of document.querySelectorAll('nav a')) {
    if (a.dataset.view === view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  if (moveFocus) $(`h-${view}`).focus();
  schedulePoll();
  refreshView();
}

async function start() {
  try {
    await loadHeader();
  } catch (error) {
    if (state.token) lock(`The console could not load this individual: ${errorText(error)}`);
    return;
  }
  route(false);
}

wire();
syncControls();
track(0);
takeToken(); // without a token the locked notice stays visible and nothing is requested
