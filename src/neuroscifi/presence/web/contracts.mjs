// Presence Cortex typed contracts (docs/presence/PRESENCE_CORTEX_ARCHITECTURE.md §Contracts).
// Plain data and frozen vocabularies shared by every module; the backend (presence/aggregator.py) uses the same words.

export const STATE_SCHEMA = 'nsf.presence.state/1';
export const SCENE_SCHEMA = 'nsf.presence.scene/1';

export const ACTIVITIES = Object.freeze(['idle', 'listening', 'interpreting', 'reasoning', 'planning', 'searching',
  'reading', 'analyzing', 'verifying', 'comparing', 'learning', 'consolidating', 'predicting', 'simulating',
  'executing', 'generating', 'responding', 'waiting', 'recovering', 'resting']);
export const SECONDARY = Object.freeze(['web_research', 'tool_execution', 'audio_streaming', 'memory_retrieval',
  'evidence_verification', 'knowledge_consolidation', 'background_investigation', 'device_sync']);
export const CONDITIONS = Object.freeze(['healthy', 'busy', 'resource_constrained', 'network_degraded',
  'disconnected', 'recovering', 'awaiting_permission', 'error']);
export const INTERACTIONS = Object.freeze(['user_speaking', 'user_typing', 'responding', 'user_interruption',
  'tool_awaiting_confirmation', 'autonomous_research', 'voice_muted', 'text_only', 'background_task_completed']);

// Arbitration ranks (spec): user-critical > communication > foreground cognition > tool > background > idle.
export const RANK = Object.freeze({ user_critical: 5, communication: 4, foreground: 3, tool: 2, background: 1, idle: 0 });

// Scene slots: a fixed pool of SVG paths, so the DOM never grows. Each slot has a fixed resampled point count, which
// is what lets any shape morph into any other (shared point-count resampling).
export const SLOTS = Object.freeze(['ground', 'body', 'eyeL', 'eyeR', 'm0', 'm1', 'm2', 'm3', 'm4', 'm5',
  'a0', 'a1', 'a2', 'a3']);
export const POINTS = Object.freeze({ body: 72, ground: 24, default: 28 });
export const pointsFor = (slot) => POINTS[slot] ?? POINTS.default;

export const TONES = Object.freeze(['ink', 'secondary', 'purple', 'blue', 'mint', 'yellow', 'pink']);
export const ACCENT_TONES = Object.freeze(['purple', 'blue', 'mint', 'yellow', 'pink']);

// Rendered size classes and what each keeps (detail simplification; stroke proportion stays constant).
export const SIZES = Object.freeze({
  indicator: { max: 28, slots: ['body', 'eyeL', 'eyeR'] },
  compact: { max: 64, slots: ['body', 'eyeL', 'eyeR', 'm0', 'm1', 'm2', 'a0', 'a1', 'ground', 'm5', 'a3'] },  // m5, a3: overlays
  normal: { max: 140, slots: null },
  expanded: { max: Infinity, slots: null },
});
export function sizeClass(px) {
  for (const [name, spec] of Object.entries(SIZES)) if (px <= spec.max) return name;
  return 'expanded';
}

// Stroke widths in viewBox units (100 x 100): constant proportion at every size.
export const STROKE = Object.freeze({ bold: 4.4, fine: 2.8 });

export const PREFS_KEY = 'nsf.presence.prefs';
export const DEFAULT_PREFS = Object.freeze({ motion: 'system', intensity: 'normal', modelDoodles: false });
export const MOTION_PREFS = Object.freeze(['system', 'reduced', 'full']);
export const INTENSITY_PREFS = Object.freeze({ low: 0.5, normal: 1, high: 1.4 });

// Motion budget enforced by the validator and the engine.
export const BUDGET = Object.freeze({ maxAnimatedSlots: 8, maxAmplitude: 14, maxRotationSpeed: 1.2,
  maxVelocity: 260, maxAcceleration: 5200 });
