# Continual learning: the kinds, kept apart

Status: v0.7.0, working tree. Decision: ADR-038 (draft `.omc/autopilot/adr/ADR-038-knowledge-evolution.md`). Code: `webcortex/evolution.py` (`update_research_policy`, `revert_research_policy`, `research_policy`, `model_adaptation_guard`). Tests: `tests/webcortex/test_evolution.py`. Related: [KNOWLEDGE_EVOLUTION.md](KNOWLEDGE_EVOLUTION.md).

"The system learns" can mean three different things. They have different risks, so each has its own storage, its own controls and its own way back:

| Kind | What changes | Automatic? | Bound | Log | Undo |
|---|---|---|---|---|---|
| 1. Knowledge evolution | Memory and graph: knowledge versions, hypotheses, predictions, edges | Yes, but candidates only. The owner accepts every knowledge item | ADR-035 bar, question budget, hypotheses per task | `web_events`, `web_feedback`, versions | Owner reject or archive, `forget_*`, versions never destroyed |
| 2. Procedural learning | Research-policy statistics: which providers yield verified claims | Only when asked (`nsf knowledge learning --update`) | Step ≤ 0.1, weight in [0.5, 1.5], ≥ 5 sources | `web_metrics` (kind `procedural`) and a `decision` event | `--revert` writes the previous weights back |
| 2b. Forecasts as read (ADR-046) | How a forecast is read when the admitted autonomous investigations are ranked (never which are admitted): nothing stored | Yes, once 20 forecasts of a kind have settled | Identity below 20; held to the stated forecast by a prior; never reverses an order; within [0.01, 0.99]; one reader | Each queued investigation's `decision` event holds the stated forecast and the reading; `nsf knowledge calibration` shows the mapping | `nsf web config --set calibrated_forecasts=false`; forgetting a prediction removes its part at once |
| 3. Model adaptation | Model weights | **Never** automatic. Not implemented in this package | n/a | `web_metrics` (kind `adaptation`) for every guard decision | A rollback plan is required before anything can be permitted |

## 1. Knowledge evolution

Knowledge evolution changes what is known and nothing else. It is described in KNOWLEDGE_EVOLUTION.md, and in brief:

- Consolidation writes verified conclusions only as `provisional` candidates with an owner question.
- Research opportunities become hypotheses.
- Predictions are made and settled.
- Supersession adds versions and never overwrites.
- Contradictions are recorded and never resolved silently.

It never changes how the system searches, scores or reasons. The evidence weights, the category policy and the thresholds are code constants and owner configuration, and no learned value modifies them. One learned reading exists beside it since ADR-046 (kind 2b): which autonomous investigation is chosen next depends on forecasts read through how forecasts of the same kind fared. It moves no stored forecast, confidence, verification state or threshold.

## 2. Procedural learning (research-policy statistics)

`evolution.update_research_policy(db, now)` recomputes, from the current records:

- **per provider** (`web_sources.provider`): sources read, and sources behind at least one verified conclusion (a supporting claim of a `verified_conclusion` row);
- **per mode** (`web_tasks.mode`): finished tasks, and tasks that produced a verified conclusion.

It then moves each provider's advisory weight toward `0.5 + verified_sources / sources`, under three limits:

- **Bounded.** A weight moves at most `POLICY_STEP` = 0.1 per update and stays inside `POLICY_BOUNDS` = [0.5, 1.5]. A provider with fewer than `POLICY_MIN_SOURCES` = 5 sources keeps its weight.
- **Logged.** Each update is a new `web_metrics` row (kind `procedural`) holding the new weights, the previous weights, the changes and the statistics. A `decision` event names the snapshot and the changed providers.
- **Reversible.** `revert_research_policy` writes a new snapshot with the previous weights, marked `reverted`. History is never deleted.

The weights are **advisory** in this release. Nothing reads them to reorder or skip a provider automatically, and connecting them to provider selection is a separate owner decision: they are recorded for the owner's inspection (`nsf knowledge learning`) and for no other use. The review that closed the forecast loop (ADR-046) looked at this one again and left it as it is, because no document defines what a reader of the weights would do. Because the statistics are recomputed from records at each update, a forgotten task or source drops out of the next update. Snapshots contain provider names and numbers only.

Test: `test_procedural_policy_is_bounded_logged_and_reversible`. Six sources of one provider with no verified conclusion move its weight from 1.0 to 0.9, then to 0.8, and the revert restores 0.9. All three snapshots and events are logged.

## 3. What procedural learning is not

The following are procedural state too, but they are not learned:

- Provider health and circuit-breaker state (`web_strategies`, `services.persist`) are operational: they record failures and cooldowns.
- Mode outcome counts (`state.record_mode_outcome`) are statistics that are not fed back into a policy.

## 4. Model adaptation: refused unless all three conditions hold

This package contains **no** code that changes model weights. The inference gateway (ADR-036) only calls a served model, and Web Cortex research never trains. To keep it that way, any weight-changing action must pass `evolution.model_adaptation_guard(db, request, now)` (`nsf knowledge adapt ACTION --request-json '{...}'`). The guard refuses, with a `Conflict`, unless the request carries all of the following:

1. **Owner authorisation:** `owner_authorization = {by: "owner", confirmed: true, at: <time>}`.
2. **An isolated evaluation plan:** `isolated_evaluation = {isolated: true, heldout, metric, pass_bar}`. The held-out data is never used for training.
3. **A rollback plan:** `rollback_plan = {checkpoint, procedure}`, a restorable copy of the current weights and how to restore it.

A request from an autonomous origin (curiosity, refresh, monitor, scheduler, autonomous, pocket) is **always** refused, even when it carries all three. Every decision, allowed or refused, is logged in `web_metrics` (kind `adaptation`) with the action name, `allowed`, the missing items and `implemented: false`. A permitted request is **still not executed**: the guard returns "no weight-changing implementation ships in this package". The owner would carry out such work outside the package, in isolation, against the stated evaluation and rollback plan. Test: `test_model_adaptation_needs_authorisation_isolated_evaluation_and_rollback`, which also checks that `graph_revisions` is unchanged.

The instance's executable graphs (`instance/graphs.py`: learn, evaluate and promote under an immutable scoring rule) are a separate, older mechanism with their own promotion gate. Nothing described here touches them.

## 5. How to verify

```sh
.venv/bin/python -m pytest -q tests/webcortex/test_evolution.py -k "procedural or adaptation"
nsf knowledge learning            # current weights and fresh statistics (no write)
nsf knowledge learning --update   # one bounded, logged step
nsf knowledge learning --revert   # undo it
nsf knowledge adapt fine-tune     # refused: exit 2, "model adaptation refused: missing ..."
```
