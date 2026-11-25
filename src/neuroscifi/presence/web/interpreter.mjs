// Semantic interpreter + arbitration (docs/presence/COGNITIVE_STATE_MAPPING.md).
// Input: the backend state (presence/aggregator.py), local real signals (voice events with measured RMS level,
// typing in this page, connection health). Output: one intent — what to show and the words that describe it.
// Nothing here invents activity: unknown -> 'present' with "connecting"; disconnected -> no activity claimed.
import { RANK } from './contracts.mjs';

export const VOICE_FRESH_MS = 8000;      // a voice event older than this (with no newer one) no longer drives motion
export const INTERRUPT_MS = 1500;
export const LOCAL_ACTIVE_MS = 60000;    // user interaction in this page keeps "present" instead of "resting"

// Composition for each activity (Tier A names; Tier B concepts are free text handled by the planner).
export const ACTIVITY_CONCEPT = Object.freeze({
  idle: 'present', listening: 'listening', interpreting: 'thinking', reasoning: 'thinking', planning: 'exploration',
  searching: 'researching', reading: 'reading', analyzing: 'computing', verifying: 'evaluating evidence',
  comparing: 'comparing', learning: 'growth', consolidating: 'construction', predicting: 'ideation',
  simulating: 'experiment', executing: 'execution', generating: 'responding', responding: 'responding',
  waiting: 'waiting', recovering: 'recovering', resting: 'resting',
});

export const ACTIVITY_TEXT = Object.freeze({
  idle: 'Present and ready', listening: 'Listening', interpreting: 'Interpreting your request', reasoning: 'Reasoning',
  planning: 'Planning', searching: 'Searching for sources', reading: 'Reading sources', analyzing: 'Analysing',
  verifying: 'Verifying evidence', comparing: 'Comparing sources', learning: 'Learning', consolidating: 'Consolidating knowledge',
  predicting: 'Predicting', simulating: 'Simulating', executing: 'Executing a task', generating: 'Writing a response',
  responding: 'Responding', waiting: 'Waiting', recovering: 'Recovering', resting: 'Resting',
});
const CONDITION_TEXT = { busy: 'busy', resource_constrained: 'writes paused to protect storage', network_degraded: 'some search providers are failing',
  disconnected: 'connection lost', recovering: 'recovering an interrupted task', awaiting_permission: 'questions are waiting for you', error: 'a task failed' };
const ENGINE_TEXT = { asr: 'speech recognition', tts: 'speech output' };
// network_degraded has two real causes (aggregator sources): failing search providers and unreachable speech engines.
function conditionNotes(condition, sources) {
  const notes = [];
  for (const c of condition) {
    if (c !== 'network_degraded') { if (CONDITION_TEXT[c]) notes.push(CONDITION_TEXT[c]); continue; }
    const speech = sources?.voice === 'engines_unavailable';
    if (speech) {
      const down = String(sources.voice_engines_down ?? '').split(',').map((k) => ENGINE_TEXT[k]).filter(Boolean);
      notes.push(`${down.length ? down.join(' and ') : 'speech services'} unavailable; type instead`);
    }
    if (!speech || sources?.search === 'degraded') notes.push(CONDITION_TEXT.network_degraded);
  }
  return notes;
}
const SECONDARY_TEXT = { web_research: 'web research', background_investigation: 'background research', evidence_verification: 'evidence checks',
  knowledge_consolidation: 'knowledge consolidation', memory_retrieval: 'memory retrieval', audio_streaming: 'audio', tool_execution: 'tool execution', device_sync: 'device sync' };

const cand = (activity, priority, extra = {}) => ({ activity, priority, rank: RANK[priority], since: null, concept: null, secondary: [], detail: null, ...extra });

export function voiceCandidates(voice, now) {
  if (!voice || now - voice.at > VOICE_FRESH_MS && !voice.mic_capturing && !voice.speaking) return [];
  if (voice.mode === 'text_only') return [];
  const out = [];
  if (voice.speaking && !voice.output_muted && voice.mode !== 'voice_in_text_out') {
    out.push(cand('responding', 'communication', { subsystem: 'voice', since: voice.speakingSince ?? voice.at, detail: 'Speaking the answer', secondary: ['audio_streaming'], audio: 'output' }));
  } else if (voice.mic_capturing) {
    out.push(cand('listening', 'communication', { subsystem: 'voice', since: voice.micSince ?? voice.at, detail: 'Listening (microphone on)', secondary: ['audio_streaming'], audio: 'mic' }));
  }
  return out;
}

// Arbitration: highest rank first, then the most recent. Voice from this page (with a measured level) replaces
// the backend's voice candidates, which carry no level.
export function arbitrate(server, local, now) {
  let list = (server?.activities ?? []).map((c) => ({ ...c }));
  const voice = voiceCandidates(local.voice, now);
  if (local.voice) list = list.filter((c) => c.subsystem !== 'voice');
  list = list.concat(voice);
  list.sort((a, b) => (b.rank - a.rank) || ((b.since ?? 0) - (a.since ?? 0)));
  return list;
}

export function interpret(server, local, now = Date.now()) {
  const conn = local.connection ?? 'connecting';
  if (conn === 'disconnected' || conn === 'locked') {
    return finish({ activity: 'waiting', concept: 'disconnected', priority: 'idle', condition: conn === 'locked' ? [] : ['disconnected'],
      interaction: [], secondary: [], intensity: 0, level: null, others: [],
      detail: conn === 'locked' ? 'Not signed in or not paired; activity unknown' : 'Connection lost; activity unknown' });
  }
  if (!server) {
    return finish({ activity: 'idle', concept: 'present', priority: 'idle', condition: [], interaction: [], secondary: [],
      detail: 'Connecting…', intensity: 0, level: null, others: [], unknown: true });
  }
  const list = arbitrate(server, local, now);
  let primary = list[0] ?? { ...server.primary };
  const interaction = new Set(server.interaction ?? []);
  const voice = local.voice;
  if (voice) {
    // the server's user_interruption stays: it comes from the hub's interrupted_at, set only by a real barge-in/stop
    for (const k of ['user_speaking', 'responding', 'voice_muted', 'text_only']) interaction.delete(k);
    if (voice.mode === 'text_only') interaction.add('text_only');
    else {
      if (voice.output_muted) interaction.add('voice_muted');
      if (voice.speaking && !voice.output_muted) interaction.add('responding');
      if (voice.mic_capturing && !voice.speaking && (voice.level ?? 0) > 0.06) interaction.add('user_speaking');
      if (voice.interruptedAt && now - voice.interruptedAt < INTERRUPT_MS) interaction.add('user_interruption');
    }
  }
  if (local.typingAt && now - local.typingAt < 2500) interaction.add('user_typing');
  if (primary.activity === 'resting' && local.interactedAt && now - local.interactedAt < LOCAL_ACTIVE_MS) {
    primary = { ...primary, activity: 'idle', detail: 'Ready; nothing is running' };
  }
  let level = null;
  if (primary.audio && voice && Number.isFinite(voice.level)) level = Math.max(0, Math.min(1, voice.level));
  const others = list.filter((c) => c !== primary && c.activity !== primary.activity).slice(0, 2);
  return finish({ activity: primary.activity, concept: primary.concept, priority: primary.priority ?? 'idle', taskId: primary.task_id ?? null,
    label: primary.label ?? null, step: primary.step ?? null, detail: primary.detail ?? null, subsystem: primary.subsystem ?? null,
    secondary: primary.secondary ?? [], condition: (server.condition ?? []).filter((c) => c !== 'healthy'), sources: server.sources ?? {},
    interaction: [...interaction].sort(),
    intensity: server.intensity ?? 0, level, others, audio: primary.audio ?? null, since: primary.since ?? null });
}

function finish(intent) {
  // Authorised background work nobody is waiting on is shown subtly: a calm exploring entity with an orbiting dot,
  // at half the motion energy, instead of the full foreground composition.
  intent.subtle = intent.priority === 'background' && !intent.concept && !['waiting', 'recovering'].includes(intent.activity);
  intent.concept = intent.concept ?? (intent.subtle ? 'exploring' : ACTIVITY_CONCEPT[intent.activity]) ?? 'present';
  const words = describe(intent);
  intent.text = words.text;
  intent.short = words.short;
  intent.assertive = words.assertive;
  return intent;
}

// Accessible description: what is happening, from which real source, never a percentage that is not known.
export function describe(intent) {
  const base = intent.concept === 'disconnected' ? 'Not connected' : intent.concept === 'asking' ? 'Waiting for your answer'
    : intent.concept === 'error' ? 'A task failed' : intent.concept === 'achievement' ? 'Finished'
    : ['idle', 'resting'].includes(intent.activity) && intent.sources?.background === 'quiet' ? 'Quiet'
    : ACTIVITY_TEXT[intent.activity] ?? 'Present';
  const parts = [base];
  if (intent.step) parts[0] += ` (step ${intent.step.index} of ${intent.step.of})`;
  if (intent.label) parts.push(`“${intent.label}”`);
  if (intent.detail && intent.detail !== base) parts.push(intent.detail);
  const also = [...new Set([...intent.others.map((o) => ACTIVITY_TEXT[o.activity]?.toLowerCase()),
    ...intent.secondary.filter((s) => s === 'background_investigation').map((s) => SECONDARY_TEXT[s])])].filter(Boolean);
  if (also.length) parts.push(`Also: ${also.join(', ')}`);
  // The owner's quiet switch (background.py, ADR-050): said once, in plain words, whatever is shown.
  if (intent.sources?.background === 'quiet' && !String(intent.detail ?? '').startsWith('Quiet')) parts.push('Quiet: nothing runs unless you ask');
  const conds = conditionNotes(intent.condition, intent.sources);
  if (conds.length) parts.push(`Note: ${conds.join('; ')}`);
  if (intent.interaction.includes('voice_muted')) parts.push('Voice output muted');
  if (intent.interaction.includes('text_only')) parts.push('Text only');
  const assertive = intent.priority === 'user_critical' || intent.condition.includes('error');
  return { text: parts.join('. ') + '.', short: base, assertive };
}
