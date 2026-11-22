// NeuroSciFi owner console. Vanilla ES module, no framework, CSP-compatible (no inline code or styles in markup).
// The session token arrives in the URL fragment, is kept in this module only and removed from the address bar.

const VIEWS = ['overview', 'teach', 'evidence', 'questions', 'research', 'autonomy', 'knowledge', 'graphs', 'checkpoints',
               'memory', 'voice'];
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
const MAX_TEXT_BODY = 8192;   // the server's limit for research questions and monitor topics (2000 characters)
const TEXT_ROUTES = ['/api/web/tasks', '/api/web/monitors'];
const MATCH_LAST = 12;        // beliefs.MATCH_EVENTS: the executive's match question is asked only this early
const MEMORY_KINDS = ['evidence', 'belief', 'graph', 'dataset', 'checkpoint', 'deletion', 'decision', 'pending',
                      'research', 'claim', 'knowledge', 'source', 'monitor'];

const state = { token: null, view: null, epoch: '', epochs: [], semantics: null, datasets: [], graphs: [],
                selectedGraph: null, graph: null, seq: {}, busy: new Set(), offline: false };
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
  for (const kid of kids.flat(Infinity)) {
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
// Tables of three or more columns stack into labelled rows below 640px (CSS uses data-label), so row actions stay
// on screen; the explicit roles keep the table semantics when CSS changes the display of its parts.
function table(caption, headers, rows, { showCaption = false } = {}) {
  const label = (col) => col.label ?? col;
  const head = h('tr', { role: 'row' }, headers.map((col) => h('th', { scope: 'col', role: 'columnheader', class: col.num ? 'num' : null, text: label(col) })));
  const body = rows.map((cells) => h('tr', { role: 'row' }, cells.map((cell, i) => {
    const cls = [headers[i]?.num ? 'num' : null, cell?.actions ? 'actions' : null].filter(Boolean).join(' ') || null;
    const content = cell?.actions ? cell.actions : cell;
    return h('td', { class: cls, role: 'cell', 'data-label': headers[i] ? label(headers[i]) : null },
      Array.isArray(content) ? h('span', { class: 'cellgroup' }, content) : content instanceof Node ? content : String(content ?? '—'));
  })));
  const cls = [headers.length >= 5 ? 'many' : null, headers.length >= 3 ? 'stack' : null].filter(Boolean).join(' ') || null;
  const t = h('table', { class: cls, role: 'table' }, h('caption', { class: showCaption ? null : 'vh', text: caption }),
    h('thead', { role: 'rowgroup' }, head), h('tbody', { role: 'rowgroup' }, body));
  return h('div', { class: 'tablewrap', tabindex: '0', role: 'region', 'aria-label': caption }, t);
}

// Plain words first, the numbers behind them in a disclosure.
function technical(...kids) {
  return h('details', { class: 'tech' }, h('summary', { text: 'Technical detail' }), ...kids);
}

// Message text may name commands in `backticks`: they are shown as <code>, never as literal backticks.
function rich(text) {
  return String(text ?? '').split(/`([^`]+)`/).map((part, i) => (i % 2 ? h('code', { text: part }) : part)).filter((x) => x !== '');
}

function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).flatMap(([k, v]) => [h('dt', { text: k }), h('dd', {}, v instanceof Node ? v : String(v ?? '—'))]));
}

function badge(text, tone) { return h('span', { class: `badge ${tone}`, text }); }

function fmtTime(t) {
  if (t === null || t === undefined) return '—';
  return new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

// Relative age computed in this page when it renders (server text like "3 days ago" would freeze).
function ago(t) {
  if (t === null || t === undefined) return 'undated';
  const s = Date.now() / 1000 - t;
  if (s < 0) return 'in the future';
  if (s < 3600) return 'within the last hour';
  if (s < 86400) { const n = Math.floor(s / 3600); return `${n} hour${n === 1 ? '' : 's'} ago`; }
  const d = Math.floor(s / 86400);
  if (d < 60) return d === 1 ? 'yesterday' : `${d} days ago`;
  const m = Math.floor(d / 30.4);
  return m < 24 ? `${m} months ago` : `${Math.floor(d / 365.25)} years ago`;
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
  el.replaceChildren(...rich(text));
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
  el.replaceChildren(...rich(text));
  el.className = `toast ${tone}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.textContent = ''; }, 6000);
}

const WEB_OFF_TEXT = 'Web access is off for this individual, so nothing was sent. Turn it on with “Turn on web access” to research.';
const isWebOff = (error) => error instanceof ApiError && error.status === 409 && /web access is (disabled|off)/i.test(error.message);

function errorText(error) {
  if (!(error instanceof ApiError)) return 'Unexpected error in the console page.';
  if (isWebOff(error)) return WEB_OFF_TEXT;
  if (error.status === 409 && /allowance is spent/i.test(error.message)) {
    return 'Today’s allowance for autonomous research is used up, so this proposal cannot start now. ' +
      'Approve it again tomorrow, or raise the daily allowance with `nsf web config`.';
  }
  if (error.status === 507) {
    const reason = error.message.replace(/^(web memory )?writes are paused:\s*/i, '');
    return `Writes are paused by regulation (${reason}), so nothing was saved. Free disk space, forget evidence or raise the limit, then try again.`;
  }
  return error.status ? `${error.message} (HTTP ${error.status})` : error.message;
}

// Side effects of an error the page can act on: web access turned off elsewhere, writes paused, server gone.
function reactTo(error) {
  if (!(error instanceof ApiError)) return;
  if (isWebOff(error)) showWebOff();
  if (error.status === 507) loadHeader().catch(() => {});
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
    const limit = TEXT_ROUTES.includes(path) ? MAX_TEXT_BODY : MAX_BODY;
    if (new TextEncoder().encode(options.body).length > limit) throw new ApiError(0, `Request is larger than ${limit} bytes; shorten the text fields.`);
    options.headers['Content-Type'] = 'application/json';
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    setOffline(true);
    throw new ApiError(0, OFFLINE_TEXT);
  }
  if (state.offline) setOffline(false);
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    if (response.status === 403) lock('The session token was rejected. Open the URL printed by `nsf console` (or the one in its URL file) again; each start of the console issues a new token.');
    throw new ApiError(response.status, data?.error ?? response.statusText);
  }
  return data;
}

const enc = encodeURIComponent;

// ---- server stopped ------------------------------------------------------------------------------------

const OFFLINE_TEXT = 'Server stopped. Restart `nsf console` and open the new URL it prints.';

// One banner instead of an error in every slot; controls that change anything are disabled until the server answers.
function setOffline(on) {
  if (state.offline === on) return;
  state.offline = on;
  $('offline').hidden = !on;
  document.documentElement.dataset.offline = on ? '1' : '0';
  if (on) { $('writes').textContent = 'unknown'; $('writes').className = 'badge neutral'; $('writesWhy').textContent = ''; }
  syncControls();
  if (!on) refreshView();
}

function applyOffline() {
  for (const el of document.querySelectorAll('#main :is(button, input, select, textarea)')) {
    if (el.dataset.keep !== undefined) continue;   // Refresh buttons stay usable: they are how the owner retries
    if (state.offline) {
      if (el.dataset.offlineDisabled === undefined) el.dataset.offlineDisabled = el.disabled ? '1' : '0';
      el.disabled = true;
    } else if (el.dataset.offlineDisabled !== undefined) {
      el.disabled = el.dataset.offlineDisabled === '1';
      delete el.dataset.offlineDisabled;
    }
  }
}

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
    reactTo(error);
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
    if (ticket !== state.seq[key]) return undefined;
    if (state.offline) {   // keep what is shown; the banner says why nothing updates
      for (const slot of slots) { slot.removeAttribute('aria-busy'); if (slot.querySelector(':scope > .empty:only-child')?.textContent === 'Loading…') fill(slot, empty('Not loaded: the server stopped.')); }
    } else {
      for (const slot of slots) fill(slot, h('p', { class: 'msg error' }, rich(errorText(error))));
    }
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
  writes.textContent = inv.regulation.writes_allowed ? 'allowed' : 'paused';
  writes.className = `badge ${inv.regulation.writes_allowed ? 'ok' : 'warn'}`;
  $('writesWhy').textContent = inv.regulation.writes_allowed ? '' : inv.regulation.reason ?? '';
  const open = inv.memory.open_questions;
  $('openCount').textContent = open ? `${open} open` : '';
  if (inv.web) $('activeCount').textContent = inv.web.open_tasks ? `${inv.web.open_tasks} active` : '';
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
    r.reason ? ` · ${r.reason}` : ''),
    r.writes_allowed ? null : h('p', { class: 'sub', text: 'New evidence, research and other writes are refused until this clears. Free disk space, forget evidence or raise the limit.' }),
    kv([
    ['Evidence rows', `${r.evidence_rows} of ${r.config.max_evidence_rows}`],
    ['Open questions', r.open_questions],
    ['Evidence written', r.write_volume],
    ['Evidence lifetime', `${(r.config.evidence_ttl_seconds / 86400).toFixed(1)} days unless promoted`],
    ['Free-space reserve', fmtBytes(r.config.min_free_bytes)],
    ['Ask budget per epoch', r.config.ask_budget_per_epoch],
  ]));
  const mem = inv.memory;
  fill($('inventory'), table('Memory inventory', ['Record', { label: 'Count', num: true }],
    Object.entries(mem).map(([k, v]) => [k.replaceAll('_', ' '), v])),
    inv.web ? [h('h3', { text: 'Web memory' }), table('Web memory inventory', ['Record', { label: 'Count', num: true }],
      Object.entries(inv.web).map(([k, v]) => [WEB_INVENTORY[k] ?? k.replaceAll('_', ' '), v]))] : null);
  const dev = Object.entries(inv.development);
  fill($('development'), dev.length
    ? table('Development counters', ['Counter', { label: 'Value', num: true }], dev.map(([k, v]) => [k.replaceAll('_', ' '), v]))
    : empty('No development yet: record evidence or run a graph job.'));
}

const WEB_INVENTORY = { tasks: 'research tasks', open_tasks: 'research tasks still running or queued', sources: 'sources read',
  claims: 'claims extracted', knowledge: 'knowledge items (all versions)', accepted_knowledge: 'accepted knowledge',
  provisional_knowledge: 'knowledge awaiting review', entities: 'entities', monitors: 'monitors',
  predictions_open: 'open predictions', feedback: 'knowledge feedback records' };

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

// "Reads as declared, 99% sure" instead of P(inverted) = 0.010.
function plainBelief(p) {
  if (p <= 0.13) return `Reads as declared (${Math.round((1 - p) * 100)}% sure).`;
  if (p >= 0.87) return `Reads inverted (${Math.round(p * 100)}% sure).`;
  return `Not settled yet: either reading still fits (${Math.round((1 - p) * 100)}% as declared).`;
}

function probCard(label, scope, p, identifiable, detail, suspect) {
  const bar = h('div', { class: 'bar', 'aria-hidden': 'true' }, h('span', { class: 'zone' }), h('span', { class: 'fill' }), h('span', { class: 'mid' }));
  bar.querySelector('.fill').style.width = `${(p * 100).toFixed(1)}%`;
  return h('div', { class: 'prob', 'data-scope': scope },
    h('div', { class: 'probhead' }, h('span', { class: 'probname', text: label }),
      badge(identifiable ? 'Identifiable' : 'Unidentifiable', identifiable ? 'ok' : 'warn')),
    suspect?.suspected_change ? h('p', { class: 'suspect' }, badge('change suspected', 'warn'),
      ` since evidence #${suspect.suspected_since_seq}${suspect.ambiguous ? ', ambiguous' : ''}`) : null,
    bar,
    h('p', { class: 'plain', text: plainBelief(p) }),
    technical(h('p', { class: 'probnum', text: `P(inverted) = ${p.toFixed(3)} · P(as declared) = ${(1 - p).toFixed(3)}${detail}` }),
      suspect?.suspected_change ? h('p', { class: 'sub', text: `Change score (log-likelihood ratio): ${suspect.llr.toFixed(2)}` }) : null));
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
    h('p', { class: 'sub', text: 'A bar at the left means the factor reads as declared, at the right inverted. Inside the hatched band the evidence cannot tell yet (unidentifiable).' }),
    b.unidentifiable.length
      ? [h('h3', { text: 'Why some factors are unidentifiable' }), h('ul', { class: 'reasons', id: 'unidentifiable' },
          b.unidentifiable.map((u) => h('li', {}, h('strong', { text: `${scopeName(u.scope)}: ` }), u.why)))]
      : h('p', { id: 'unidentifiable', text: 'Every factor is identifiable from the current evidence.' }));
}

function scopeName(scope) {
  if (scope === 'mechanism') return 'Mechanism';
  const [kind, ...root] = scope.split(':');
  return kind === 'camera' ? `Camera ${root.join(':')}` : kind === 'partner' ? `Partner ${root.join(':')}` : scope;
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
    h('p', { id: 'matchBest' }, `This epoch's last ${m.events} learning reading(s) look like `, h('strong', { text: m.best }),
      ` (${Math.round(m.best_posterior * 100)}% likely; a new setup: ${Math.round(m.new_setup_posterior * 100)}%).`),
    h('p', { class: 'sub', id: 'matchRule', text: `The executive asks whether to continue that calibration only within this epoch's first ${MATCH_LAST} learning readings, ` +
      `and only when the best earlier setup is at least ${Math.round(m.ask_posterior * 100)}% likely (3:1 against everything else). Later readings never trigger this question.` }),
    technical(table('Earlier epochs ranked by fit', ['Epoch', 'Segments', { label: 'Log-likelihood', num: true }, { label: 'Posterior', num: true }, { label: 'Readings', num: true }],
      m.ranking.map((r) => [r.epoch, Object.values(r.segments).join(', '), r.log_likelihood.toFixed(2), r.posterior.toFixed(3), r.events])),
      h('p', { class: 'sub', text: `Posterior of a new setup: ${m.new_setup_posterior.toFixed(3)}; asking threshold ${m.ask_posterior}.` })));
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
    parts.push(h('p', { class: 'plain', text: `${scopeName(c.scope)} seems to have flipped: its readings since evidence #${c.since_seq} fit the opposite mapping much better than the old one.` +
      (c.ambiguous ? ' Another factor explains the readings almost as well, so it is not yet clear which one changed.' : '') }));
    parts.push(kv([['Scope', h('code', { id: 'changeScope', text: c.scope })], ['Since', `evidence #${c.since_seq}`]]));
    parts.push(technical(kv([['Penalised log-likelihood gain', `${c.llr.toFixed(2)} nats (threshold ${threshold.toFixed(2)})`],
      ['Ambiguous', c.ambiguous ? 'yes: the runner-up is within 1 nat' : 'no']])));
    const after = events.filter((e) => e.seq >= c.since_seq && inScope(e, c.scope));
    parts.push(h('h3', { text: `Experience behind the change (${after.length})` }),
      h('p', { class: 'sub', text: `${c.scope} readings from evidence #${c.since_seq} on, which fit a flipped mapping better than the old one.` }),
      h('ul', { class: 'events', id: 'changeEvents', tabindex: '0', 'aria-label': 'Readings after the changepoint' }, after.slice().reverse().map(eventLine)));
  } else {
    parts.push(h('p', {}, badge('No change suspected', 'ok')));
    parts.push(h('p', { class: 'plain', text: 'Every factor still reads the way it did: no flip explains the recent readings better.' }));
    parts.push(technical(kv([['Best gain', `${c.llr.toFixed(2)} nats (threshold ${threshold.toFixed(2)})`]])));
  }
  if (c.window) {
    parts.push(h('p', { class: 'sub', text: `Tested ${c.tried} changepoints over the last ${c.window} learning events.` }));
    if (c.alternatives.length) parts.push(technical(table('Alternative scopes', ['Scope', { label: 'Gain (nats)', num: true }], c.alternatives.map((a) => [a.scope, a.llr.toFixed(2)]))));
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
    h('div', { class: 'factorhead' }, h('h3', { text: scopeName(f.scope) }), h('span', { class: 'sub', text: plainBelief(f.p_inverted) })),
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
  if (e.derived_deleted?.pending) parts.push(`${e.derived_deleted.pending} question(s) of the epoch deleted`);
  if (e.checkpoints_stale?.length) parts.push(`checkpoint(s) ${e.checkpoints_stale.join(', ')} now stale`);
  if (result.failure) parts.push(`warning: ${result.failure}`);
  const next = (result.next_steps ?? []).map((x) => `${cap(x).replace(/\.?$/, '.')}`).join(' ');
  return parts.join('; ') + '.' + (next ? ` ${next}` : '');
}

async function forgetEvidence(button, e) {
  const ok = await confirmAction(`Forget evidence ${e.id}?`,
    `The ${e.channel} reading from ${e.root} (action ${e.action} → reading ${e.bit}) is deleted. Only a payload-free tombstone remains, ` +
    'so this id can never be recorded again. Beliefs are recomputed without it and checkpoints that contain it become stale. ' +
    'Open questions stay open for now: the executive closes a question whose premise disappeared at its next recorded decision.', 'Forget evidence');
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
    const group = (...buttons) => h('div', { class: 'buttons', role: 'group', 'aria-label': `Quick answers to question ${q.id}` }, buttons);
    const web = webScope(q.scope);
    const parts = questionParts(q);
    let shortcuts = null;
    if (open && change) {
      shortcuts = group(quick('yes', 'Yes, it changed — relearn only that', { resolution: `Confirmed: ${q.scope} changed`, confirm: true }),
        quick('no', 'No, nothing changed', { resolution: `Rejected: ${q.scope} did not change`, confirm: false }));
    } else if (open && epochTarget) {
      shortcuts = group(quick('continue', 'Continue there', { resolution: `Continue with the calibration of epoch ${epochTarget}`, confirm: true }),
        quick('new', 'No, this is new', { resolution: `This is a new setup, not epoch ${epochTarget}`, confirm: false }));
    } else if (open && web.kind === 'knowledge') {
      shortcuts = group(quick('accept', 'Accept', { resolution: 'Accepted by the owner', confirm: true }),
        quick('reject', 'Reject', { resolution: 'Rejected by the owner', confirm: false }));
    } else if (open && (web.kind === 'monitor' || web.kind === 'research')) {
      shortcuts = group(quick('approve', 'Approve', { resolution: 'Approved by the owner', confirm: true }),
        quick('decline', 'Decline', { resolution: 'Declined by the owner', confirm: false }));
    } else if (open && web.kind === 'contested') {
      const values = contestedValues(q);
      if (values.length) shortcuts = group(...values.map((v, i) => quick(`value${i}`, `${v} holds`, { resolution: v })));
    }
    return h('article', { class: 'card', 'data-pending': q.id, 'aria-labelledby': `q-title-${q.id}` },
      h('h2', { id: `q-title-${q.id}`, class: 'qhead', tabindex: '-1' },
        badge(q.resolved_at ? 'Resolved' : 'Open', q.resolved_at ? 'neutral' : 'info'), ` #${q.id} · ${q.kind.replace('_', ' ')} · ${web.label ?? q.scope}`),
      parts.body,
      kv([['Epoch', q.epoch ?? '—'], q.context ? ['Context', q.context] : null, ['Asked', fmtTime(q.created_at)],
        q.resolved_at ? ['Answered', `${fmtTime(q.resolved_at)}: ${q.resolution}`] : null]),
      web.label || parts.detail.length ? technical(kv([web.label ? ['Scope', h('code', { text: q.scope })] : null, ...parts.detail])) : null,
      shortcuts, q.resolved_at ? null : form, status);
  })));
}

// Web Cortex question scopes in words (raw scopes such as web:contested:<uuid>:3 go under Technical detail).
function webScope(scope) {
  const [head, kind, ...rest] = scope.split(':');
  if (head !== 'web') return { kind: null, label: scope.startsWith('epoch:') ? `earlier setup ${scope.slice(6)}` : null };
  const id = rest.join(':');
  const label = { knowledge: `knowledge #${id}`, contested: 'sources disagree', research: 'research proposal',
                  monitor: `monitor #${id}`, finding: 'monitor finding', review: 'research review' }[kind] ?? 'web question';
  return { kind, label };
}

// The values a contested question offers. Newest form first: a final machine-readable line "values: A | B", then the
// prose "one says A, another says B", then the older "(different values: A vs B)"; an explicit q.values wins (RT-B-07).
function contestedValues(q) {
  if (Array.isArray(q.values) && q.values.length) return q.values.map(String).slice(0, 4);
  const text = q.question ?? '';
  const unquote = (v) => v.trim().replace(/^(['"“‘])(.*)(['"”’])$/, '$2').trim();
  const lastLine = text.trimEnd().split('\n').pop();
  let values = [];
  const marker = /^values:\s*(.+)$/i.exec(lastLine);
  if (marker) values = marker[1].split('|').map(unquote);
  else {
    const says = /one says (.+?), another says (.+?)(?:\.(?=\s|$)|;|\)|$)/.exec(text);
    if (says) values = [says[1], says[2]].map(unquote);
    else {
      const m = /different (?:values|versions|dates|years): ([^;)]+)/.exec(text);
      if (m) values = m[1].split(' vs ').map((v) => v.trim());
    }
  }
  values = values.filter(Boolean);
  return values.length >= 2 && values.length <= 4 && values.every((v) => v.length <= 80) ? values : [];
}

// The question in a plain sentence (U-14, U-28): "(gain 8.73 nats)" numbers, a stemmed index key and the machine-readable
// "values:" line move under Technical detail; `commands` become <code>.
function questionParts(q) {
  let text = q.question ?? '';
  const detail = [];
  text = text.replace(/\s*\((?:penalised )?(?:gain|llr|log-likelihood)[^)]*\)/gi, (m) => { detail.push(['Evidence strength', m.trim().replace(/^\(|\)$/g, '')]); return ''; });
  text = text.replace(/(found a meaningful change: )«([a-z0-9]+(?: [a-z0-9]+)+)»/, (m, lead, key) => { detail.push(['Index key', h('code', { text: key })]); return `${lead}the tracked fact`; });
  text = text.replace(/\n?values:\s*[^\n]+\s*$/i, '');
  return { body: h('p', { class: 'question' }, ...rich(text)), detail };
}

async function answerQuestion(q, button, status, body) {
  const continued = body.confirm === true && q.scope.startsWith('epoch:');
  const web = webScope(q.scope).kind;
  const text = `Question ${q.id} resolved and recorded as a teacher intervention.` + (continued
    ? ` Epoch ${q.epoch} now continues the calibration of ${q.scope.slice(6)}.`
    : web === 'knowledge' ? (body.confirm ? ' The knowledge item is accepted.' : ' The knowledge item is rejected.')
      : web ? (body.confirm === undefined ? ` Recorded: ${body.resolution}.` : '')
        : body.confirm === true ? ' Only that factor is relearned.' : '');
  await act(button, status, async () => {
    await api('POST', `/api/pending/${q.id}/resolve`, body);
    if (continued) { state.epoch = q.epoch; $('epochSelect').value = q.epoch; $('evEpoch').value = q.epoch; }
    await reloadQuestionsKeepingFocus(q);
    return text;
  });
  if (!status.isConnected) say($('questionStatus'), text);
}

// RT4-01: answering re-renders the list and removes the control that had focus. Keep it where the person was, as
// Knowledge does: this question's heading (shown with resolved questions), else the next, then the previous
// question's, else the view heading.
async function reloadQuestionsKeepingFocus(q) {
  const order = [...$('pendingList').querySelectorAll('article[data-pending]')].map((a) => a.dataset.pending);
  await Promise.all([loadQuestions(), loadHeader()]);
  if (document.activeElement && document.activeElement !== document.body) return;   // focus was not lost
  const at = order.indexOf(String(q.id));
  const next = [q.id, ...order.slice(at + 1), ...order.slice(0, Math.max(0, at)).reverse()].map((id) => $(`q-title-${id}`)).find(Boolean);
  (next ?? $('h-questions')).focus();
}

async function resolveQuestion(q, input, button, status) {
  const answer = input.value.trim();
  if (!answer) { say(status, 'Write an answer before resolving.', 'error'); input.focus(); return; }
  await act(button, status, async () => {
    await api('POST', `/api/pending/${q.id}/resolve`, { resolution: answer });
    await reloadQuestionsKeepingFocus(q);
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
        g.origin === 'founding' ? 'founding' : `${g.bits}-bit, degree ${g.degree}`, g.byte_size, fmtNum(g.metrics?.validation_mse), heldoutText(g),
        fmtTime(g.created_at), { actions: [h('button', { type: 'button', 'aria-label': `View revision ${g.id}`, 'data-view-graph': g.id, text: 'View',
          onclick: (ev) => { state.selectedGraph = g.id; showGraph(g.id, ev.currentTarget); } })] }])))));
}

// Held-out error from an explicit evaluation, else the one the promotion gate measured.
function heldoutMse(g) {
  return g.metrics?.heldout_mse ?? g.decision?.checks?.heldout_gain?.candidate_mse ?? null;
}
function heldoutText(g) {
  const x = heldoutMse(g);
  return x === null ? 'not evaluated' : fmtNum(x);
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
      ['Held-out MSE', m.heldout_mse !== undefined && m.heldout_mse !== null ? fmtNum(m.heldout_mse)
        : heldoutMse(g) !== null ? `${fmtNum(heldoutMse(g))} (measured by the promotion gate)` : 'not evaluated yet'],
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
  const cell = 16, pad = 34;   // labels are 13 units: about 11 px when the matrix is scaled down to a phone
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
    if (k < cols) svg.append(s('text', { x: pad + k * cell + cell / 2, y: pad - 8, 'text-anchor': 'middle' }, k));
    if (k < rows) svg.append(s('text', { x: pad - 6, y: pad + k * cell + cell / 2 + 4, 'text-anchor': 'end' }, k));
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
    ? table('Checkpoint archives', [{ label: 'Id', num: true }, 'Status', 'Policy', { label: 'Size', num: true }, { label: 'State', num: true }, 'Created', 'Last verified', 'File', 'Actions'],
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
  const kind = $('memKind').value || 'research';
  const result = await load('memory', [$('deletionLog'), $('decisionLog'), $('memoryBrowse')], () => Promise.all([
    api('GET', '/api/memory?kind=deletion&limit=100'), api('GET', '/api/memory?kind=decision&limit=100'),
    api('GET', `/api/memory?kind=${enc(kind)}&limit=100`)]));
  if (!result) return;
  const [deletions, decisions, browsed] = result;
  renderMemoryBrowse(kind, browsed.items);
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

function scopeText(scope) {
  if (scope === null || scope === undefined) return '—';
  if (typeof scope !== 'object') return String(scope);
  return Object.entries(scope).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => `${k} ${v}`).join(' · ') || '—';
}

// Any memory kind: id, time, scope and the stored record as text (records are shown as data, never as markup).
function renderMemoryBrowse(kind, items) {
  fill($('memoryBrowse'), items.length
    ? table(`Memory of kind ${kind}, newest first`, ['Id', 'Time', 'Scope', 'Record'], items.map((r) => [
        h('code', { text: String(r.id) }), fmtTime(r.time), scopeText(r.scope),
        h('details', {}, h('summary', { text: 'Show record' }), h('pre', { text: JSON.stringify(r.payload, null, 2) }))]))
    : empty(`No ${kind} records.`));
}

// ---- research (Web Cortex) ---------------------------------------------------------------------------------
// Everything a source supplies (titles, quotes, claims, URLs) is untrusted: it is set with textContent only, and a
// URL becomes a link only when it parses as http(s).

// Canonical research wording, shared with Pocket (pocket/web, lane L4) — keep both in step:
//   field label "Kind of research"; auto "Let NeuroSciFi choose" (Pocket: "Let your Spark choose");
//   quick_lookup "Quick lookup"; deep_research "Deep research"; fact_verification "Check a claim";
//   comparative "Compare options"; scientific "Scientific literature"; technical "Technical (packages, releases,
//   advisories)"; console-only: continuous "Continuous investigation"; change_detection "What changed";
//   gap_resolution "Fill a knowledge gap".
//   Stages: Queued, Discovering, Reading, Evaluating, Synthesizing, Completed, Partial, Failed, Cancelled.
//   Verdicts: Supported / Disputed / Contested / Not enough evidence.
//   Answer status: Corroborated / Single source / Contested / Insufficient evidence. Source badge: Live / From cache.
//   Other badges are sentence case ("Provisional", "Paused").
const MODE_LABELS = {
  auto: 'Let NeuroSciFi choose', quick_lookup: 'Quick lookup', deep_research: 'Deep research',
  fact_verification: 'Check a claim', comparative: 'Compare options', scientific: 'Scientific literature',
  technical: 'Technical (packages, releases, advisories)', continuous: 'Continuous investigation',
  change_detection: 'What changed', gap_resolution: 'Fill a knowledge gap',
};
const ANSWER_STATUS = { corroborated: 'Corroborated', single_source: 'Single source', contested: 'Contested',
                        insufficient: 'Insufficient evidence', insufficient_evidence: 'Insufficient evidence' };
const statusText = (x) => ANSWER_STATUS[x] ?? cap(String(x).replaceAll('_', ' '));
const cap = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);
// Retrieval limitation codes (FETCH_ERROR_CODES and provider errors) in words.
const LIMITATION_TEXT = {
  budget_exhausted: 'the run used up its request, time or query budget before reading everything',
  rate_limited: 'the service asked us to slow down (rate limited), so it was skipped',
  blocked_robots: 'the site’s robots.txt does not allow reading this page',
  blocked_policy: 'the safe fetcher refused the address (private or disallowed)',
  timeout: 'the site did not answer in time',
  decode_error: 'the document could not be read (unreadable or hostile markup)',
  daemon_unreachable: 'the local search service (open-webSearch) is not reachable',
  daemon_failed: 'the local search service (open-webSearch) failed to start',
  dns_failure: 'the site’s name could not be resolved',
  connect_failed: 'the site could not be reached',
  http_error: 'the site answered with an error',
  too_large: 'the document is larger than the size limit',
  unsupported_type: 'the document type is not supported',
  too_many_redirects: 'the page redirected too many times',
  tls_error: 'the secure connection failed',
  disabled: 'web access was turned off',
  circuit_open: 'the provider failed repeatedly and is cooling down',
  internal_error: 'an internal error stopped reading this source',
  failed: 'the request failed',
};
const RETRIEVAL_TEXT = { live: 'read live', cache: 'from the cache', api: 'from an API', feed: 'from a feed', rendered: 'rendered with JavaScript' };
const KIND_TEXT = { page: 'Web page', pdf: 'PDF', api: 'Structured record', feed: 'Feed item' };
const LIVE_MODES = ['live', 'api', 'feed', 'rendered'];
const STAGES = [['discovering', 'Discovering'], ['reading', 'Reading'], ['evaluating', 'Evaluating'], ['synthesizing', 'Synthesizing']];
const TASK_LABELS = { queued: 'Queued', discovering: 'Discovering', reading: 'Reading', evaluating: 'Evaluating',
                      synthesizing: 'Synthesizing', completed: 'Completed', partial: 'Partial', failed: 'Failed', cancelled: 'Cancelled' };
const TERMINAL = ['completed', 'partial', 'failed', 'cancelled'];
const POLL_MS = 2000;
const QUEUED_HINT_S = 30;   // a task queued this long with no worker gets the "is the worker running?" hint
const web = { status: null, tasks: [], taskId: null, task: null, taskSig: '', listSig: '', seen: new Map(), timer: null, polling: false,
              queuedSince: new Map(), privacy: null };

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
  if (t.origin_label) return t.origin_label;   // the backend's own wording when it provides one
  const o = t.origin ?? '';
  if (t.family || o.startsWith('pocket')) return 'Pocket device';
  if (o.startsWith('monitor:')) return `Monitor #${o.slice(8)}`;
  if (o.startsWith('refresh:')) return `Refresh of knowledge #${o.slice(8)}`;
  if (o.startsWith('refresh-owner:')) return `Refresh of knowledge #${o.slice(14)}`;
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
  loadLlmStatus();
  if (web.taskId) await loadTask(web.taskId);
  schedulePoll();
}

// Inference gateway health: an explicit state from the server (ready / degraded / unavailable / unauthorised /
// not_configured), never inferred here.
const LLM_STATE = { ready: ['Ready', 'ok'], degraded: ['Degraded', 'warn'], unavailable: ['Unavailable', 'warn'],
  unauthorised: ['Unauthorised', 'warn'], not_configured: ['Not configured', 'neutral'] };

async function loadLlmStatus() {
  const slot = $('llmStatus');
  try {
    renderLlmStatus(await api('GET', '/api/llm/status'));
  } catch (error) {
    fill(slot, h('p', { class: 'msg error', text: errorText(error) }));
  } finally {
    slot.removeAttribute('aria-busy');
  }
}

function renderLlmStatus(s) {
  const hl = s.health ?? {};
  const [word, tone] = LLM_STATE[hl.state] ?? [String(hl.state), 'neutral'];
  const u = s.usage_24h;
  const answers = !s.config?.reasoning ? 'Extractive summary (model reasoning is off)'
    : hl.state === 'ready' ? 'Written by the model from verified evidence, every sentence checked'
      : 'Extractive summary until the model is ready';
  fill($('llmStatus'), kv([
    ['State', h('span', { id: 'llmState' }, badge(word, tone))],
    ['Model', hl.model ?? 'none'],
    ['Endpoint', hl.endpoint ? `${hl.endpoint} (${hl.locality ?? 'unknown'})` : 'none configured'],
    ['Research answers', answers],
    hl.reason ? ['Why', hl.reason] : null,
    ['Last 24 hours', u && u.calls ? `${u.calls} call${u.calls === 1 ? '' : 's'}, ${u.prompt_tokens + u.completion_tokens} tokens${u.avg_tokens_per_second ? `, about ${fmtNum(u.avg_tokens_per_second, 1)} tokens/s` : ''}` : 'no calls'],
  ]));
}

// report.reasoning (webcortex/reasoning.py): a model-written answer that passed the server's citation and claim-state
// guard, or the template label with the reason. Model text is set as text only, like every web-derived string.
// The server label says "reasoning ... verified"; the guard only checks each sentence against its cited sources, and a
// single-source statement is marked as such. State exactly that, keeping the model name.
function modelAnswerLabel(x) {
  // the model name comes from generated_by ("model:<name>"); the backend label wording is not parsed
  const who = (x.generated_by ?? '').startsWith('model:') ? x.generated_by.slice(6) : null;
  return `Written by ${who ?? 'the local model'} from the collected evidence. Each sentence was checked against its cited sources; single-source statements are marked.`;
}

function reasoningBlock(r) {
  const x = r.reasoning;
  if (!x) return [];
  if (x.generated_by === 'template' || !x.answer?.length) {
    // T2-03: no model sentence survived the guard: say what was not established before any quoted evidence
    return [x.unanswered?.text ? h('p', { class: 'msg', id: 'reasoningUnanswered', text: x.unanswered.text }) : null,
      x.note?.text ? h('p', { class: 'msg', id: 'reasoningNote', text: x.note.text }) : null,
      h('p', { class: 'msg warn', id: 'reasoningLabel', 'data-generated-by': 'template' }, badge('Extractive summary (template)', 'neutral'), ` ${x.label}`),
      x.reason ? technical(h('p', { text: `Why: ${x.reason}` })) : null];
  }
  const out = [h('h4', { text: 'Answer' }),
    h('p', { class: 'sub', id: 'reasoningLabel', 'data-generated-by': x.generated_by }, badge('Model-written summary', 'info'), ` ${modelAnswerLabel(x)}`),
    h('ol', { class: 'answers', id: 'modelAnswer' }, x.answer.map((s) => h('li', {}, h('p', { class: 'generated', text: s.text }),
      h('p', { class: 'answermeta' }, citeLinks(s.citations), ' ',
        Object.entries(s.states ?? {}).map(([id, st]) => badge(`${id}: ${statusText(st)}`, st === 'corroborated' || st === 'supported' ? 'ok' : 'neutral')),
        s.flag ? badge(`self-check flagged: ${s.flag.problem.replace(/_/g, ' ')} (not confirmed by the checks)`, 'neutral') : null))))];
  // D-R5-3: the verdict did not evaluate or establish the checked statement: a system note, whatever the model wrote
  if (x.note?.text) out.push(h('p', { class: 'msg', id: 'reasoningNote', text: x.note.text }));
  if (x.gaps?.length) {
    out.push(h('h4', { text: 'Knowledge gaps' }), h('ul', { class: 'reasons', id: 'modelGaps' }, x.gaps.map((g) => h('li', {}, g.text, ' ', badge(g.by === 'model' ? 'model-identified' : 'from the evidence', 'neutral')))));
  }
  const u = x.usage ?? {};
  out.push(technical(
    x.rejected?.length ? h('div', {}, h('p', { text: `${x.rejected.length} model sentence${x.rejected.length === 1 ? ' was' : 's were'} rejected by the guard:` }),
      h('ul', { class: 'reasons', id: 'modelRejected' }, x.rejected.map((j) => h('li', {}, h('span', { text: j.text }), ' — ', h('em', { text: j.reasons.join('; ') }))))) : null,
    h('p', { text: `${x.generated_by} · ${u.calls ?? 0} call(s) · ${u.prompt_tokens ?? 0} prompt + ${u.completion_tokens ?? 0} completion tokens · ${fmtNum((u.latency_ms ?? 0) / 1000, 1)} s${u.first_token_ms ? ` · first token ${fmtNum(u.first_token_ms, 0)} ms` : ''}` })));
  return out;
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
        s.providers.map((p) => [p.name, providerBadge(p), `${p.successes ?? 0} ok, ${p.failures ?? 0} failed${p.last_ok_at ? `; last success ${ago(p.last_ok_at)}` : ''}${p.key === 'configured' ? `; key configured, ${p.used_today ?? 0} of ${p.daily_cap} queries today` : ''}`,
          p.last_error ? `${p.last_error}${p.last_failure_at ? ` (${ago(p.last_failure_at)})` : ''}` : 'none']))
    : empty('No providers configured.'));
  const cur = s.curiosity;
  fill($('curiosityList'), cur === null
    ? empty('Curiosity is shown while web access is on.')
    : cur.length
      ? h('ol', { class: 'reasons' }, cur.map((x) => h('li', {}, h('strong', { text: curiosityTitle(x) }),
          h('br'), h('span', { class: 'sub', text: `${modeLabel(x.mode)}: ${plainReason(x.reason)}` }),
          technical(kv([['Why', x.reason], ['Kind', x.kind], ['Utility', fmtNum(x.utility, 3)],
            ['Expected information gain', `${fmtNum(x.info_gain, 3)} bits`], x.target.key ? ['Index key', h('code', { text: x.target.key })] : null])))))
      : empty('Nothing is worth investigating now: what is known is fresh or well supported.'));
  $('activeCount').textContent = s.active ? `${s.active} active` : '';
  syncControls();
  if (web.task && web.taskId) rerenderReportIfStalled();
}

// A curiosity candidate's title (RT4-02): its statement or objective, never the stemmed index key ("kilometr long
// river varnel"), which goes under Technical detail.
function curiosityTitle(x) {
  const t = x.target ?? {};
  const said = t.statement ?? t.objective;
  if (said) return said;
  return { hypothesis: 'An open question the sources disagree about', gap: 'A claim only one source supports' }[x.kind] ?? 'An open question';
}

// The reason in plain words: clauses that carry the numbers ("gains 0.898 bits", "P(current)=0.50", "x1.5") move
// to Technical detail, which shows the full reason.
function plainReason(reason) {
  const clauses = String(reason ?? '').split(/;\s*/).filter((c) => c && !/\bgains?\b|\bbits\b|P\(current\)|utility|down-weighted/i.test(c))
    .map((c) => c.replace(/\s*\((?:confidence|x)\s*[\d.]+\)/gi, '').trim()).filter(Boolean);
  const text = clauses.join('; ');
  return text ? text[0].toUpperCase() + text.slice(1) + (/[.!?]$/.test(text) ? '' : '.') : 'Worth checking again.';
}

// The queued-without-worker hint depends on the worker state and on time, not only on the task row.
function rerenderReportIfStalled() {
  const t = web.task;
  const sig = JSON.stringify([t.id, t.status, t.cancel_requested, t.updated_at, t.worker_alive, workerMissing(t)]);
  if (sig !== web.taskSig) { web.taskSig = sig; rerender($('report'), renderReport(t)); }
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
  const sig = JSON.stringify([filter, needle, shown.map((t) => [t.id, t.status, t.cancel_requested, t.updated_at, t.worker_alive, workerMissing(t)]), web.taskId]);
  if (sig === web.listSig && $('taskList').querySelector('table, .empty')) { $('taskList').removeAttribute('aria-busy'); return; }
  web.listSig = sig;
  rerender($('taskList'), shown.length
    ? table('Research tasks, newest first', ['Status', 'Kind of research', 'Objective', 'Created', 'Origin', 'Actions'], shown.map((t) => [
        h('span', {}, badge(taskLabel(t), taskTone(t)), workerMissing(t) ? h('span', { class: 'sub stalled', text: ' no worker yet' }) : null),
        modeLabel(t.mode), h('span', { class: 'objective', text: t.objective }), fmtTime(t.created_at),
        originLabel(t), { actions: taskActions(t, $('taskStatusMsg')) }]))
    : empty(needle ? 'No task matches the filter.'
      : filter ? `No ${$('taskStatus').selectedOptions[0].textContent.toLowerCase()} tasks.`
        : 'No research yet. Ask a question above.'));
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
    const label = t.status === 'failed' ? 'Resume' : 'Run again';
    out.push(h('button', { type: 'button', id: `resume-${t.id}`, text: label, 'aria-label': `${label} research task: ${what}`,
      onclick: (ev) => resumeTask(t, ev.currentTarget, statusEl) }));
  }
  return out;
}

function resumeTask(t, control, statusEl) {
  const what = short(t.objective, 60);
  return act(control, statusEl, async () => {
    const next = await api('POST', `/api/web/tasks/${t.id}/resume`, {});
    web.taskId = next.id;
    web.taskSig = '';
    await refreshTasks();
    if (next.requeued === 'existing' || next.deduplicated) return `Already running — opened the existing task for “${what}”.`;
    return next.id === t.id ? `Queued “${what}” again; it continues from its last checkpoint.`
      : `Continuing “${what}” as a new linked task; sources read before are reused from the cache where possible.`;
  });
}

// U-06: web access was turned off behind the page's back: show the notice and put focus on its switch.
function showWebOff() {
  $('webOff').hidden = false;
  if (web.status) web.status.config.enabled = false;
  syncControls();
  loadWebStatus().catch(() => {}).finally(() => { if (!$('webOff').hidden) $('webEnable').focus(); });
}

// U-13: from a report to the knowledge items it proposed, focused and filtered to those awaiting review.
async function reviewKnowledge(ids) {
  web.focusKnowledge = ids[0];
  $('kStatus').value = 'provisional';
  $('kTopic').value = '';
  location.hash = '#knowledge';
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
  const sig = JSON.stringify([task.id, task.status, task.cancel_requested, task.updated_at, task.worker_alive, workerMissing(task)]);
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

// A queued task nobody picks up: the task's own worker field when the backend reports one, else the worker state
// in /api/web/status, else time (QUEUED_HINT_S after the page or the server first saw it queued).
function workerMissing(t) {
  if (t.status !== 'queued' || t.proposed) return false;
  if (typeof t.worker_alive === 'boolean') return false;   // true: running fine; false: the "stopped responding" line
  const w = web.status?.worker ?? {};
  if (w.embedded && w.running) return false;
  if (typeof w.alive === 'boolean' && w.alive) return false;
  if (!web.queuedSince.has(t.id)) web.queuedSince.set(t.id, Date.now());
  const waited = Math.max((Date.now() - web.queuedSince.get(t.id)) / 1000, Date.now() / 1000 - (t.created_at ?? Infinity));
  return typeof w.alive === 'boolean' ? !w.alive || waited >= QUEUED_HINT_S : waited >= QUEUED_HINT_S;
}

function queuedHint() {
  // R6 setup (h): the worker heartbeat (web status `worker.alive`) says plainly when none runs
  const none = web.status?.worker?.alive === false;
  return h('p', { class: 'msg error', id: 'queuedHint' }, ...rich((none ? 'Your Spark has no research worker running, so this task waits. '
    : 'No research worker has picked this task up yet. Is the research service running? ') +
    'Start `neuroscifi-webcortex` (or run `nsf web work`), or restart the console with `--web-worker`.'));
}

// Technical failures become a plain sentence; the raw text stays under Technical detail.
const TECHNICAL = /[A-Z][A-Za-z]+(Error|Exception)\b|Traceback|\w+_\w+|\.py\b/;
function errorBlock(t) {
  if (!t.error) return null;
  if (t.status === 'failed') {
    return h('div', { class: 'failure' }, h('p', { class: 'msg error', text: 'This research stopped because of an error inside the research worker. ' +
      'Resume it to continue from its last checkpoint; sources already read are reused.' }), technical(h('pre', { text: t.error })));
  }
  if (TECHNICAL.test(t.error)) return h('div', { class: 'failure' }, h('p', { class: 'msg error', text: `This research ended early (${taskLabel(t).toLowerCase()}).` }), technical(h('pre', { text: t.error })));
  return h('p', { class: 'msg error', text: `This research ended: ${t.error.replace(/\.$/, '')}.` });
}

function reportActions(t) {
  if (!['failed', 'partial', 'cancelled'].includes(t.status)) return null;
  const label = t.status === 'failed' ? 'Resume from checkpoint' : 'Run again as a new task';
  return h('div', { class: 'buttons' }, h('button', { type: 'button', id: 'reportResume', class: 'primary', text: label,
    onclick: (ev) => resumeTask(t, ev.currentTarget, $('reportStatus')) }));
}

function renderReport(t) {
  const r = t.result;
  const head = [
    h('h3', { class: 'question', text: t.objective }),
    h('p', { class: 'badges' }, badge(taskLabel(t), taskTone(t)), r ? liveBadge(r) : null,
      r?.memory?.answered_from_memory ? badge('Answered from memory', 'neutral') : null,
      ` ${modeLabel(t.mode)} · ${originLabel(t)} · created ${fmtTime(t.created_at)}${t.finished_at ? ` · finished ${fmtTime(t.finished_at)}` : ''}`),
    stageList(t),
    t.cancel_requested && isActive(t) ? h('p', { class: 'msg busy', text: 'Cancel requested: the run stops before its next network request.' }) : null,
    workerMissing(t) ? queuedHint() : null,
    isActive(t) && t.worker_alive === false
      ? h('p', { class: 'msg error', id: 'workerStopped', text: 'The research worker running this task stopped responding. Cancel it, or resume it once the worker runs again.' }) : null,
    errorBlock(t),
    reportActions(t),
  ];
  if (!r) {
    return [...head, empty(isActive(t) ? 'The report appears when the run finishes. The stage above is the one the worker reports; nothing is estimated.'
      : 'This task ended without a report.')];
  }
  return [...head, ...reportBody(r, t), usageBlock(t), evidenceBlock(t)];
}

// Live / From cache from the sources actually read (cited first), never from a run that read nothing.
function liveBadge(r) {
  const sources = r.sources ?? [];
  const cited = sources.filter((x) => x.cited !== false);
  const pool = cited.length ? cited : sources;
  if (!pool.length) return null;
  return pool.some((x) => LIVE_MODES.includes(x.retrieval_mode)) ? badge('Live', 'info') : badge('From cache', 'neutral');
}

// "3 copies, 1 independent source" when several cited documents repeat one origin (U-18; fields from L1a).
function copiesText(x) {
  const copies = x?.copies, independent = x?.independent_sources;
  if (typeof copies !== 'number' || typeof independent !== 'number' || copies <= independent) return null;
  return `${copies} copies, ${independent} independent source${independent === 1 ? '' : 's'}`;
}

// Sources about the same subject that are not cited as support: listed as "See also", never as citations.
function seeAlso(numbers) {
  if (!numbers?.length) return null;
  return h('span', { class: 'seealso' }, 'See also ', numbers.map((n, i) => [i ? ', ' : '',
    h('a', { href: `#src-${n}`, class: 'related', 'aria-label': `related source ${n}, not cited as support`, text: `source ${n}`,
      onclick: (ev) => { ev.preventDefault(); const el = $(`src-${n}`); if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); } } })]));
}

function answerItem(a, r) {
  const finding = a.status === 'summary' ? null : (r.findings ?? []).find((f) => f.statement === a.text);
  const copies = copiesText(a) ?? copiesText(finding);
  const meta = [a.status && a.status !== 'summary' ? badge(statusText(a.status), a.status === 'corroborated' ? 'ok' : a.status === 'contested' ? 'warn' : 'neutral') : null,
    finding?.superseded ? badge('Older release', 'neutral') : null,
    typeof a.confidence === 'number' ? badge(`confidence ${pct(a.confidence)}`, 'neutral') : null,
    copies ? h('span', { class: 'sub copies', text: copies }) : null, seeAlso(a.related ?? finding?.related)];
  const text = a.status === 'summary' ? freshLead(a.text, r) : a.text;
  const body = a.type === 'quote' ? h('blockquote', { class: 'quote', text }) : h('p', { class: 'template', text });
  return h('li', {}, body, h('p', { class: 'answermeta' }, citeLinks(a.citations), ' ', meta));
}

// "Newest cited source: 3 days ago." was true when the report was written; say the date and the age now.
function freshLead(text, r) {
  const t = r.freshness?.newest_source_at;
  if (t === null || t === undefined) return text;
  return text.replace(/Newest cited source: [^.]*\./, `Newest cited source: published ${fmtDay(t)} (${ago(t)}).`);
}

function side(label, x) {
  return h('div', { class: 'side' }, h('p', { class: 'sidehead' }, `${label} `, citeLinks([x.citation]), ` ${x.domain} · ${fmtDay(x.published_at)} · ${x.source_type}`),
    x.type === 'quote' ? h('blockquote', { class: 'quote', text: x.text }) : h('p', { text: x.text }));
}

const VERDICT_TEXT = {
  supported: ['The sources support this claim.', 'ok', 'Supported'],
  disputed: ['The sources contradict this claim.', 'warn', 'Disputed'],
  contested: ['The sources disagree about this claim.', 'warn', 'Contested'],
  insufficient: ['There is not enough evidence to decide this claim.', 'neutral', 'Not enough evidence'],
  // ADR-035 verification states (verdict.state); `label` above stays the pre-v0.7.0 fallback
  conflicting: ['Credible sources disagree about this claim; neither side clearly outweighs the other.', 'warn', 'Conflicting'],
  temporally_unresolved: ['No evidence establishes whether this is true now.', 'neutral', 'Not established for now'],
  not_evaluated: ['This claim was not evaluated (a judgement, or no usable evidence was examined).', 'neutral', 'Not evaluated'],
};
const BASIS_TEXT = { corroboration: 'independent sources agree', official_source: "the subject's own site", registry_record: 'the package registry record', version_marker: 'the version number (pre-release marker)', contradiction: 'contradicting evidence' };

// The verdict in one sentence plus the claim that was checked. A verdict never says "0 contradict" next to listed
// contradictions: such reasons are dropped and the contradictions are pointed to instead.
function verdictBlock(r) {
  const v = r.verdict;
  const [sentence, tone, word] = VERDICT_TEXT[v.state] ?? VERDICT_TEXT[v.label] ?? [v.label, 'neutral', cap(v.label)];
  const facts = [v.state ? `State: ${v.state.replace(/_/g, ' ')}` : null,
    v.state ? `basis: ${v.basis ? (BASIS_TEXT[v.basis] ?? v.basis) : 'none'}` : null,
    v.category ? `category: ${v.category.replace(/_/g, ' ')}${v.aspect ? ` (${v.aspect})` : ''}` : null].filter(Boolean);
  const conflicts = (r.contradictions ?? []).filter((c) => !v.citations?.length || v.citations.includes(c.a?.citation) || v.citations.includes(c.b?.citation));
  let reasons = v.reasons ?? [];
  let plain = v.summary ?? v.plain ?? sentence;
  const counted = Array.isArray(v.supporting) && Array.isArray(v.contradicting);
  if (counted) {   // the backend's own classification of the checked claim (L1a); never a count made here
    const [s, c] = [v.supporting.length, v.contradicting.length];
    if (s || c) plain = `${plain} ${s} source sentence${s === 1 ? '' : 's'} support${s === 1 ? 's' : ''} it; ${c} contradict${c === 1 ? 's' : ''} it.`;
  } else if (conflicts.length) {   // older reports: drop "0 contradict" beside a listed contradiction
    reasons = reasons.filter((x) => !/\b0 contradict/.test(x));
    if (v.label === 'supported' || v.label === 'insufficient') {
      plain = `${plain} The sources also disagree with each other: see the ${conflicts.length} contradiction${conflicts.length === 1 ? '' : 's'} below.`;
    }
  }
  return [h('h4', { text: 'Verdict' }),
    h('p', { class: 'verdictline', id: 'verdictText' }, badge(word, tone), ` ${plain}`),
    facts.length ? h('p', { class: 'sub', id: 'verdictBasis', 'data-state': v.state ?? '', text: `${facts.join(' · ')}.` }) : null,
    v.statement ? h('p', { class: 'sub', id: 'claimChecked' }, h('strong', { text: 'Claim checked: ' }), v.statement) : null,
    technical(h('p', { text: `Support estimate ${pct(v.confidence)}${v.citations?.length ? ` from sources ${v.citations.map((n) => `[${n}]`).join(' ')}` : ''}.` }),
      reasons.length ? h('ul', { class: 'reasons' }, reasons.map((x) => h('li', { text: x }))) : null)];
}

// T1-11: health from the most recent outcome and the time since the last success (the backend's `health`), not
// from the circuit alone, which stays closed until several failures in a row.
const PROVIDER_HEALTH = { healthy: ['healthy', 'ok'], failing: ['failing: last call failed', 'warn'], down: ['down', 'warn'],
  unchecked: ['no recent success', 'neutral'], unused: ['not used yet', 'neutral'],
  rate_limited: ['rate-limited', 'warn'], unconfigured: ['not configured (no API key)', 'neutral'],
  off: ['off (cap 0)', 'neutral'] };
function providerBadge(p) {
  const fallback = p.state === 'closed' ? 'healthy' : p.state === 'open' ? 'down' : String(p.state ?? 'unknown');
  const [word, tone] = PROVIDER_HEALTH[p.health ?? fallback] ?? [String(p.health ?? p.state).replace('_', ' '), 'neutral'];
  return badge(word, tone);
}

// Limitation strings such as "wikipedia: rate_limited" or "budget_exhausted: budget exhausted: …" in words.
function plainLimitation(text, r) {
  const m = /^([^:]+): ([a-z_]+)(?::\s*(.*))?$/.exec(text);
  if (m && LIMITATION_TEXT[m[2]]) {
    const who = m[1] === m[2] || m[1] === 'retrieval' ? null : m[1];
    return `${who ? `${who}: ` : ''}${cap(LIMITATION_TEXT[m[2]])}.`;
  }
  const code = /^([a-z_]+): (.*)$/.exec(text);
  if (code && LIMITATION_TEXT[code[1]]) {
    const rest = code[2].replace(new RegExp(`^${code[1].replace(/_/g, '[ _]')}:?\\s*`, 'i'), '');
    return `${cap(LIMITATION_TEXT[code[1]])}${rest ? ` (${rest})` : ''}.`;
  }
  const cache = /^Served from cache retrieved [^;]*; not re-verified live$/.exec(text);
  if (cache) {
    const read = (r.sources ?? []).filter((x) => x.cited !== false).map((x) => x.retrieved_at).filter((x) => typeof x === 'number');
    if (read.length) return `Served from the cache (read ${fmtDay(Math.min(...read))}, ${ago(Math.min(...read))}); not re-checked live.`;
  }
  return text;
}

function reportBody(r, t) {
  const modelAnswer = r.reasoning && r.reasoning.generated_by !== 'template' && r.reasoning.answer?.length;
  const out = [...reasoningBlock(r), h('h4', { text: modelAnswer ? 'Extractive summary (quoted evidence)' : 'Answer' }),
    h('ol', { class: 'answers' }, (r.answer ?? []).map((a) => answerItem(a, r)))];
  const claims = (r.sources ?? []).length > 0;
  if (r.verdict && claims) out.push(...verdictBlock(r));
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
      h('p', { class: 'reason', text: c.description ?? `Disagreement: ${c.reason}` }), h('div', { class: 'sides' }, side('A', c.a), side('B', c.b)),
      c.description ? technical(h('p', {}, h('code', { text: c.reason }))) : null,
      h('p', { class: 'sub', text: c.assessment }))));
  }
  if (r.uncertainties?.length) out.push(h('h4', { text: 'Uncertainties' }), h('ul', { class: 'reasons' }, r.uncertainties.map((x) => h('li', { text: x }))));
  if (r.limitations?.length) {
    const plain = r.limitations.map((x) => plainLimitation(x, r));
    out.push(h('h4', { text: 'Limitations' }), h('ul', { class: 'reasons', id: 'limitations' }, plain.map((x) => h('li', { text: x }))));
    if (plain.some((x, i) => x !== r.limitations[i])) out.push(technical(h('ul', { class: 'reasons' }, r.limitations.map((x) => h('li', {}, h('code', { text: x }))))));
  }
  const f = r.freshness;
  if (f) {
    out.push(h('h4', { text: 'Freshness' }), kv([['Required', `${f.class}${f.window_seconds ? ` (within ${(f.window_seconds / 86400).toFixed(f.window_seconds < 86400 ? 2 : 0)} days)` : ''}`],
      ['Satisfied', badge(f.satisfied ? 'Yes' : 'No', f.satisfied ? 'ok' : 'warn')],
      ['Newest source', f.newest_source_at === null || f.newest_source_at === undefined ? 'undated' : `${fmtDay(f.newest_source_at)} (${ago(f.newest_source_at)})`]]));
  }
  out.push(h('h4', { text: 'Sources' }), (r.sources ?? []).length ? h('ol', { class: 'sources' }, r.sources.map(sourceItem)) : empty('No sources were read.'));
  const k = r.consolidation;
  if (k?.provisional?.length) {
    const n = k.provisional.length;
    out.push(h('p', { class: 'msg ok', id: 'reviewLink' }, `${n} new provisional knowledge item${n === 1 ? '' : 's'} await${n === 1 ? 's' : ''} your review: `,
      h('a', { href: '#knowledge', text: n === 1 ? 'review it under Knowledge' : 'review them under Knowledge',
        onclick: (ev) => { ev.preventDefault(); reviewKnowledge(k.provisional); } }), '.'));
  }
  return out;
}

function sourceFlags(s) {
  const f = s.flags ?? {};
  const out = [];
  if (f.injection?.length) out.push(badge(`injection quarantined (${f.quarantined_sentences ?? 0} sentence${f.quarantined_sentences === 1 ? '' : 's'})`, 'warn'));
  if (f.truncated) out.push(badge('truncated', 'warn'));
  if (s.published_at === null || s.published_at === undefined) out.push(badge('undated', 'neutral'));
  if (s.date_source === 'search_engine' && s.search_published_at) out.push(badge(`search engine reports ${fmtDay(s.search_published_at)} (lower trust)`, 'neutral'));
  if (s.future_published_at) out.push(badge(`future date ${fmtDay(s.future_published_at)} set aside`, 'warn'));
  if (s.source_type === 'tertiary') out.push(badge('encyclopedia (tertiary)', 'neutral'));
  if (f.js_suspected) out.push(badge('needs JavaScript', 'neutral'));
  if (f.hidden_removed) out.push(badge(`${f.hidden_removed} hidden element${f.hidden_removed === 1 ? '' : 's'} removed`, 'neutral'));
  if (s.cited === false) out.push(badge('read, not cited', 'neutral'));
  return out;
}

function sourceHow(s) {
  if (s.kind === 'api' && s.retrieval_mode === 'api') return 'Structured record from an API';
  return `${KIND_TEXT[s.kind] ?? cap(s.kind ?? 'document')}, ${RETRIEVAL_TEXT[s.retrieval_mode] ?? s.retrieval_mode}`;
}

function sourceDate(s) {
  // a revision or modification time is never shown as a publication date (T1-05)
  if ((s.published_at === null || s.published_at === undefined) && s.modified_at) {
    return `${s.modified_source === 'wikipedia:revision' ? 'Last edited' : 'Last modified'} ${fmtDay(s.modified_at)} (not a publication date)`;
  }
  return `Published ${fmtDay(s.published_at)}`;
}

function sourceItem(s) {
  return h('li', { id: `src-${s.n}`, tabindex: '-1', value: String(s.n), 'data-source': s.n },
    h('p', { class: 'srctitle' }, h('strong', { text: s.title || s.domain || 'Untitled' }), ` · ${s.domain}`),
    h('p', { class: 'srcurl' }, safeLink(s.url)),
    h('p', { class: 'sub', text: `${sourceDate(s)} · retrieved ${fmtTime(s.retrieved_at)} · ${sourceHow(s)}` }),
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
            badge(c.cluster_status ? statusText(c.cluster_status) : c.kind, 'neutral')))))),
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
    `${(c.before || []).join(', ') || '—'} → ${(c.after || []).join(', ') || '—'}`);
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
    ? table('Monitors', ['Topic', 'Kind of research', 'Every', 'Status', 'Last run', { label: 'Runs', num: true }, 'Recent findings', 'Actions'], monitors.map((m) => [
        h('span', { text: m.topic }), modeLabel(m.mode), `${fmtNum(m.interval_seconds / 3600, 0)} h`, badge(cap(m.status), m.status === 'active' ? 'ok' : m.status === 'proposed' ? 'info' : 'neutral'),
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
  const wanted = $('kStatus').value;
  const out = await load('knowledge', [$('knowledgeList')], () => api('GET', `/api/web/knowledge${topic ? `?topic=${enc(topic)}` : ''}`));
  if (!out) return;
  web.knowledge = out.items;
  renderKnowledge(topic, wanted);
}

function knowledgeMatches(k, wanted) {
  if (!wanted) return true;
  if (wanted === 'contested') return !!k.contested;
  if (wanted === 'stale') return k.status === 'stale' || !!k.stale;
  return k.status === wanted;
}

function renderKnowledge(topic = $('kTopic').value.trim(), wanted = $('kStatus').value) {
  const items = (web.knowledge ?? []).filter((k) => knowledgeMatches(k, wanted));
  const what = wanted ? `${$('kStatus').selectedOptions[0].textContent.toLowerCase()} ` : '';
  fill($('knowledgeList'), items.length ? h('div', { class: 'grid' }, items.map(knowledgeCard))
    : empty(wanted && web.knowledge?.length ? `No ${what}knowledge${topic ? ` about “${topic}”` : ''}.`
      : topic ? `Nothing known about “${topic}”. Research it under Research.` : 'Nothing learned from the web yet. Next step: turn on web access, then ask a question under Research; what it finds appears here for your review.'));
  const target = web.focusKnowledge;
  if (target !== undefined && target !== null) {
    web.focusKnowledge = null;
    const title = $(`k-title-${target}`) ?? $('knowledgeList').querySelector('h2.kstatement');
    if (title) { title.scrollIntoView({ block: 'center' }); title.focus(); }
  }
}

// knowledge.decide reports earlier answered questions whose record now carries this revised decision.
function revised(out) {
  const ids = out?.decisions_revised ?? [];
  return ids.length ? ` Your earlier answer${ids.length === 1 ? '' : 's'} to question${ids.length === 1 ? '' : 's'} ${ids.map((i) => `#${i}`).join(', ')} now record${ids.length === 1 ? 's' : ''} this change.` : '';
}

function knowledgeCard(k) {
  const status = h('p', { class: 'msg', role: 'status' });
  const what = short(k.statement, 60);
  const action = (verb, label, cls, work) => h('button', { type: 'button', id: `k-${verb}-${k.id}`, class: cls, text: label,
    'aria-label': `${label}: ${what}`, onclick: (ev) => { web.focusAfterReview = true; work(ev.currentTarget); } });
  const run = (control, path, body, text) => act(control, status, async () => {
    const out = await api('POST', `/api/web/knowledge/${k.id}/${path}`, body);
    const message = text(out);
    const order = [...$('knowledgeList').querySelectorAll('article[data-knowledge]')].map((a) => a.dataset.knowledge);
    await Promise.all([loadKnowledge(), loadHeader()]);
    // RT-B-04: the re-render removed the button that had focus. Keep it where the person was: this item's heading, else
    // the next (then the previous) item's, else the view heading.
    const hadFocus = web.focusAfterReview;
    web.focusAfterReview = false;
    if (hadFocus && !$('knowledgeList').contains(document.activeElement)) {
      const at = order.indexOf(String(k.id));
      const next = [k.id, ...order.slice(at + 1), ...order.slice(0, at).reverse()].map((id) => $(`k-title-${id}`)).find(Boolean);
      (next ?? $('h-knowledge')).focus();
    }
    say($('knowledgeStatus'), message);
    return message;
  });
  const live = ['provisional', 'accepted', 'stale'].includes(k.status);
  const buttons = [
    k.status === 'provisional' || k.status === 'stale' ? action('accept', 'Accept', 'primary', (c) => run(c, 'review', { accept: true }, (o) => `Accepted knowledge #${k.id}.${revised(o)}`)) : null,
    k.status === 'provisional' || k.status === 'stale' ? action('reject', 'Reject', null, (c) => run(c, 'review', { accept: false }, (o) => `Rejected knowledge #${k.id}.${revised(o)}`)) : null,
    live ? action('refresh', 'Refresh', null, (c) => run(c, 'refresh', {}, (t) => `Re-checking knowledge #${k.id}: research task queued (${modeLabel(t.mode)}).`)) : null,
    live ? action('archive', 'Archive', null, (c) => run(c, 'archive', {}, () => `Archived knowledge #${k.id}; it is kept for history only.`)) : null,
    action('forget', 'Forget', 'danger', async (c) => {
      if (!(await confirmAction('Forget this knowledge?', `“${what}” and every earlier version of it are deleted from memory. This cannot be undone.`, 'Forget'))) return;
      run(c, 'forget', {}, () => `Forgot knowledge #${k.id} and its version history.`);
    }),
  ].filter(Boolean);
  const superseded = (k.versions ?? []).filter((v) => v.id !== k.id && (v.status === 'superseded' || v.superseded_by));
  return h('article', { class: 'card', 'data-knowledge': k.id, 'aria-labelledby': `k-title-${k.id}` },
    h('h2', { id: `k-title-${k.id}`, class: 'kstatement', tabindex: '-1', text: k.statement }),
    h('p', { class: 'badges' }, badge(cap(k.status), KNOWLEDGE_TONE[k.status] ?? 'neutral'), k.stale && k.status !== 'stale' ? badge('Stale', 'warn') : null,
      k.contested ? badge('Contested', 'warn') : null, badge(`confidence ${pct(k.confidence)}`, 'neutral'), ` #${k.id} · version ${k.version}`),
    // T1-18: dates are labelled by provenance; the owner's own assertion was never verified against sources
    kv([['Freshness class', k.freshness_class], ...(k.provenance === 'user_asserted'
      ? [['Asserted by you', `on ${fmtTime(k.first_verified_at)} (${ago(k.first_verified_at)})`]]
      : [[k.status === 'provisional' ? 'Last checked against sources' : 'Last verified', `${fmtTime(k.last_verified_at)} (${ago(k.last_verified_at)})`], [k.status === 'provisional' ? 'First seen' : 'First verified', fmtTime(k.first_verified_at)]]),
      k.stale ? ['Why stale', String(k.stale_reason ?? '').replace(/^stale:\s*/i, '')] : null, k.note ? ['Note', k.note] : null, k.pending_id ? ['Open question', `#${k.pending_id}`] : null]),
    h('h3', { text: 'Sources' }), k.sources.length ? h('ul', { class: 'reasons' }, k.sources.map((s) => h('li', {}, h('span', { text: `${s.title || s.domain} · ${fmtDay(s.published_at)} · ` }), safeLink(s.url))))
      : empty('No sources recorded.'),
    h('details', { id: `k-versions-${k.id}` }, h('summary', { text: `Version history (${(k.versions ?? []).length})` }),
      table('Version history', [{ label: 'Version', num: true }, 'Status', 'Statement', 'Valid from', 'Valid until'],
        (k.versions ?? []).map((v) => [v.version, v.status, h('span', { text: v.statement }), fmtTime(v.valid_from), fmtTime(v.valid_until)]))),
    superseded.length || k.feedback?.length ? h('div', { class: 'changed' }, h('h3', { text: 'What changed' }),
      superseded.length ? h('ul', { class: 'reasons' }, superseded.map((v) => h('li', { text: `Version ${v.version} (${v.status}) said: ${v.statement}` }))) : null,
      k.feedback?.length ? h('ul', { class: 'reasons' }, k.feedback.map((f) => h('li', {
        text: `${fmtTime(f.created_at)}: ${String(f.effect).replaceAll('_', ' ')} (confidence ${pct(f.before_confidence)} → ${pct(f.after_confidence)})` }))) : null) : null,
    technical(kv([['Key', h('code', { text: k.key ?? '—' })],
      k.feedback?.length ? ['Information gained', k.feedback.map((f) => `${fmtNum(f.info_gain ?? 0, 3)} bits`).join(', ')] : null])),
    h('div', { class: 'buttons', role: 'group', 'aria-label': `Actions for knowledge #${k.id}` }, buttons), status);
}

// ---- autonomy (D4; docs/autonomy/RESEARCH_POLICY.md) -----------------------------------------------------
// GET /api/web/autonomy: the switch and its owner record, both daily budgets (UTC day), policy, the last ten decisions
// and, while web access is on, the current ranking. POST /api/web/autonomy records the owner's switch and limits.

const AUTO_POLL_MS = 15000;
const AUTO_SCOPES = [['autonomous', 'Autonomous research'], ['user', 'Your research']];
const AUTO_METRICS = [['searches', 'Searches', 'count'], ['pages', 'Pages read', 'count'], ['seconds', 'Active time', 'min'],
                      ['spend_usd', 'External spend', 'usd']];
const SIGNAL_TEXT = { goal: 'Matches an active goal', contradiction: 'Contradiction', hypothesis: 'Open hypothesis',
  prediction_uncertainty: 'Uncertain prediction', source_change: 'A monitored source changed', stale: 'Past its freshness window',
  gap: 'Only one source' };
const CANDIDATE_KIND = { contested: 'Contested knowledge', stale: 'Stale knowledge', prediction: 'Prediction due',
  gap: 'Single-source gap', hypothesis: 'Contested subject', hypothesis_prediction: 'Open hypothesis' };
const auto = { status: null, sig: '', dirty: false, timer: null };

const two = (x) => (Number(x) || 0).toFixed(2);
const three = (x) => (Number(x) || 0).toFixed(3);
const minutes = (seconds) => +((Number(seconds) || 0) / 60).toFixed(1);
// One amount in the unit of its limit: counts, minutes or US dollars (the API stores seconds and dollars).
function amount(x, unit, withUnit = true) {
  if (unit === 'usd') return `$${two(x)}`;
  if (unit === 'min') return `${minutes(x)}${withUnit ? ' min' : ''}`;
  return String(Math.round(Number(x) || 0));
}
function duration(seconds) {
  const s = Number(seconds) || 0;
  if (s < 120) return `${+s.toFixed(1)} s`;
  if (s < 7200) return `${+(s / 60).toFixed(1)} minutes`;
  return `${+(s / 3600).toFixed(1)} hours`;
}
// Server reasons give durations in seconds ("within the last 86400 s"): say them in minutes or hours.
const humane = (text) => cap(String(text ?? '').replace(/\b(\d+(?:\.\d+)?) s\b/g, (_, n) => duration(Number(n))));
function ownerText(by) {
  return { 'owner:console': 'you, in this console', 'owner:cli': 'you, on the command line', owner: 'you' }[by] ?? by ?? 'unknown';
}

async function loadAutonomy() {
  loadNodes();
  const s = await load('autonomy', ['autoState', 'autoBudgets', 'autoCandidates', 'autoDecisions'].map($),
    () => api('GET', '/api/web/autonomy'));
  if (!s) return;
  auto.sig = '';
  renderAutonomy(s);
  scheduleAutoPoll();
}

// ---- research nodes (ADR-042 draft; RESEARCH_POLICY.md section 6) ----
// GET /api/web/nodes: nodes with their key algorithm and fingerprint, attested enrolments waiting for the owner and
// this Spark's own fingerprint. POST /api/web/nodes enrols a pending device by fingerprint, rejects it, or revokes.
const ALG_TEXT = { Ed25519: 'Ed25519', 'ECDSA-P256': 'ECDSA P-256' };
const NODE_STATE = { enrolled: ['Enrolled, signs its requests', 'ok'], reenrol: ['Must enrol again', 'warn'], revoked: ['Revoked, key deleted', 'neutral'] };
let nodesSig = '';
async function loadNodes(quiet = false) {
  const s = quiet ? await api('GET', '/api/web/nodes').catch(() => null) : await load('nodes', [$('autoNodes')], () => api('GET', '/api/web/nodes'));
  if (s) renderNodes(s);
}
const fp = (text) => h('code', { class: 'fp', text });
function renderNodes(s) {
  const sig = JSON.stringify(s);
  if (sig === nodesSig && $('autoNodes').querySelector(':scope > p.sub')) { $('autoNodes').removeAttribute('aria-busy'); return; }   // already shown
  nodesSig = sig;
  const out = [h('p', { class: 'sub' }, s.spark ? ['This Spark’s own fingerprint, which each device pins when it pairs: ', fp(s.spark.fingerprint)]
    : 'This Spark’s signing key is created when Pocket first starts with this individual.')];
  if (s.pending.length) {
    out.push(h('h3', { text: 'Waiting for you' }), h('ul', { class: 'nodes', id: 'nodePending' }, s.pending.map((p, i) => {
      const id = `nodeName${i}`;
      return h('li', { 'data-state': 'pending' },
        h('p', {}, `Pocket pairing ${p.pocket_label} (family ${p.family}) proved an ${ALG_TEXT[p.alg] ?? p.alg} key, ${ago(p.attested_at)}. Its fingerprint:`),
        h('p', {}, fp(p.fingerprint)),
        h('p', { class: 'sub', text: 'Enrol it only if the device shows exactly this fingerprint under “This device as a research node”.' }),
        h('label', { for: id, text: 'Name of the node (what your devices hand research to)' }),
        h('input', { id, value: p.pocket_label, maxlength: 64, autocomplete: 'off', spellcheck: 'false' }),
        h('div', { class: 'buttons' },
          h('button', { type: 'button', class: 'primary', text: 'Fingerprints match: enrol', 'aria-label': `Fingerprints match: enrol ${p.fingerprint}`,
            onclick: (ev) => act(ev.currentTarget, $('autoNodesStatus'), async () => {
              const label = $(id).value.trim();
              if (!label) { $(id).focus(); return 'Enter a name for the node first.'; }
              renderNodes(await api('POST', '/api/web/nodes', { action: 'enrol', fingerprint: p.fingerprint, label }));
              return `Enrolled “${label}” with fingerprint ${p.fingerprint}. Its requests are now signed.`;
            }) }),
          h('button', { type: 'button', text: 'Reject', 'aria-label': `Reject ${p.fingerprint}`,
            onclick: (ev) => act(ev.currentTarget, $('autoNodesStatus'), async () => {
              renderNodes(await api('POST', '/api/web/nodes', { action: 'reject', fingerprint: p.fingerprint }));
              return 'Enrolment request rejected. Nothing was enrolled.';
            }) })));
    })));
  }
  out.push(h('h3', { text: 'Nodes' }));
  if (!s.nodes.length) out.push(empty('No research node yet.'));
  else out.push(h('ul', { class: 'nodes', id: 'nodeList' }, s.nodes.map((n) => {
    const [word, tone] = NODE_STATE[n.state] ?? [n.state, 'neutral'];
    return h('li', { 'data-state': n.state },
      h('p', {}, h('strong', { text: n.label }), ' ', badge(word, tone), ` · family ${n.family} · enrolled ${fmtTime(n.enrolled_at)}`),
      n.fingerprint ? h('p', {}, fp(n.fingerprint), ` · ${ALG_TEXT[n.alg] ?? n.alg}`) : null,
      n.state === 'reenrol' ? h('p', { class: 'sub', text: 'Enrolled before nodes signed their requests: it must enrol again from its Research panel before it can take or hand off research.' }) : null,
      n.state === 'revoked' ? null : h('div', { class: 'buttons' }, h('button', { type: 'button', text: 'Revoke', 'aria-label': `Revoke ${n.label}`,
        onclick: async (ev) => {
          const button = ev.currentTarget;
          if (!await confirmAction(`Revoke “${n.label}”?`, 'It loses its research sessions and its key is deleted, so nothing it signs is accepted any more. It stays paired and can still ask and read research. To make it a node again, it must enrol again.', 'Revoke')) return;
          act(button, $('autoNodesStatus'), async () => {
            renderNodes(await api('POST', '/api/web/nodes', { action: 'revoke', label: n.label }));
            return `Revoked “${n.label}”: its key was deleted.`;
          });
        } })));
  })));
  fill($('autoNodes'), out);
}

// Budgets move while research runs anywhere (worker, Pocket, CLI): poll the open view, re-render only on a change.
function scheduleAutoPoll() {
  clearTimeout(auto.timer);
  auto.timer = null;
  if (!state.token || state.view !== 'autonomy') return;
  auto.timer = setTimeout(async () => {
    auto.timer = null;
    if (state.view !== 'autonomy') return;
    if (!document.hidden && !state.offline) {
      try { renderAutonomy(await api('GET', '/api/web/autonomy')); } catch { /* retried on the next tick */ }
      if (!$('autoNodes').contains(document.activeElement)) await loadNodes(true);   // never under the owner's typing
    }
    scheduleAutoPoll();
  }, AUTO_POLL_MS);
}

function renderAutonomy(s) {
  const sig = JSON.stringify(s);
  if (sig === auto.sig) return;
  const before = auto.status;
  auto.sig = sig;
  auto.status = s;
  const box = $('autoSwitch');
  if (!state.busy.has(box)) box.checked = !!s.enabled;
  $('autoSwitchWord').textContent = box.checked ? 'On' : 'Off';
  rerender($('autoState'), autoStateBlock(s));
  rerender($('autoBudgets'), autoBudgetsBlock(s));
  if (s.candidates !== undefined) rerender($('autoCandidates'), autoCandidatesBlock(s.candidates, s));
  rerender($('autoDecisions'), autoDecisionsBlock(s.decisions ?? []));
  if (!auto.dirty) fillLimits(s);
  if (before) announceBudget(before, s);
}

function autoStateBlock(s) {
  const r = s.record ?? {};
  const [word, tone] = s.enabled ? ['On', 'ok'] : s.switch ? ['Switch on, not recorded', 'warn'] : ['Off', 'neutral'];
  const a = s.autonomous;
  const out = [kv([
    ['State', h('span', { id: 'autoStateBadge' }, badge(word, tone))],
    ['Enabled by', r.enabled_by ? `${ownerText(r.enabled_by)}, ${fmtTime(r.enabled_at)}` : 'never enabled'],
    r.disabled_at ? ['Last switched off', `${ownerText(r.disabled_by)}, ${fmtTime(r.disabled_at)}`] : null,
    ['Running now', `${a.running ?? 0} of ${a.limits.concurrent ?? 0} autonomous investigations`],
    ['Idle check', `at most every ${duration(s.idle_interval)}`],
    ['Cooldown', `a subject waits ${duration(s.cooldown)} before it is investigated again`],
  ])];
  if (s.switch && !s.enabled) {
    out.push(h('p', { class: 'msg error' }, 'The switch is on but no owner decision is recorded (it was set without one), so the idle investigator stays off. Switch it on here to record your decision.'));
  }
  if (s.candidates === null) {
    out.push(h('p', { class: 'sub' }, 'Web access is off, so nothing runs whatever this switch says. Turn it on under ',
      h('a', { href: '#research', text: 'Research → Web settings' }), '.'));
  }
  return out;
}

// A budget as segmented meters: solid = used, hatched = held by tasks still running (their allowance), rest = left.
function meter(scope, key, name, unit, values) {
  const { used, limit, held = 0, left, free = false } = values;
  // as autonomy._exhausted: fewer than one search, page or second left (spend: nothing left)
  const out = limit <= 0 || (unit === 'usd' ? left <= 0 : left < 1) ? 'out' : left / limit < 0.2 ? 'low' : 'ok';
  const pctOf = (x) => (limit > 0 ? Math.max(0, Math.min(100, (x / limit) * 100)) : 0);
  const usedBar = h('span', { class: 'used' }), heldBar = h('span', { class: 'held' });
  usedBar.style.width = `${pctOf(used)}%`;
  heldBar.style.width = `${Math.min(100 - pctOf(used), pctOf(held))}%`;
  const foot = [free ? (out === 'out' ? 'No free slot: new investigations wait' : `${amount(left, unit)} free`) : out === 'out' ? 'None left today' : `${amount(left, unit)} left`];
  if (held > 0) foot.push(`${amount(held, unit)} held by running tasks`);
  return h('li', { class: 'meter', 'data-key': key, 'data-state': out },
    h('div', { class: 'meterhead' }, h('span', { class: 'metername', id: `m-${scope}-${key}`, text: name }),
      h('span', { class: 'meterval' }, h('strong', { text: amount(used, unit, unit === 'usd') }), ` of ${amount(limit, unit)}`)),
    h('div', { class: 'mtrack', 'aria-hidden': 'true' }, usedBar, heldBar),
    h('p', { class: 'meterfoot', text: foot.join(' · ') }));
}

function autoBudgetsBlock(s) {
  const panels = AUTO_SCOPES.map(([scope, title]) => {
    const b = s[scope];
    const meters = AUTO_METRICS.map(([key, name, unit]) => {
      const used = Number(b.used[key]) || 0, committed = Number(b.committed[key]) || 0;
      return meter(scope, key, name, unit, { used, limit: Number(b.limits[key]) || 0, held: Math.max(0, committed - used),
                                             left: Number(b.remaining[key]) || 0 });
    });
    if (scope === 'autonomous') {
      const limit = Number(b.limits.concurrent) || 0, running = Number(b.running) || 0;
      meters.push(meter(scope, 'concurrent', 'At the same time', 'count', { used: running, limit, left: Math.max(0, limit - running), free: true }));
    }
    const observed = h('dl', { class: 'kv observed' },
      h('dt', { text: 'SearXNG requests' }), h('dd', { 'data-key': 'searxng_requests', text: amount(b.used.searxng_requests, 'count') }),
      h('dt', { text: 'Upstream engine requests' }), h('dd', { 'data-key': 'engine_requests', text: amount(b.used.engine_requests, 'count') }));
    return h('section', { class: 'scope', 'data-scope': scope, 'data-exhausted': b.refusal ? '' : null, 'aria-labelledby': `bud-${scope}` },
      h('div', { class: 'scopehead' }, h('h3', { id: `bud-${scope}`, text: title }),
        b.refusal ? badge('Exhausted', 'warn') : badge('Room left', 'ok')),
      b.refusal ? h('p', { class: 'refusal', text: `New ${scope === 'user' ? 'research of yours' : 'investigations'} are refused: ${b.refusal}.` }) : null,
      h('ul', { class: 'meters', 'aria-label': `${title}: today’s budget` }, meters),
      observed,
      h('p', { class: 'meterfoot', text: `${b.tasks} task${b.tasks === 1 ? '' : 's'} charged today, ${b.open} still running. SearXNG and engine requests are observed, not limited; cache hits count as neither.` }));
  });
  const prices = s.prices_usd ?? {};
  return [h('p', { class: 'sub', text: `UTC day ${s.day}. Prices: $${two(prices.search)} per search, $${two(prices.page)} per page${!Number(prices.search) && !Number(prices.page) ? ' (no paid provider, so spend stays at $0.00)' : ''}.` }),
          h('div', { class: 'budgets' }, panels)];
}

// Changes since the last render, for screen readers (aria-live polite): only the amounts left that moved.
function announceBudget(before, s) {
  const words = [];
  for (const [scope, title] of AUTO_SCOPES) {
    for (const [key, name, unit] of AUTO_METRICS) {
      const was = before[scope]?.remaining?.[key], now = s[scope]?.remaining?.[key];
      if (was !== now && now !== undefined) words.push(`${title}, ${name.toLowerCase()}: ${amount(now, unit)} of ${amount(s[scope].limits[key], unit)} left`);
    }
    if (!before[scope]?.refusal && s[scope]?.refusal) words.push(`${title} budget exhausted`);
  }
  if (words.length) $('autoLive').textContent = `Budget updated. ${words.join('; ')}.`;
}

function candidateTitle(x) {
  return x.statement || x.objective || CANDIDATE_KIND[x.kind] || 'An open question';
}

function signalList(signals) {
  const on = Object.entries(signals ?? {}).filter(([, v]) => Number(v) > 0).sort((a, b) => b[1] - a[1]);
  if (!on.length) return h('p', { class: 'candmeta', text: 'No weighted signal: ranked on expected gain alone.' });
  return h('ul', { class: 'signals', 'aria-label': 'Signals' },
    on.map(([k, v]) => {
      const name = SIGNAL_TEXT[k] ?? k.replaceAll('_', ' ');
      const tip = `${name}: weight ${two(v)} on a 0 to 1 scale. A higher weight ranks this question earlier.`;
      return h('li', { 'data-signal': k, title: tip, 'aria-label': tip }, name, h('span', { class: 'sv', 'aria-hidden': 'true', text: two(v) }));
    }));
}

function autoCandidatesBlock(c, s) {
  if (c === null) return [empty('Candidates are ranked while web access is on; with it off nothing can be researched.')];
  const out = [];
  if (!s.enabled && c.chosen.length) out.push(h('p', { class: 'sub', text: 'Autonomy is off: this is what it would investigate. Nothing is queued.' }));
  // The API can rank the same statement twice (for example as contested knowledge and as an open hypothesis). Show it
  // once, at its best rank, and name the other reasons it was ranked.
  const chosen = [];
  for (const x of c.chosen) {
    const first = chosen.find((y) => candidateTitle(y) === candidateTitle(x));
    if (first) first.alsoKinds.push(CANDIDATE_KIND[x.kind] ?? x.kind); else chosen.push({ ...x, alsoKinds: [] });
  }
  out.push(h('h3', { text: `Would investigate (${chosen.length})` }));
  out.push(chosen.length
    ? h('ol', { class: 'candidates', id: 'autoChosen' }, chosen.map((x) => h('li', { 'data-key': x.key }, h('div', {},
        h('p', { class: 'kstatement', text: candidateTitle(x) }),
        h('p', { class: 'candmeta', text: `${CANDIDATE_KIND[x.kind] ?? x.kind}${x.alsoKinds.length ? ` (also: ${x.alsoKinds.join(', ')})` : ''} · ${modeLabel(x.mode)} · priority ${three(x.priority)} · gain ${three(x.info_gain)} bits` }),
        signalList(x.signals),
        technical(kv([['Ranking', x.reason], ['Cost', three(x.cost)], ['Search terms', x.objective || '—'], ['Index key', h('code', { text: x.key })]]))))))
    : empty(c.refused.length ? 'Nothing clears the bar now: every candidate was refused for the reason shown below.'
      : 'Nothing in the records gives a reason to research: no contested, stale or single-source knowledge and no open hypothesis with a prediction.'));
  if (c.refused.length) {
    out.push(h('h3', { text: `Refused (${c.refused.length})` }),
      h('ul', { class: 'refused', id: 'autoRefused' }, c.refused.map((x) => h('li', { 'data-key': x.key },
        h('strong', { text: candidateTitle(x) }),
        h('p', { class: 'refreason' }, badge('Refused', 'warn'), ' ', humane(x.reason)),
        h('p', { class: 'candmeta', text: `gain ${three(x.info_gain)} bits · minimum ${c.min_gain}` })))));
  }
  return out;
}

const POLICY_WORDS = { autonomous_daily: 'autonomous', user_daily: 'your', prices_usd: 'price per' };
function changeText(changes) {
  const parts = [];
  for (const [key, value] of Object.entries(changes ?? {})) {
    if (value && typeof value === 'object') {
      for (const [k, v] of Object.entries(value)) {
        const unit = k === 'seconds' ? 'min' : k === 'spend_usd' || key === 'prices_usd' ? 'usd' : 'count';
        const name = { spend_usd: 'spend', seconds: 'active time', concurrent: 'investigations at once' }[k] ?? k;
        parts.push(`${POLICY_WORDS[key] ?? key} ${name} ${amount(v, unit)}`);
      }
    } else {
      parts.push(key === 'min_gain' ? `minimum gain ${value} bits` : `${key.replaceAll('_', ' ')} ${duration(value)}`);
    }
  }
  return parts.join('; ') || 'no values';
}

function decisionParts(d) {
  const a = d.autonomy;
  if (a && typeof a === 'object') {
    return ['chosen', 'Chosen', 'ok', [
      h('p', { class: 'dectext', text: `${CANDIDATE_KIND[a.kind] ?? a.kind}, researched as “${modeLabel(a.mode)}”: priority ${three(a.priority)}, gain ${three(a.info_gain)} bits.` }),
      signalList(a.signals),
      d.task_id ? h('p', { class: 'dectext' }, h('a', { href: '#research', 'data-task': d.task_id, text: 'Open its task under Research' })) : null,
      technical(kv([['Ranking', a.reason], ['Cost', three(a.cost)], ['Minimum gain', `${a.min_gain} bits`], ['Index key', h('code', { text: a.key ?? '—' })]]))]];
  }
  const text = (t) => h('p', { class: 'dectext' }, ...rich(t));
  switch (a) {
    case 'refused': return ['refused', 'Refused', 'warn', [text(humane(d.reason ?? 'refused'))]];
    case 'nothing worth researching':
      return ['refused', 'Nothing researched', 'neutral', [text(humane(d.reason ?? 'no candidate')
        + (typeof d.best_gain === 'number' ? ` (best gain ${three(d.best_gain)} bits, minimum ${d.min_gain}).` : ''))]];
    case 'enabled': return ['switch', 'Switched on', 'info', [text(`Recorded for ${ownerText(d.by)}.`)]];
    case 'disabled': return ['switch', 'Switched off', 'info', [text(`Recorded for ${ownerText(d.by)}.`)]];
    case 'policy changed': return ['policy', 'Limits changed', 'info', [text(`${cap(changeText(d.changes))}.`)]];
    case 'notified':
      // R1-01: only a delivered question is a notification; an older record without one says it was not delivered
      if (d.pending_id) return ['finding', 'You were notified', 'ok', [text(`A meaningful finding: question #${d.pending_id} under Questions.`)]];
      // falls through: an older record without a question id was never delivered
    case 'finding not delivered':
      return ['finding', 'Not delivered', 'warn', [text('A meaningful finding was recorded, but no question reached you.'),
        d.task_id ? h('p', { class: 'dectext' }, h('a', { href: '#research', 'data-task': d.task_id, text: 'Open its task under Research' })) : null]];
    case 'no meaningful finding: no notification':
      return ['finding', 'No notification', 'neutral', [text('The investigation found nothing meaningful, so you were not notified.')]];
    default: return ['other', 'Decision', 'neutral', [text(cap(String(a)))]];
  }
}

function autoDecisionsBlock(decisions) {
  if (!decisions.length) return [empty('No autonomy decision yet. Decisions appear when you switch autonomy, change its limits, or the idle worker chooses or refuses an investigation.')];
  return [h('ol', { class: 'decisions', id: 'autoDecisionList' }, decisions.map((d) => {
    const [kind, title, tone, body] = decisionParts(d);
    return h('li', { 'data-kind': kind },
      h('div', { class: 'dechead' }, badge(title, tone), h('span', { class: 'dectime', text: `${fmtTime(d.at)} · ${ago(d.at)}` })), body);
  }))];
}

// ---- limits form ----

const limitInputs = () => [...$('autoLimits').querySelectorAll('input[data-key]')];
function storedLimit(s, input) {
  const { scope, key } = input.dataset;
  if (!scope) return s[key];
  return s[scope].limits[key];
}
function fillLimits(s) {
  for (const input of limitInputs()) {
    const v = storedLimit(s, input);
    input.value = input.dataset.key === 'seconds' ? String(+(Number(v) / 60).toFixed(2)) : String(v ?? '');
    setFieldError(input, null);
  }
}
function setFieldError(input, text) {
  let note = $(`${input.id}-err`);
  if (!note) {
    note = h('p', { class: 'fielderr', id: `${input.id}-err` });
    input.after(note);
    input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), note.id].filter(Boolean).join(' '));
  }
  note.textContent = text ?? '';
  if (text) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
}
// Returns {body, changes, errors}: integers for counts (at most 16 investigations at once), non-negative numbers
// for minutes and dollars, 0 to 1 bits for the minimum gain - the API's own rules, checked before sending.
function readLimits(s) {
  const body = {}, changes = [], errors = [];
  for (const input of limitInputs()) {
    const { scope, key } = input.dataset;
    const raw = input.value.trim(), label = $('autoLimits').querySelector(`label[for="${input.id}"]`).textContent;
    const integer = ['searches', 'pages', 'concurrent'].includes(key);
    let error = null;
    const value = Number(raw);
    if (raw === '') error = 'Enter a value.';
    else if (!Number.isFinite(value) || value < 0) error = 'Must be a number of 0 or more.';
    else if (integer && !/^\d+$/.test(raw)) error = 'Must be a whole number.';
    else if (key === 'concurrent' && value > 16) error = 'At most 16 at the same time.';
    else if (key === 'min_gain' && value > 1) error = 'At most 1 bit.';
    else if (!integer && value > 1e9) error = 'That is too large.';
    setFieldError(input, error);
    if (error) { errors.push(input); continue; }
    const next = key === 'seconds' ? Math.round(value * 60 * 1000) / 1000 : value;
    const was = Number(storedLimit(s, input));
    if (Math.abs(next - was) < 1e-9) continue;
    const scopeName = scope === 'autonomous' ? 'autonomous' : scope === 'user' ? 'your' : '';
    changes.push(`${scopeName ? `${scopeName} ` : ''}${label.replace(/\s*\(.*\)$/, '').toLowerCase()} ${input.dataset.key === 'seconds' ? `${minutes(was)} → ${minutes(next)} min` : key === 'spend_usd' ? `$${two(was)} → $${two(next)}` : `${was} → ${next}`}`);
    if (scope) (body[`${scope}_daily`] ??= {})[key] = next; else body[key] = next;
  }
  return { body, changes, errors };
}

function wireAutonomy() {
  $('refreshAutonomy').addEventListener('click', loadAutonomy);
  $('autoLimits').addEventListener('input', () => { auto.dirty = true; });
  $('autoReset').addEventListener('click', () => {
    auto.dirty = false;
    if (auto.status) fillLimits(auto.status);
    say($('autoLimitsStatus'), 'Changes undone: the fields show the stored limits.', 'ok');
  });
  $('autoLimits').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const s = auto.status;
    if (!s) return;
    const { body, changes, errors } = readLimits(s);
    if (errors.length) {
      say($('autoLimitsStatus'), `Nothing was saved: correct ${errors.length === 1 ? 'the marked field' : `the ${errors.length} marked fields`}.`, 'error');
      errors[0].focus();
      return;
    }
    if (!changes.length) { say($('autoLimitsStatus'), 'Nothing to save: the fields match the stored limits.', 'ok'); return; }
    act($('autoSave'), $('autoLimitsStatus'), async () => {
      await api('POST', '/api/web/autonomy', body);
      auto.dirty = false;
      await loadAutonomy();
      return `Saved: ${changes.join('; ')}.`;
    });
  });
  $('autoSwitch').addEventListener('change', async (ev) => {
    const box = ev.currentTarget, on = box.checked;
    if (on) {
      box.checked = false;   // stays off until the owner confirms
      $('autoSwitchWord').textContent = 'Off';
      const lim = auto.status?.autonomous?.limits ?? {};
      const ok = await confirmAction('Let the individual research on its own?',
        `When its worker is idle it may start investigations that its records give a reason for: up to ${lim.searches} searches, ` +
        `${lim.pages} pages, ${minutes(lim.seconds)} minutes of active time and $${two(lim.spend_usd)} a day, ${lim.concurrent} at a time, each with an ` +
        `expected gain of at least ${auto.status?.min_gain} bits. Nothing it finds is accepted without your review, and you are notified only of ` +
        'meaningful findings. Your decision is recorded with the time.' +
        (auto.status?.candidates === null ? ' Web access is off, so nothing runs until you turn it on.' : ''), 'Switch on');
      box.focus();
      if (!ok) { say($('autoSwitchStatus'), 'Autonomous research stays off. Nothing was recorded.', 'ok'); return; }
      box.checked = true;
    }
    $('autoSwitchWord').textContent = on ? 'On' : 'Off';
    act(box, $('autoSwitchStatus'), async () => {
      let s;
      try {
        s = await api('POST', '/api/web/autonomy', { enabled: on });
      } catch (error) {
        box.checked = !on;
        $('autoSwitchWord').textContent = box.checked ? 'On' : 'Off';
        throw error;
      }
      await loadAutonomy();
      return on ? `Autonomous research is on, recorded as enabled by ${ownerText(s.record.enabled_by)} at ${fmtTime(s.record.enabled_at)}.`
        : 'Autonomous research is off and the switch-off is recorded. No new investigation starts.';
    });
  });
  $('autoDecisions').addEventListener('click', (ev) => {
    const link = ev.target.closest('a[data-task]');
    if (!link) return;
    web.taskId = link.dataset.task;   // the Research view opens this task's report when it loads
    web.taskSig = '';
  });
}

// ---- controls ------------------------------------------------------------------------------------------

// A control stays disabled while its own request is in flight, whatever the data says. A button disabled by the
// data says why in a line after it, linked with aria-describedby (U-30).
function enable(id, allowed, why = null) {
  const el = $(id);
  el.disabled = state.busy.has(el) || !allowed;
  if (why === null) return;
  let note = $(`${id}-why`);
  if (!note) {
    note = h('p', { class: 'sub why', id: `${id}-why` });
    el.after(note);
    el.setAttribute('aria-describedby', [el.getAttribute('aria-describedby'), note.id].filter(Boolean).join(' '));
  }
  note.textContent = allowed ? '' : why;
}

const WEB_OFF_WHY = 'Turn on web access first.';

function syncControls() {
  const hasEpoch = !!state.epoch;
  for (const id of ['observeBtn', 'recordDecision', 'forgetEpoch']) enable(id, hasEpoch, 'Start or choose an epoch first.');
  $('forgetEpoch').textContent = hasEpoch ? `Forget epoch ${state.epoch}` : 'Forget epoch';
  enable('learnBtn', $('learnTrain').value && $('learnValidation').value, 'Needs a training and a validation dataset: simulate one first.');
  enable('recallBtn', $('recallDataset').value && state.graphs.some((g) => g.status === 'active'), 'Needs a stored dataset and an active graph.');
  const g = state.graph;
  const candidate = g?.status === 'candidate';
  enable('gPromote', candidate, g ? `Revision #${g.id} is ${g.status}: only a candidate can be promoted or rejected.` : '');
  enable('gReject', candidate);
  enable('gRejectReason', candidate);
  enable('gEvaluate', !!g);
  enable('fmapBtn', !!$('fmapDataset').value, 'Needs a stored dataset: simulate one first.');
  $('ckPrune').textContent = `Prune (keep ${$('ckKeep').value.trim() || 'N'})`;
  $('ckRebuild').textContent = `Rebuild stale archives (keep ${$('ckKeep').value.trim() || 'N'})`;
  $('obsRootLabel').textContent = CHANNEL_ROOT[$('obsChannel').value];
  const webOn = !!web.status?.config?.enabled;
  enable('askBtn', webOn, web.status ? WEB_OFF_WHY : '');
  enable('monitorBtn', webOn, web.status ? WEB_OFF_WHY : '');
  for (const b of document.querySelectorAll('#monitorList button')) {
    b.disabled = state.busy.has(b) || !webOn;
    b.setAttribute('aria-describedby', 'monitorBtn-why');
  }
  applyOffline();
}

// U8: Tab and Shift+Tab stay inside the open confirmation dialog.
function trapFocus(ev) {
  if (ev.key !== 'Tab') return;
  const items = [...ev.currentTarget.querySelectorAll('button:not(:disabled), input:not(:disabled), [href]')];
  if (!items.length) return;
  const first = items[0], last = items.at(-1);
  if (ev.shiftKey && (document.activeElement === first || !ev.currentTarget.contains(document.activeElement))) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && (document.activeElement === last || !ev.currentTarget.contains(document.activeElement))) { ev.preventDefault(); first.focus(); }
}

function wire() {
  $('confirmDialog').addEventListener('keydown', trapFocus);
  $('memKind').replaceChildren(...MEMORY_KINDS.map((k) => option(k, k)));
  $('memKind').value = 'research';
  $('memKind').addEventListener('change', loadMemory);
  $('kStatus').addEventListener('change', () => renderKnowledge());
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
      'All camera, word and reference evidence of this epoch is deleted and tombstoned. Beliefs return to their priors; ' +
      'the epoch\'s questions, decisions, belief revisions and detected changes are deleted, and checkpoints that contain the rows become stale. The epoch name stays.', 'Forget epoch');
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
  wireAutonomy();
  window.addEventListener('hashchange', () => { if (!takeToken()) route(true); });
}

// Privacy refusal (U-03, C-06): plain words naming what was found, the field marked invalid and focused, and an
// explicit owner override that resends with authorization 'owner-approved-disclosure' after a confirmation.
const PRIVATE_KINDS = { email: 'an e-mail address', phone: 'a phone number', api_key: 'an API key', jwt: 'a login token',
  iban: 'a bank account number (IBAN)', credit_card: 'a card number', mac_address: 'a device (MAC) address',
  ipv4: 'an IP address', ipv6: 'an IP address', file_path: 'a file path on this computer', private_term: 'a term you marked private',
  evidence_root: 'a name from this individual’s own evidence', root: 'a name from this individual’s own evidence',
  mixed_script: 'text that mixes alphabets (a look-alike trick)' };

function kindList(kinds) {
  const words = [...new Set(kinds.map((k) => PRIVATE_KINDS[k] ?? `private data (${k.replaceAll('_', ' ')})`))];
  if (!words.length) return 'private details';
  return words.length === 1 ? words[0] : `${words.slice(0, -1).join(', ')} and ${words.at(-1)}`;
}

function privacyKinds(error) {
  if (!(error instanceof ApiError) || error.status !== 400) return null;
  const m = /private data \(([^)]*)\)/i.exec(error.message);
  return m ? m[1].split(',').map((k) => k.trim()).filter(Boolean) : null;
}

function clearPrivacy() {
  if (web.privacy === null) return;
  web.privacy = null;
  $('askObjective').removeAttribute('aria-invalid');
  $('askDisclose').checked = false;
  $('askDiscloseRow').hidden = true;
}

// U-09/S-14: the backend may answer an identical running question with the existing task (deduplicated).
// The backend may also answer without queueing research (`kind`): from the stored history of the individual's own
// understanding ('history', T1-04), by asking which subject a follow-up means ('clarify', T1-10), or by refusing
// private data ('refused', a normal outcome rather than an HTTP error: T1-20). web.previous is this page session's
// last question (kept in memory only, never stored), sent so "Is that version secure?" is read about it.
async function submitResearch(disclose, route = null, asked = null) {
  const objective = asked ?? $('askObjective').value.trim();
  if (!objective) { say($('askStatus'), 'Write a question or objective first.', 'error'); $('askObjective').focus(); return; }
  if (asked === null && !askCount()) { $('askObjective').focus(); return; }
  const status = $('askStatus');
  let refused = null, clarify = null;
  await act($('askBtn'), status, async () => {
    const body = { objective, mode: $('askMode').value || 'auto' };
    if (disclose) body.authorization = 'owner-approved-disclosure';
    if (web.previous) body.previous = web.previous;
    if (route) body.route = route;
    let t;
    try {
      t = await api('POST', '/api/web/tasks', body);
    } catch (error) {
      refused = privacyKinds(error);
      if (refused) return null;
      throw error;
    }
    if (t.kind === 'refused') { refused = t.kinds ?? []; return null; }
    if (t.kind === 'clarify') { clarify = t.clarify; return null; }
    $('askObjective').value = '';
    clearPrivacy();
    askCount();
    web.previous = t.objective;
    if (t.kind === 'history') {
      showHistory(t);
      return `Answered from what this individual has stored about “${short(t.topic, 60)}”: no web search was made.`;
    }
    showHistory(null);
    web.taskId = t.id;
    web.taskSig = '';
    await refreshTasks();
    const read = t.resolved_followup ? ` “${t.resolved_followup.anaphor}” was read as ${t.resolved_followup.subject}, from your previous question.` : '';
    if (t.deduplicated || t.existing) return `Already running — opened the existing task. Its progress and report are under Report.${read}`;
    return `Research queued (${modeLabel(t.mode)}): “${short(t.objective, 90)}”.${read} Its progress and report appear under Report.`;
  });
  if (clarify) {
    say(status, clarify, 'error');
    $('askObjective').focus();
  }
  if (refused) {
    web.privacy = refused;
    say(status, `Nothing was sent. Your question contains ${kindList(refused)}. Remove personal details such as e-mail addresses, ` +
      'phone numbers or names you marked private, or tick “Send these details to search providers anyway”.', 'error');
    $('askObjective').setAttribute('aria-invalid', 'true');
    $('askDiscloseRow').hidden = false;
    $('askDisclose').checked = false;
    $('askObjective').focus();
  }
}

// T1-04: "What has changed in your understanding of X?" answered from evolution.changes: the template summary, the
// stored versions (with supersession) and the dated timeline; a fresh web run is a separate, explicit action.
function showHistory(t) {
  const slot = $('askHistory');
  if (!t) { slot.hidden = true; slot.replaceChildren(); return; }
  const hist = t.history ?? {};
  const subjects = [...(hist.subjects ?? [])].sort((a, b) => (b.versions?.length ?? 0) - (a.versions?.length ?? 0));
  const versions = subjects.flatMap((s) => (s.versions ?? []).map((v) => [`v${v.version}`, cap(v.status),
    v.statement, fmtTime(v.times?.valid_from), v.superseded_by ? `#${v.superseded_by}` : '—']));
  const events = subjects.flatMap((s) => s.timeline ?? []).sort((a, b) => (a.at ?? 0) - (b.at ?? 0)).slice(-20);
  slot.replaceChildren(
    h('h3', { id: 'h-history', text: `What changed in the understanding of “${t.topic}”` }),
    h('p', { id: 'historySummary', text: hist.summary?.text ?? '' }),
    h('p', { class: 'sub', text: `${hist.summary?.label ?? ''} Taken from stored versions, supersessions, evidence and hypotheses; no web search was made.` }),
    versions.length ? table('Stored versions', ['Version', 'Status', 'Statement', 'Valid from', 'Superseded by'], versions, { showCaption: true })
      : empty('No knowledge version about this topic is stored.'),
    events.length ? table('Timeline', ['Date', 'Event', 'Detail'], events.map((e) => [e.date ?? fmtTime(e.at), cap(String(e.kind).replaceAll('_', ' ')), e.text]), { showCaption: true })
      : null,
    h('button', { type: 'button', id: 'historyResearch', text: 'Research this on the web now',
      onclick: () => submitResearch(false, 'research', t.research?.objective ?? t.objective) }));
  slot.hidden = false;
}

// C-13: the limit is in bytes (UTF-8), so CJK text runs out sooner than the character count suggests.
function askCount() {
  const text = $('askObjective').value;
  const chars = [...text].length;
  const bytes = new TextEncoder().encode(JSON.stringify({ objective: text, mode: 'technical' })).length;
  const left = MAX_TEXT_BODY - 200 - bytes;
  const el = $('askCount');
  if (chars > 2000) { say(el, `${chars} characters: the limit is 2000. Shorten the question; nothing is cut off for you.`, 'error'); return false; }
  if (left < 0) { say(el, `Too long by ${-left} bytes. Shorten the question.`, 'error'); return false; }
  el.className = 'msg sub';
  el.textContent = chars ? `${chars} of 2000 characters` : '';
  return true;
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
  $('askForm').addEventListener('submit', (ev) => { ev.preventDefault(); submitResearch(false); });
  $('askObjective').addEventListener('input', () => { clearPrivacy(); askCount(); });
  $('askDisclose').addEventListener('change', async (ev) => {
    if (!ev.currentTarget.checked) return;
    const kinds = web.privacy ?? [];
    const ok = await confirmAction('Send private details to search providers?',
      `Your question contains ${kindList(kinds)}. If you continue, the question is sent as written to search providers and to the sites they return, ` +
      'which may log it. This is recorded as your authorisation for this question only.', 'Send anyway');
    if (!ok) { ev.target.checked = false; ev.target.focus(); return; }
    submitResearch(true);
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
  dispatchEvent(new CustomEvent('nsf:session'));   // presence (presence/web/console-boot.mjs) stops polling
  $('lockedText').replaceChildren(...rich(text));
  for (const id of VIEWS) $(id).hidden = true;
  $('nav').hidden = true;
  $('locked').hidden = false;
}

function takeToken() {
  const match = /^#token=([A-Za-z0-9_-]{16,128})$/.exec(location.hash);
  if (!match) return false;
  state.token = match[1];
  history.replaceState(null, '', location.pathname + location.search);
  dispatchEvent(new CustomEvent('nsf:session'));
  start();
  return true;
}
// The presence element reads the session token through this getter (it never stores it).
window.nsfConsoleToken = () => state.token;

const LOADERS = { overview: loadOverview, teach: loadTeach, evidence: loadEvidence, questions: loadQuestions,
                  research: loadResearch, autonomy: loadAutonomy, knowledge: loadKnowledge, graphs: loadGraphs, checkpoints: loadCheckpoints,
                  memory: loadMemory, voice: loadVoice };

// Voice Cortex panel (src/neuroscifi/voice/web): loaded on first visit; it keeps its own session and event stream.
function loadVoice() {
  import('/voice/voice-ui.mjs').then((m) => m.mountConsole($('voiceMount'), state.token))
    .catch((error) => { $('voiceMount').textContent = `The voice panel could not load: ${errorText(error)}`; });
}

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
  showCurrentNav();
  if (moveFocus) $(`h-${view}`).focus();
  schedulePoll();
  scheduleAutoPoll();
  refreshView();
  if (view !== 'overview') loadHeader().catch(() => {});   // nav counts stay current on every view (U-29)
}

// U-12: below 1000px the navigation is one scrollable row; keep the current item in view without moving the page.
function showCurrentNav() {
  const list = $('nav').querySelector('ul');
  const current = list.querySelector('a[aria-current="page"]');
  if (!current || list.scrollWidth <= list.clientWidth) return;
  const item = current.parentElement;
  const left = item.offsetLeft - list.offsetLeft;
  if (left < list.scrollLeft || left + item.offsetWidth > list.scrollLeft + list.clientWidth) {
    list.scrollLeft = Math.max(0, left - (list.clientWidth - item.offsetWidth) / 2);
  }
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
