// The owner console's "Made and kept" view (OMNIMIND lane U, ADR-079): what the owner kept or NeuroSciFi made, shown
// under the console's unchanged Content-Security-Policy. Nothing from an artifact is ever written as markup: pictures
// are parsed as SVG (inert) and rebuilt element by element through an allow-list (importSvg); tables and documents are
// built with DOM calls from the JSON the server sends; sound is decoded and played through Web Audio; an explorable
// model is lane G2's first-party runtime mounted into the pane; a raster picture comes from a short-lived address the
// server mints for this session. console.js passes its helpers in (ctx), so this view looks and behaves like the rest.

const SVG = 'http://www.w3.org/2000/svg';
// Exactly lane G1's safety check (artifacts/check/svg_safety.py ELEMENTS, ATTRIBUTES, the aria-/data- prefixes).
// "style" and "metadata" are allowed there but left out here without counting as dropped: the picture's own
// stylesheet would be blocked by the console's policy (its paints come from the console's tokens instead), and the
// data block is for editing, not for looking at.
export const SVG_ELEMENTS = new Set(['svg', 'g', 'title', 'desc', 'defs', 'pattern', 'clipPath', 'linearGradient', 'stop',
  'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'path', 'text', 'tspan']);
const QUIET = new Set(['style', 'metadata']);
export const SVG_ATTRIBUTES = new Set(['id', 'class', 'transform', 'x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry',
  'x1', 'y1', 'x2', 'y2', 'points', 'd', 'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-dasharray',
  'stroke-dashoffset', 'stroke-linecap', 'stroke-linejoin', 'stroke-opacity', 'stroke-miterlimit', 'opacity', 'font-size',
  'font-family', 'font-weight', 'font-style', 'text-anchor', 'dominant-baseline', 'letter-spacing', 'viewBox',
  'preserveAspectRatio', 'role', 'lang', 'patternUnits', 'patternTransform', 'gradientUnits', 'offset', 'stop-color',
  'stop-opacity', 'clip-path', 'clipPathUnits', 'dx', 'dy', 'paint-order', 'vector-effect', 'shape-rendering', 'color',
  'visibility', 'display', 'focusable', 'tabindex']);
const TEXTUAL = new Set(['id', 'class', 'role', 'lang']);
const URL_REF = /url\(\s*(['"]?)([^)'"]*)\1\s*\)/gi;
const ACTIVE = /javascript:|vbscript:|data:|expression\s*\(|@import/i;
let imports = 0;

// A parsed document is held to this page's policy too, so a <style> element or a style attribute in it would be
// reported as a refused inline style although nothing is applied. Both are taken out of the text before it is parsed
// (they would be dropped by the allow-list anyway); `styles` counts the attributes taken out.
const STYLE_ELEMENT = /<style\b[^>]*>[\s\S]*?<\/style\s*>/gi;
const STYLE_ATTRIBUTE = /\sstyle\s*=\s*("[^"]*"|'[^']*')/gi;
function unstyled(text) {
  let styles = 0;
  const out = String(text).replace(STYLE_ELEMENT, '').replace(STYLE_ATTRIBUTE, () => { styles += 1; return ' '; });
  return { text: out, styles };
}

// Rebuild an SVG picture from its text: {node, dropped} where dropped names (with counts) everything left out. The
// text is parsed as XML (a parsed document runs nothing and loads nothing); only allow-listed elements and attributes
// are copied, ids are renamed so two pictures never share one, and a url() may name only an element of the picture.
export function importSvg(text) {
  const clean = unstyled(text);
  const doc = new DOMParser().parseFromString(clean.text, 'image/svg+xml');
  const root = doc.documentElement;
  if (doc.getElementsByTagName('parsererror').length || root.namespaceURI !== SVG || root.localName !== 'svg') {
    return { node: null, dropped: [['it is not a picture this view can read', 1]] };
  }
  const dropped = new Map();
  const note = (what) => dropped.set(what, (dropped.get(what) ?? 0) + 1);
  if (clean.styles) dropped.set('the attribute style', clean.styles);
  const ids = new Set([...doc.querySelectorAll('[id]')].map((e) => e.getAttribute('id')));
  const prefix = `art${++imports}-`;
  const copy = (src) => {
    if (src.nodeType === Node.TEXT_NODE || src.nodeType === Node.CDATA_SECTION_NODE) return document.createTextNode(src.nodeValue);
    if (src.nodeType !== Node.ELEMENT_NODE) return null;          // comments, processing instructions
    if (src.namespaceURI !== SVG || !SVG_ELEMENTS.has(src.localName)) {
      if (!(src.namespaceURI === SVG && QUIET.has(src.localName))) note(`the element ${src.localName}`);
      return null;
    }
    const el = document.createElementNS(SVG, src.localName);
    for (const attr of [...src.attributes]) {
      const name = attr.name, low = name.toLowerCase();
      let value = attr.value;
      if (name === 'xmlns' || name.startsWith('xmlns:')) continue;  // a namespace declaration, not an attribute
      if (low.startsWith('on')) { note('an event handler'); continue; }
      if (low.includes('href')) { note('a link'); continue; }
      if (attr.namespaceURI || !(SVG_ATTRIBUTES.has(name) || name.startsWith('aria-') || name.startsWith('data-'))) {
        note(`the attribute ${name}`); continue;
      }
      const words = TEXTUAL.has(name) || name.startsWith('aria-') || name.startsWith('data-');
      if (!words && ACTIVE.test(value)) { note('an active value'); continue; }
      if (!words && /url\(/i.test(value)) {
        let inside = true;
        value = value.replace(URL_REF, (_, q, ref) => {
          ref = ref.trim();
          if (ref.startsWith('#') && ids.has(ref.slice(1))) return `url(#${prefix}${ref.slice(1)})`;
          inside = false;
          return 'none';
        });
        if (!inside || /url\(/i.test(value.replace(/url\(#[\w.-]+\)/g, ''))) { note('a reference outside the picture'); continue; }
      }
      if (name === 'id') value = prefix + value;
      if (name === 'aria-labelledby' || name === 'aria-describedby') {
        value = value.split(/\s+/).filter((x) => ids.has(x)).map((x) => prefix + x).join(' ');
      }
      el.setAttribute(name, value);
    }
    for (const kid of src.childNodes) { const c = copy(kid); if (c) el.append(c); }
    return el;
  };
  const node = copy(root);
  node.classList.add('art-svg');            // console.css: the theme's ink and paper, scaled to the pane
  node.removeAttribute('width');            // the viewBox keeps its proportions; the pane gives the size
  node.removeAttribute('height');
  if (!node.getAttribute('role')) node.setAttribute('role', 'img');
  return { node, dropped: [...dropped] };
}

// ---- state -------------------------------------------------------------------------------------------------------

let ctx = null;
let wired = false;
const view = { page: 0, list: null, chosen: new Set(), shown: null, mounted: null, audio: null, seq: 0, job: null };
const $ = (id) => document.getElementById(id);

async function raw(path, kind = 'text') {
  const response = await fetch(path, { cache: 'no-store', credentials: 'omit', headers: { Authorization: `Bearer ${ctx.token()}` } });
  if (!response.ok) throw new Error(`could not read it (HTTP ${response.status})`);
  return kind === 'text' ? response.text() : response.arrayBuffer();
}

export async function show(c) {
  ctx = c;
  if (!wired) wire();
  await loadList();
}

// Leaving the view: an explorable model is unmounted, a sound stops.
export function leave() {
  unmountPane();
  stopSound();
}

function unmountPane() {
  try { view.mounted?.unmount(); } catch { /* the runtime restores its element itself */ }
  view.mounted = null;
}

// ---- the list ----------------------------------------------------------------------------------------------------

async function loadList() {
  const { h, fill } = ctx;
  const slot = $('artifactList');
  const out = await ctx.load('artifacts', [slot], () => ctx.api('GET', `/api/artifacts?page=${view.page}`));
  if (!out) return;
  view.list = out;
  const counts = out.counts;
  $('artifactCounts').textContent = counts.given + counts.made
    ? `${counts.given} given by you · ${counts.made} made by NeuroSciFi (not evidence)` : '';
  $('artPrev').disabled = view.page === 0;
  $('artNext').disabled = !out.more;
  $('artPager').hidden = view.page === 0 && !out.more;
  $('uploadBox').hidden = !out.upload;
  $('uploadOff').hidden = !!out.upload;
  if (!out.items.length) {
    fill(slot, h('p', { class: 'empty' }, ctx.rich(view.page ? 'Nothing more.' : 'Nothing is kept yet. Keep a file with `nsf artifact add FILE`, or make something from one with `nsf artifact make FILE --request "…"`.')));
  } else {
    fill(slot, h('ul', { class: 'art-list' }, out.items.map(item)));
  }
  syncMake();
}

function provenanceBadge(x) {
  return x.made ? ctx.badge('Made by NeuroSciFi, not evidence', 'neutral') : ctx.badge('Given by you', 'info');
}

function item(x) {
  const { h } = ctx;
  const check = h('input', { type: 'checkbox', 'data-id': x.id, 'aria-label': `Use ${x.label} to make something`,
    checked: view.chosen.has(x.id), onchange: (ev) => { if (ev.target.checked) view.chosen.add(x.id); else view.chosen.delete(x.id); syncMake(); } });
  const from = x.from.length ? h('p', { class: 'sub' }, 'Made from ', x.from.map((s, i) => [i ? ', ' : '', s.label ?? 'something forgotten']), x.from_more ? ' and more' : '') : null;
  return h('li', { class: `art-item${view.shown === x.id ? ' current' : ''}`, 'data-id': x.id },
    h('label', { class: 'check art-use' }, check, h('span', { class: 'vh', text: 'Use' })),
    h('div', { class: 'art-main' },
      h('button', { type: 'button', class: 'linkish art-open', 'aria-current': view.shown === x.id ? 'true' : null, onclick: () => open(x.id), text: x.label }),
      h('p', { class: 'sub', text: `${cap(x.what)} · ${ctx.fmtBytes(x.size)} · ${ctx.ago(x.created_at)}${x.state === 'stored' ? '' : ` · ${x.state}`}` }),
      from,
      x.flagged ? h('p', { class: 'sub warnline', text: 'Its text looked like instructions to an AI, so it is never shown to one.' }) : null,
      x.held ? h('p', { class: 'sub warnline', text: `${cap(x.held)}.` }) : null),
    provenanceBadge(x));
}

const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);

// ---- one artifact ------------------------------------------------------------------------------------------------

async function open(id, { focus = true } = {}) {
  const { h, fill } = ctx;
  const ticket = ++view.seq;
  unmountPane();
  stopSound();
  view.shown = id;
  for (const li of document.querySelectorAll('#artifactList .art-item')) {
    const on = li.dataset.id === id;
    li.classList.toggle('current', on);
    const b = li.querySelector('.art-open');
    if (on) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
  }
  const pane = $('artifactPane');
  pane.setAttribute('aria-busy', 'true');
  let x;
  try {
    x = await ctx.api('GET', `/api/artifacts/${id}`);
  } catch (error) {
    if (ticket === view.seq) fill(pane, h('p', { class: 'msg error' }, ctx.rich(ctx.errorText(error))));
    return;
  }
  if (ticket !== view.seq) return;
  $('h-artifact').textContent = x.label;
  const body = h('div', { class: 'art-body' });
  fill(pane, h('div', { class: 'art-head' }, provenanceBadge(x), h('p', { class: 'art-what', text: `${cap(x.what)}.` })),
    body, details(x));
  await render(x, body, ticket);
  if (focus && ticket === view.seq) $('h-artifact').focus();
}

async function render(x, body, ticket) {
  const { h } = ctx;
  try {
    if (x.view === 'held') body.append(h('p', { class: 'empty' }, ctx.rich(`${cap(x.held)}. Nothing of it is read or shown until you decide: \`nsf artifact forget ${x.id}\` removes it; \`nsf artifact release ${x.id}\` ends the hold if it does not name what you forgot.`)));
    else if (x.view === 'svg') await showSvg(x, body);
    else if (x.view === 'table') body.append(tableOf(x.table, x.label));
    else if (x.view === 'document') await showDocument(x, body, ticket);
    else if (x.view === 'sound') body.append(soundBlock(x));
    else if (x.view === 'explorable') await showExplorable(x, body);
    else if (x.view === 'raster') await showRaster(x, body);
    else body.append(h('p', { class: 'empty' }, ctx.rich(`${x.unread ? cap(x.unread) + '. ' : ''}This view cannot show it; what could be measured is listed below. To look at it elsewhere: \`${x.export}\`.`)));
  } catch (error) {
    body.append(h('p', { class: 'msg error', text: `It could not be shown here: ${error.message ?? error}. To look at it elsewhere: ${x.export}` }));
  }
}

function droppedNote(dropped) {
  if (!dropped.length) return null;
  const words = dropped.map(([what, n]) => (n > 1 ? `${what} (${n} times)` : what)).join(', ');
  return ctx.h('p', { class: 'sub warnline', text: `Left out for safety: ${words}.` });
}

async function showSvg(x, body) {
  const text = await raw(`/api/artifacts/${x.id}/content`);
  const { node, dropped } = importSvg(text);
  if (!node) throw new Error('it is not a picture this view can read');
  body.append(ctx.h('figure', { class: 'art-figure' }, node));
  const left = droppedNote(dropped);
  if (left) body.append(left);
}

function tableOf(t, caption) {
  const { h } = ctx;
  const headers = t.columns.map((c) => ({ label: c.unit ? `${c.name} (${c.unit})` : c.name, num: c.kind === 'number' || c.kind === 'integer' }));
  const rows = t.rows.map((r) => r.map((v) => (v === null ? '—' : typeof v === 'number' ? fmtValue(v) : v)));
  const more = t.n_rows > t.rows.length || t.n_columns > t.columns.length;
  return h('div', { class: 'art-table' }, ctx.table(t.title ?? caption, headers, rows),
    more ? h('p', { class: 'sub', text: `Showing ${t.rows.length} of ${t.n_rows} rows${t.n_columns > t.columns.length ? ` and ${t.columns.length} of ${t.n_columns} columns` : ''}.` }) : null);
}

function fmtValue(v) {
  return Number.isInteger(v) ? String(v) : String(+v.toPrecision(8));
}

async function showDocument(x, body, ticket) {
  const { h } = ctx;
  const doc = x.document;
  const out = h('div', { class: 'art-doc' });
  let list = null;
  const figures = [];
  for (const b of doc.blocks) {
    if (b.kind !== 'list_item') list = null;
    if (b.kind === 'heading') out.append(h(`h${Math.min(6, 2 + Math.max(1, b.level))}`, { text: b.text }));
    else if (b.kind === 'list_item') { if (!list) { list = h('ul'); out.append(list); } list.append(h('li', { text: b.text })); }
    else if (b.kind === 'code') out.append(h('pre', {}, h('code', { text: b.text })));
    else if (b.kind === 'table' && b.table) out.append(tableOf(b.table, b.text || 'Table'));
    else if (b.kind === 'figure') {
      const fig = h('figure', { class: 'art-figure', 'data-figure': b.figure }, h('figcaption', { text: b.text || `Figure ${b.figure + 1}` }));
      figures.push(fig);
      out.append(fig);
    } else if (b.kind === 'page_break') out.append(h('hr'));
    else if (b.text) out.append(h('p', { class: b.kind === 'note' ? 'sub' : null, text: b.text }));
  }
  if (doc.n_blocks > doc.blocks.length) out.append(h('p', { class: 'sub', text: `Showing ${doc.blocks.length} of ${doc.n_blocks} parts.` }));
  body.append(out);
  // A document NeuroSciFi made embeds its figures as SVG pictures, in order: each is read from the stored file and
  // rebuilt through the same allow-list as a picture of its own. A given document's figures are named only.
  if (!figures.length || !x.made || x.media_type !== 'text/html') return;
  const html = new DOMParser().parseFromString(unstyled(await raw(`/api/artifacts/${x.id}/content`)).text, 'text/html');
  if (ticket !== view.seq) return;
  const svgs = [...html.querySelectorAll('svg')].filter((s) => !s.parentElement?.closest('svg'));
  figures.forEach((fig, i) => {
    if (!svgs[i]) return;
    const { node, dropped } = importSvg(new XMLSerializer().serializeToString(svgs[i]));
    if (node) fig.prepend(node);
    const left = droppedNote(dropped);
    if (left) fig.append(left);
  });
}

function soundBlock(x) {
  const { h } = ctx;
  const s = x.sound;
  const words = `${cap(x.what)}: ${s.channels === 1 ? 'one channel' : `${s.channels} channels`}, ${Math.round(s.rate_hz).toLocaleString('en')} samples per second.`;
  const guide = x.made_notes.find((n) => /pitch|listen/i.test(n));
  const play = h('button', { type: 'button', class: 'primary', id: 'artPlay', onclick: () => playSound(x) , text: 'Play' });
  const stop = h('button', { type: 'button', id: 'artStop', disabled: true, onclick: stopSound, text: 'Stop' });
  return h('div', { class: 'art-sound' }, h('p', { text: words }), guide ? h('p', { class: 'sub', text: guide }) : null,
    h('div', { class: 'buttons' }, play, stop), h('p', { id: 'artSoundStatus', class: 'msg', role: 'status' }));
}

async function playSound(x) {
  stopSound();
  const status = $('artSoundStatus');
  try {
    const bytes = await raw(`/api/artifacts/${x.id}/content`, 'bytes');
    const Ctx = window.AudioContext ?? window.webkitAudioContext;
    const audio = new Ctx();
    const buffer = await audio.decodeAudioData(bytes);
    const source = audio.createBufferSource();
    source.buffer = buffer;
    source.connect(audio.destination);
    source.onended = () => { if (view.audio?.source === source) stopSound(true); };
    view.audio = { audio, source };
    source.start();
    document.documentElement.dataset.artifactAudio = 'playing';
    $('artPlay').disabled = true;
    $('artStop').disabled = false;
    status.textContent = `Playing (${buffer.duration.toFixed(1)} seconds).`;
  } catch (error) {
    status.textContent = `It could not be played here: ${error.message ?? error}.`;
  }
}

function stopSound(ended = false) {
  const a = view.audio;
  view.audio = null;
  if (a) {
    try { a.source.onended = null; a.source.stop(); } catch { /* it had ended */ }
    a.audio.close().catch(() => {});
    document.documentElement.dataset.artifactAudio = 'stopped';
    const status = $('artSoundStatus');
    if (status) status.textContent = ended ? 'Finished.' : 'Stopped.';
  }
  if ($('artPlay')) $('artPlay').disabled = false;
  if ($('artStop')) $('artStop').disabled = true;
}

async function showExplorable(x, body) {
  if (!document.querySelector('link[data-explorable]')) {
    document.head.append(ctx.h('link', { rel: 'stylesheet', href: '/artifacts/explorable.css', 'data-explorable': '' }));
  }
  const runtime = await import('/artifacts/explorable.mjs');
  const slot = ctx.h('div', { class: 'art-explorable' });
  body.append(slot);
  view.mounted = runtime.mount(slot, x.spec);
}

async function showRaster(x, body) {
  const { h } = ctx;
  const minted = await ctx.api('POST', `/api/artifacts/${x.id}/image`, {});
  const size = x.raster?.width ? `${x.raster.width} × ${x.raster.height} pixels` : '';
  const img = h('img', { src: minted.url, alt: `${x.label}: ${x.what}`, width: x.raster?.width, height: x.raster?.height, class: 'art-raster' });
  body.append(h('figure', { class: 'art-figure' }, img, h('figcaption', { class: 'sub', text: `${size}${size ? '. ' : ''}Shown from an address that works for two minutes, in this console only.` })));
}

function details(x) {
  const { h } = ctx;
  const parts = [];
  if (x.check) {
    parts.push(h('p', { class: x.check.ok ? 'sub' : 'msg error', text: x.check.ok
      ? 'Checked after it was made, by an independent reader of the file: it passed.'
      : `The independent check found problems: ${x.check.failed.join(', ')}.` }));
    for (const w of x.check.warnings) parts.push(h('p', { class: 'sub warnline', text: w }));
  }
  if (x.from.length || x.made_into.length) {
    const link = (s) => h('button', { type: 'button', class: 'linkish', onclick: () => open(s.id), text: s.label ?? s.id });
    if (x.from.length) parts.push(h('p', { class: 'sub' }, 'Made from: ', x.from.map((s, i) => [i ? ', ' : '', link(s)])));
    if (x.made_into.length) parts.push(h('p', { class: 'sub' }, 'Made into: ', x.made_into.map((s, i) => [i ? ', ' : '', link(s)])));
  }
  if (x.relations.length) {
    parts.push(h('section', { class: 'art-rel', 'aria-labelledby': 'h-artrel' }, h('h3', { id: 'h-artrel', text: 'How it relates to other files' }),
      h('p', { class: 'sub', text: `Each line is ${x.claim_label}.` }),
      h('ul', {}, x.relations.map((r) => h('li', {}, `This ${r.words} `, h('button', { type: 'button', class: 'linkish', onclick: () => open(r.other.id), text: r.other.label ?? r.other.id }),
        ` (${r.relation === 'contradicts' || r.relation === 'consistent_with' ? 'measured' : 'estimated'} with confidence ${Math.round(r.confidence * 100)}%).`)))));
  }
  if (x.why) {
    parts.push(h('details', { class: 'tech art-why' }, h('summary', { text: 'Why it has this form' }),
      h('ul', { class: 'reasons' }, x.why.map((line) => h('li', { class: line.startsWith('  ') ? 'indent' : null, text: line.trim() })))));
  }
  const facts = (x.facts ?? []).map((f) => [f.name.replaceAll('_', ' '), `${typeof f.value === 'object' ? JSON.stringify(f.value) : f.value} (${f.how})`]);
  if (facts.length || x.notes.length) {
    parts.push(ctx.technical(facts.length ? ctx.kv(facts) : null,
      x.notes.length ? h('ul', { class: 'reasons' }, x.notes.map((n) => h('li', { text: `Not recovered: ${n}` }))) : null,
      h('p', { class: 'sub' }, ctx.rich(`Format ${x.format} (${x.media_type}), ${ctx.fmtBytes(x.size)}. To write it to a file: \`${x.export}\`.`))));
  }
  if (x.feedback) parts.push(feedbackBlock(x));
  const forget = h('button', { type: 'button', class: 'danger', id: 'artForget', onclick: (ev) => forgetIt(x, ev.currentTarget), text: 'Forget' });
  parts.push(h('div', { class: 'buttons art-actions' }, forget));
  return h('div', { class: 'art-details' }, parts);
}

function feedbackBlock(x) {
  const { h } = ctx;
  const status = h('p', { class: 'msg', role: 'status' });
  const send = (reaction, form, button) => ctx.act(button, status, async () => {
    const out = await ctx.api('POST', `/api/artifacts/${x.id}/feedback`, form ? { reaction, form } : { reaction });
    return out.recorded ? 'Thank you: recorded. It helps choose the form next time.' : `Not recorded: ${out.why}.`;
  });
  const select = h('select', { id: 'artWanted', 'aria-label': 'The form you wanted' },
    x.feedback.forms.map((f) => h('option', { value: f.form, text: f.label })));
  return h('section', { class: 'art-feedback', 'aria-labelledby': 'h-artfb' }, h('h3', { id: 'h-artfb', text: 'Was this the right form?' }),
    h('div', { class: 'buttons' },
      h('button', { type: 'button', onclick: (ev) => send('kept', null, ev.currentTarget), text: 'Keep' }),
      h('button', { type: 'button', onclick: (ev) => send('dismissed', null, ev.currentTarget), text: 'Not useful' })),
    h('div', { class: 'inline' }, h('label', { for: 'artWanted', text: 'I wanted' }), select,
      h('button', { type: 'button', onclick: (ev) => send('replaced', select.value, ev.currentTarget), text: 'Record' })),
    status);
}

async function forgetIt(x, button) {
  const ok = await ctx.confirmAction(`Forget ${x.label}?`,
    `Its bytes and everything NeuroSciFi made from it are deleted; checkpoint archives that hold it become stale and are rewritten without it. This cannot be undone.`, 'Forget');
  if (!ok) return;
  await ctx.act(button, $('artifactStatus'), async () => {
    const out = await ctx.api('POST', `/api/artifacts/${x.id}/forget`, {});
    unmountPane();
    stopSound();
    view.shown = null;
    view.chosen.delete(x.id);
    $('h-artifact').textContent = 'Shown here';
    ctx.fill($('artifactPane'), ctx.empty('Choose something in the list to show it here.'));
    await loadList();
    return `Forgotten: ${out.removed === 1 ? 'one file' : `${out.removed} files, with what was made from them`}.${out.next_steps.length ? ' ' + out.next_steps.join(' ') : ''}`;
  });
}

// ---- make ----------------------------------------------------------------------------------------------------------

function syncMake() {
  const n = view.chosen.size;
  $('makeChosen').textContent = n ? `From ${n === 1 ? 'one chosen file' : `${n} chosen files`}.` : 'Tick one or more files in the list below to make something from them.';
  $('makeBtn').disabled = !n || view.job !== null || ctx.offline();
}

async function makeIt(event) {
  event.preventDefault();
  const request = $('makeRequest').value.trim();
  if (!request) { ctx.say($('makeStatus'), 'Say what you want to know or see.', 'error'); $('makeRequest').focus(); return; }
  await ctx.act($('makeBtn'), $('makeStatus'), async () => {
    const started = await ctx.api('POST', '/api/artifacts/make', { sources: [...view.chosen], request });
    view.job = started.job;
    try {
      for (;;) {
        await new Promise((r) => setTimeout(r, 400));
        const j = await ctx.api('GET', `/api/artifacts/jobs/${started.job}`);
        if (j.state === 'running') continue;
        if (j.state === 'failed') throw new Error(j.error);
        return await finished(j.result);
      }
    } finally {
      view.job = null;
      syncMake();
    }
  });
}

async function finished(r) {
  const { h, fill } = ctx;
  if (r.status === 'made') {
    view.page = 0;
    await loadList();
    await open(r.artifact, { focus: false });
    return 'Made, checked and kept. It is shown here.';
  }
  unmountPane();
  stopSound();
  view.shown = null;
  $('h-artifact').textContent = r.status === 'words' ? 'In words' : 'Nothing was made';
  const because = r.why.find((l) => /No picture or file was made|Could not|could not|none of the sources/.test(l));
  fill($('artifactPane'),
    r.words.length ? h('ul', { class: 'art-words' }, r.words.map((w) => h('li', { text: w }))) : null,
    h('p', { class: r.status === 'words' ? 'sub' : 'msg error', text: r.status === 'words'
      ? `No picture was made because words suffice here.${because ? ' ' + because : ''}` : (because ?? 'Nothing could be made from these files.') }),
    h('details', { class: 'tech art-why' }, h('summary', { text: 'Why' }), h('ul', { class: 'reasons' }, r.why.map((l) => h('li', { text: l.trim() })))));
  return r.status === 'words' ? 'Answered in words; nothing was made or stored.' : 'Nothing was made.';
}

// ---- upload (shown only when the owner turned it on) ------------------------------------------------------------

async function uploadFiles(files) {
  const status = $('uploadStatus');
  for (const file of files) {
    if (!view.list?.upload) return;
    if (file.size > view.list.max_bytes) { ctx.say(status, `${file.name} is larger than the ${ctx.fmtBytes(view.list.max_bytes)} a file may have (\`nsf config --artifact-max-mib\`).`, 'error'); continue; }
    await ctx.act($('uploadPick'), status, async () => {
      const start = await ctx.api('POST', '/api/artifacts/uploads', { name: file.name, size: file.size });
      for (let offset = 0; offset < file.size; offset += start.chunk_bytes) {
        const chunk = await file.slice(offset, offset + start.chunk_bytes).arrayBuffer();
        const response = await fetch(`/api/artifacts/uploads/${start.upload}/chunk?offset=${offset}`, { method: 'POST', cache: 'no-store', credentials: 'omit',
          headers: { Authorization: `Bearer ${ctx.token()}`, 'Content-Type': 'application/octet-stream' }, body: chunk });
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error ?? `HTTP ${response.status}`);
        status.textContent = `Sending ${file.name}: ${Math.round(100 * Math.min(file.size, offset + chunk.byteLength) / file.size)}%`;
      }
      const out = await ctx.api('POST', `/api/artifacts/uploads/${start.upload}/finish`, {});
      if (!out.kept) throw new Error(`${file.name} was not kept: ${out.why}`);
      view.page = 0;
      await loadList();
      return `Kept ${file.name}: ${out.artifact.what}.`;
    });
  }
}

// ---- wiring --------------------------------------------------------------------------------------------------------

function wire() {
  wired = true;
  $('makeForm').addEventListener('submit', makeIt);
  $('artPrev').addEventListener('click', () => { if (view.page > 0) { view.page -= 1; loadList(); } });
  $('artNext').addEventListener('click', () => { view.page += 1; loadList(); });
  $('refreshArtifacts').addEventListener('click', () => loadList());
  const pick = $('uploadFile');
  $('uploadPick').addEventListener('click', () => pick.click());
  pick.addEventListener('change', () => { uploadFiles([...pick.files]); pick.value = ''; });
  const zone = $('uploadBox');
  zone.addEventListener('dragover', (ev) => { ev.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (ev) => { ev.preventDefault(); zone.classList.remove('over'); uploadFiles([...(ev.dataTransfer?.files ?? [])]); });
}
