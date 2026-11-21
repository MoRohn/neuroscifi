// Visual metaphor planner (docs/presence/DOODLE_GRAMMAR.md): intent -> one validated scene.
// Tier A canonical (or system) composition when one exists; else Tier B (semantic template or seeded constellation);
// Tier C (a model-proposed spec) only when the caller passes one. Every Tier B/C scene is validated; any failure falls
// back to the nearest canonical composition. Overlays: one background activity as an orbiting dot, one non-primary
// condition as a small shape. Detail is simplified for small sizes (SIZES[].slots).
import { SLOTS, SIZES, pointsFor } from './contracts.mjs';
import { composition, CANONICAL, SYSTEM, OVERLAY } from './canonical.mjs';
import { composeSpec, specToComposition } from './composer.mjs';
import { validateScene, validateSpec } from './validator.mjs';
import { resample, centroid } from './geometry.mjs';

// Nearest canonical for any concept (mirrors presence/scene_spec.py NEAREST).
export const NEAREST = [[/read|document|paper|book|article/, 'reading'], [/comput|code|analy|data|calculat/, 'computing'],
  [/search|research|investigat|look|scan|find/, 'researching'], [/launch|start|discover/, 'discovery'],
  [/idea|predict|insight|hypothes/, 'ideation'], [/experiment|test|simulat|trial/, 'experiment'],
  [/map|explor|plan|route|navigat/, 'exploration'], [/build|construct|assembl|consolidat|organi/, 'construction'],
  [/execut|run|process|tool|gear|operat/, 'execution'], [/listen|audio|sound|voice|hear/, 'listening'],
  [/learn|grow|feedback|improv/, 'growth'], [/finish|done|achiev|complet|goal/, 'achievement'],
  [/think|reason|consider|evaluat|compar|verif/, 'thinking'], [/respond|answer|reply|speak/, 'responding'],
  [/rest|sleep|idle|wait/, 'resting']];
export function nearest(concept) {
  const text = String(concept ?? '').toLowerCase();
  for (const [re, name] of NEAREST) if (re.test(text)) return name;
  return 'present';
}

// Resample a composition into the fixed slot pool; hidden slots stay in the pool (collapsed, opacity 0).
export function finalize(comp, sizeName = 'normal') {
  const size = SIZES[sizeName];
  const keep = size?.objectSlots && !comp.slots.eyeL ? size.objectSlots : size?.slots;
  const slots = {};
  for (const name of SLOTS) {
    const s = comp.slots[name];
    if (!s) continue;
    const pts = resample(s.shape, pointsFor(name));
    const [cx, cy] = centroid(pts);
    slots[name] = { pts, closed: s.shape.closed, tone: s.tone, width: s.width, fill: s.fill, dash: s.dash,
      anims: s.anims, cx, cy, visible: !keep || keep.includes(name) };
  }
  return { id: comp.id, tier: comp.tier, breathe: comp.breathe, slots, template: comp.template ?? null };
}

function withOverlays(comp, intent) {
  const slots = { ...comp.slots };
  const conds = intent.condition ?? [];
  let overlay = null;
  if (intent.concept !== 'asking' && conds.includes('awaiting_permission')) overlay = OVERLAY.question();
  else if (intent.concept !== 'error' && conds.includes('error')) overlay = OVERLAY.alert();
  else if (conds.includes('network_degraded') || conds.includes('resource_constrained')) overlay = OVERLAY.degraded();
  else if (intent.subtle || ((intent.others ?? []).some((o) => o.priority === 'background') && intent.priority !== 'background')) {
    overlay = OVERLAY.orbit();
  }
  if (overlay) for (const [k, v] of Object.entries(overlay)) if (!slots[k]) slots[k] = v;
  return { ...comp, slots };
}

export function plan(intent, { size = 'normal', spec = null } = {}) {
  const concept = intent.concept ?? 'present';
  let comp = null, fallbackReason = null;
  if (spec) {
    const errors = validateSpec(spec);
    if (errors.length) fallbackReason = `model spec rejected: ${errors[0]}`;
    else comp = specToComposition(spec, { tier: 'C' });
  }
  if (!comp && (CANONICAL[concept] || SYSTEM[concept])) comp = composition(concept);
  if (!comp) {
    const { spec: sp, template } = composeSpec(concept);
    const errors = validateSpec(sp);
    if (errors.length) fallbackReason = `procedural spec rejected: ${errors[0]}`;
    else comp = { ...specToComposition(sp, { tier: 'B' }), id: concept, template };
  }
  if (comp) {
    const scene = finalize(withOverlays(comp, intent), size);
    const errors = validateScene(scene);
    if (!errors.length) return { ...scene, concept, fallbackReason };
    fallbackReason = `scene rejected: ${errors[0]}`;
  }
  const near = nearest(concept);
  const scene = finalize(withOverlays(composition(near), intent), size);
  return { ...scene, tier: 'fallback', concept, fallbackReason, fallbackTo: near };
}
