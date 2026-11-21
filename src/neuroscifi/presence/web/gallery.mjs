// Presence gallery: renders every canonical composition and several procedural concepts in light and dark with the
// production planner, engine and renderer; a live harness for transition and performance tests. Behind the console
// session token: nothing renders until GET /api/presence accepts the token from the URL fragment.
import { plan } from './planner.mjs';
import { MotionEngine } from './motion.mjs';
import { Renderer, createSvg } from './renderer.mjs';
import { CANONICAL_NAMES, SYSTEM_NAMES } from './canonical.mjs';
import { TEMPLATES } from './composer.mjs';
import { ACTIVITIES } from './contracts.mjs';
import { Presence, expose } from './presence.mjs';
import { initTheme, setThemePref, themePref } from './theme.mjs';
import { validateSpec } from './validator.mjs';

const $ = (id) => document.getElementById(id);
const PROCEDURAL = [...TEMPLATES.map((t) => t.name), 'quantum gardening', 'tidal folklore'];
const INTENT = { condition: [], others: [], secondary: [], priority: 'foreground', interaction: [] };

export function staticFigure(concept, { size = 'normal', spec = null, caption = concept } = {}) {
  const svg = createSvg();
  const renderer = new Renderer(svg);
  const engine = new MotionEngine();
  engine.reduced = true;
  const scene = plan({ ...INTENT, concept }, { size, spec });
  engine.setScene(scene, { instant: true });
  renderer.draw(engine);
  const fig = document.createElement('figure');
  fig.className = 'gcell';
  fig.dataset.concept = concept;
  fig.dataset.tier = scene.tier;
  const cap = document.createElement('figcaption');
  cap.textContent = `${caption} · ${scene.tier}${scene.fallbackTo ? ` → ${scene.fallbackTo}` : ''}`;
  fig.append(svg, cap);
  return fig;
}

function fillPanels(prefix, concepts, opts = {}) {
  for (const theme of ['Light', 'Dark']) $(prefix + theme).replaceChildren(...concepts.map((c) => staticFigure(c, opts)));
}

let version = 0;
export function syntheticState(activity, { concept = null, priority = 'foreground', intensity = 0.6, condition = ['healthy'], others = [] } = {}) {
  const cand = (a, p, c = null) => ({ activity: a, concept: c, subsystem: 'gallery', priority: p, rank: { user_critical: 5, communication: 4, foreground: 3, tool: 2, background: 1, idle: 0 }[p],
    since: Date.now() / 1000, task_id: null, label: null, detail: 'Synthetic gallery state', step: null, progress: null, secondary: [], confidence: 1 });
  const primary = cand(activity, activity === 'idle' || activity === 'resting' ? 'idle' : priority, concept);
  const acts = primary.priority === 'idle' ? [] : [primary, ...others.map(([a, p]) => cand(a, p))];
  return { schema: 'nsf.presence.state/1', scope: 'owner', primary, activities: acts, condition, interaction: [], intensity, voice: null,
    sources: {}, version: `g${++version}`, ts: Date.now() / 1000, signals: {} };
}

function harness() {
  const presence = new Presence($('live'), { channel: null });
  presence.local.connection = 'ok';
  expose(presence);
  const set = (activity, opts) => { presence.server = syntheticState(activity, opts); presence.request(); };
  const list = [...ACTIVITIES.map((a) => [a, {}]), ['verifying', { concept: 'evaluating evidence' }], ['waiting', { concept: 'asking', priority: 'user_critical' }],
    ['searching', { priority: 'background' }], ['reasoning', { others: [['searching', 'background']] }]];
  $('liveBtns').replaceChildren(...list.map(([a, o]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = o.concept ?? (o.priority === 'background' ? `${a} (background)` : o.others ? `${a} + background` : a);
    b.addEventListener('click', () => set(a, o));
    return b;
  }));
  const stats = () => { const s = presence.stats(); $('liveStats').textContent = JSON.stringify(s); return s; };
  setInterval(stats, 1000);
  window.__presenceGallery = {
    set, stats, presence,
    // n synthetic transitions, one every gapMs, cycling through every activity (J, perf).
    async run(n, gapMs = 30) {
      for (let i = 0; i < n; i++) {
        set(ACTIVITIES[i % ACTIVITIES.length], { intensity: (i % 10) / 10 });
        await new Promise((r) => setTimeout(r, gapMs));
      }
      await new Promise((r) => setTimeout(r, 400));
      return stats();
    },
  };
  set('idle');
}

let started = false;
async function start() {
  const m = /^#token=([A-Za-z0-9_-]{16,128})$/.exec(location.hash);
  if (!m || started) return;
  started = true;
  const token = m[1];
  history.replaceState(null, '', location.pathname);
  const ok = await fetch('/api/presence?wait=0', { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' }).then((r) => r.ok).catch(() => false);
  if (!ok) { started = false; $('glocked').textContent = 'The session token was rejected. Open the gallery with the token printed by `nsf console`.'; return; }
  $('glocked').hidden = true;
  $('gcontent').hidden = false;
  fillPanels('a', [...CANONICAL_NAMES, ...SYSTEM_NAMES]);
  fillPanels('b', PROCEDURAL);
  for (const theme of ['Light', 'Dark']) {
    $('s' + theme).replaceChildren(...['indicator', 'compact', 'normal', 'expanded'].map((size) => {
      const f = staticFigure('researching', { size, caption: size });
      f.classList.add(`s-${size}`);
      return f;
    }));
  }
  $('cForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const concept = $('cConcept').value.trim().toLowerCase();
    $('cResult').textContent = 'Asking the local model…';
    const reply = await fetch('/api/presence/scene', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ concept }), cache: 'no-store' }).then((r) => r.json()).catch(() => null);
    const spec = reply?.status === 'ok' && validateSpec(reply.spec).length === 0 ? reply.spec : null;
    $('cResult').textContent = spec ? `Model spec accepted (validated on the server and here): ${JSON.stringify(spec)}`
      : `No model spec (${reply?.status ?? 'error'}${reply?.reason ? `: ${reply.reason}` : ''}); showing the procedural composition, or the nearest canonical "${reply?.fallback ?? 'present'}" if that fails.`;
    for (const theme of ['Light', 'Dark']) $('c' + theme).replaceChildren(staticFigure(concept || 'present', { spec }));
  });
  harness();
}

initTheme();
$('gTheme').value = themePref();
$('gTheme').addEventListener('change', () => setThemePref($('gTheme').value));
addEventListener('hashchange', start);
start();
