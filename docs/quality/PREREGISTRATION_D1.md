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
