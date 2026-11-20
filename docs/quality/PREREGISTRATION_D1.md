# Pre-registration: earlier-setup recognition (decision D-1)

This file was written and committed **before** any of the implementation or measurement it describes. Its commit hash and time are the audit trail. Thresholds and design below may not be changed after results are seen. A miss is reported as a miss.

## Diagnosis (from the analysis at commit `03687c9`)

1. **The current scenario has no negative control.** Recognition is measured only on lifetimes that really return to the original setup, so a recogniser that always offered "continue there?" would pass. Any recognition rate must be paired with a false-offer rate on returns to genuinely new setups.

2. **Calibration bits alone cannot meet both targets.**
   - A return observes K binary relations: mechanism⊕camera, mechanism⊕partner, and the mechanism when references are read.
   - A brand-new setup reproduces the earlier setup's relations by chance with probability 2^-K: 25% for K=2, 12.5% for K=3.
   - So a bits-only recogniser with recall r makes false offers on about r·2^-K of new setups, before noise.
   - At r ≥ 0.80 and K = 2 that is ≥ 0.20, which violates the bound in §3. The measured sensitivity table at `88f56fd` agrees: 0.64–0.70 even with references.

3. **The missing evidence is the panel's dynamics.** The brief recognises an earlier context from the prediction errors of its stored graph over observed population dynamics (p.10, "Recovering the right circuit"; 95.8% selection in the brief, 95.5% here). In the brief a "panel" is a mechanism with dynamics. A camera remount changes the sensor alignment, not the panel's dynamics. The principled split is:
   - **Which panel** comes from graph prediction evidence on a short observation stream.
   - **Which calibration segment** comes from the camera, word and reference relation evidence (the existing `match_epochs`).

## 1. Product design to implement

1. **Epoch ↔ graph context.**
   - An epoch may declare the graph context of its panel, via `nsf epoch start NAME --context CTX` or a new `nsf epoch context NAME CTX`. This is a new table, `epoch_contexts`, added as migration 5.
   - Governance: forgetting an epoch removes its association, and forgetting the context's datasets has the effects that already exist.

2. **Observation stream for the current epoch.**
   - The owner (or a device) can attach a short population stream to the current epoch: `nsf epoch stream NAME FILE.npz` with arrays `states`, `targets`.
   - It is stored like other evidence: subject to regulation, with an expiry equal to the evidence TTL, forgettable with the epoch, and labelled `observed` or `simulated`.
   - It is evidence about which panel this is. It is never used for training.

3. **Dynamics evidence.** For each candidate earlier epoch E with an active graph G_E:
   - log p(stream | G_E) uses an isotropic Gaussian residual model, with variance equal to G_E's stored validation MSE.
   - The **new-setup** alternative uses the zero predictor with variance equal to the stream's own mean squared target, i.e. "knowing nothing about this panel". This is the model of a panel with unknown dynamics. It is fixed here and never fitted to the scenario.
   - Per-step terms are summed over the stream.

4. **Combined posterior.**
   - Score each candidate (E, calibration segments) as log p(bits | E, segments) + log p(stream | G_E), minus the existing log(#segments) penalty.
   - The new-setup alternative scores the exact bits marginal (as now) plus its stream term.
   - Without a stream, the dynamics term is absent, so the behaviour is identical to now.

5. **Prior.** P(new setup) = (n_new + 1) / (n_new + n_continue + 2). The counts come from the owner's earlier answers to epoch questions in this instance, so the first decision uses ½.

6. **Ask rule unchanged.** Ask "continue there?" when the best candidate's posterior is ≥ 0.75. That threshold is not changed.

## 2. Acceptance scenario changes (`first_physical_test`)

- **Seeds, N = 100 return lifetimes, return length 4 steps, noise: unchanged.**
- **Panel dynamics.** Each lifetime's panel-A also has graph_bench world dynamics. Panel-A's graph is learned and promoted from simulated train and validation data, all labelled simulated. The return attaches a 12-step population stream from the true panel.
  - 12 steps is about 0.4% of a 1536-step training run. It is fixed here.
- **New negative-control arm.** 100 additional lifetimes, from the same seed family with an offset fixed in code, where the "return" is a **new panel**:
  - a new graph_bench world;
  - new random mechanism, camera and partner bits;
  - the **same** camera and partner root names, which is the hard case: the same devices on a different panel.
- **Bits-only arm.** The bits-only recogniser (current code path, no stream) is re-run on both arms and **reported, not judged**, as the information bound.

## 3. Pass criteria (judged)

| Check | Threshold |
|---|---|
| Earlier setup recognised (asked "continue there?" naming panel-A) on true returns | ≥ 0.80 |
| False offers (asked "continue there?" naming any earlier epoch) on new-panel returns | ≤ 0.10 |
| The offered calibration segment is the original (pre-remount) camera segment, among recognised returns | ≥ 0.80 |
| Existing checks: attribution ≥ 0.80, retention ≥ 0.90, confident-wrong < fixed wiring | unchanged |

All four existing checks must still pass, and the other six scenarios must still pass.

## 4. What does not change

The ask threshold (0.75), the segment penalty, reading noise (0.10), MATCH_EVENTS (12), the reference schedule, the seeds, N, and the return length. If the result misses, it is reported with its numbers and nothing here is adjusted.

## Implementation notes

Appended after the commit above, during implementation and before any result was measured. Nothing in §1–§4 is changed. Where the text left a choice open, the most literal reading was taken; each choice is listed here.

1. **Which stream is scored.** The stream is the most recently attached stream of the epoch being matched (highest `epoch_streams.id` for that epoch). Streams of candidate epochs are not used. As with evidence, an expired stream counts until the sweep removes it. The CLI, the console and Pocket sweep before every request.
2. **The dynamics log-likelihood.** The graph G_E is the decoded NSG1 snapshot of the context's `active` revision. It predicts `targets[t] = G_E · states[t]`, as `learner.mse` and recall do. The variance is `graph_revisions.metrics["validation_mse"]`, the packed snapshot's validation MSE that `graphs.learn` writes. The term is Σ_t Σ_i log N(residual_ti; 0, σ²), i.e. per-step isotropic Gaussians summed over the stream. The new-setup term uses the same formula, with residual = target and σ² = mean of targets² over all T×N values.
3. **Candidates without a dynamics term while a stream exists.** This covers a candidate with no declared context, a context with no active graph, an active graph with no positive, finite validation MSE (for example a founding or imported graph), or a graph whose population count differs from the stream's. Such a candidate is scored with the new setup's dynamics term. The stream then neither favours nor penalises it relative to a new setup; it gets no free pass in either direction. The ranking reports `dynamics.source = "new_setup"` and the reason.
4. **Degenerate stream.** If every target is zero, the zero predictor's variance (the mean squared target) is 0. The term is then undefined, and no dynamics term is added for any candidate or for the new setup, the same as having no stream.
5. **The empirical prior's counts.** The schema had no record of a "this is a new setup" answer. Migration 5 holds exactly the two tables named in the brief. So `executive.resolve` now bumps the development counter `epoch_answers_new` on `confirm=False`, and `epoch_answers_continue` on `confirm=True`, for an `epoch:<E>` question. Neither counter is bumped for an idempotent repeat of the same answer, for `confirm=None` (closing without an answer), for system closures, or for answers to change questions. P(new) = (n_new + 1) / (n_new + n_continue + 2). The remaining mass, 1 − P(new), is split evenly across the candidate epochs, as the old ½ was. Like `teacher_interventions`, the counters hold no payload and survive forgetting an epoch.
6. **Return values.** In each ranking entry, `log_likelihood` is the combined score: bits (already net of the segment penalty) + dynamics. `bits_log_likelihood` and `dynamics_log_likelihood` are reported separately, and `dynamics_log_likelihood` is `null` without a stream. `new_setup_log_likelihood` is the combined new-setup score, also split into `new_setup_bits_log_likelihood` and `new_setup_dynamics_log_likelihood`. With no stream and no epoch answers, every value equals the pre-D-1 matcher's, operation for operation; `tests/instance/test_panel_dynamics.py` checks this on random instances.
7. **Governance beyond §1.1–1.2.** `forget_epoch` deletes the epoch's `epoch_contexts` and `epoch_streams` rows. Sweep deletes streams whose `expires_at` has passed; expiry is set at attach time to the evidence TTL. A stream row carries no evidence sequence number, so deleting a stream marks every valid checkpoint stale. A stream also leaves no tombstone, so `checkpoints.restore` drops every stream the live instance no longer holds (matched on id and sha256).
8. **Scenario seeds and offsets, fixed in code.**
   - Panel-A's world for lifetime index i is world 'a' of `datasets.simulate(seed=default_rng([seed, 12, i]).integers(2**31))`. That call simulates 1536 train, 256 validation and 512 heldout steps, all provenance simulated. The world is learned and promoted under gate-v1 as graph context `panel-A`, and epoch `panel-A` declares that context.
   - The return stream is 12 steps of `graph_bench.stream(world, 12, default_rng([seed, 13, i]))`. It is attached, with provenance simulated, right after epoch `return` starts and before its first reading.
   - The new-panel arm has as many lifetimes as the true arm (100 by default), with lifetime indices `NEW_PANEL_OFFSET + j` and `NEW_PANEL_OFFSET = 10000`. Each new-panel lifetime first teaches its own panel-A exactly like a true lifetime. Its return then draws, from `default_rng([seed, 15, index])`: new mechanism, camera and partner bits; 4 return steps of camera and word readings under the same root names and reading noise (0.10); and the seed of the new graph_bench world (world 'a') whose stream is attached.
   - The simulated owner answers every "continue there?" on a new-panel return with `confirm=False`.
9. **Judged quantities.**
   - Recognition: the fraction of true returns in which the executive asked "continue there?" naming panel-A.
   - False offers: the fraction of new-panel returns with any "continue there?" question. Panel-A is the only earlier epoch.
   - Offered segment: the camera segment of the named candidate in `match_epochs` at the moment the first panel-A question is asked. This is exactly what a "continue there" answer links. "Original" means the label `camera-1@0`. In a lifetime whose remount was never confirmed, panel-A has only `camera-1@0`, which counts as original; the report gives the number of recognised returns that had two camera segments. The denominator is the recognised true returns.
   - The existing checks (attribution, retention, confident-wrong compared with fixed wiring) are computed on the true arm only, as before.
10. **Bits-only arm (report-only).** In the restart process, each return of both arms is replayed with the same readings, executive and simulated owner, but without the stream. The replay runs on an in-memory copy of the file, taken before epoch `return` starts. The report-only "continue there" counterfactual previously compared against the judged return, which never continued. Its "not continued" side now uses this bits-only arm, because the judged arm does continue.
