# Integration status

Updated at every wave boundary. Evidence is the integrator's own re-run, not an agent's report.

| Seam | Producer → consumer | State | Evidence |
|---|---|---|---|
| Package and CLI registration | W1 → all | Integrated | `nsf --help`; check.sh green at `392ea0f` |
| Instance schema and store | architect → W2-A/B/C | Integrated | `nsf init/birth/regulation` smoke; immutability trigger fires (`ef95e48`) |
| `rules.register` at birth → graph gate | architect → W2-B | Integrated | test_graphs gate (d), (e); rule pinned per candidate |
| evidence → beliefs → executive | W2-A internal | Integrated | 66 tests incl. 220-sequence legacy parity at 1e-12 |
| governance → `graphs.invalidate_dependents` | W2-C → W2-B | Integrated | test_governance dataset cascade; test_journeys |
| checkpoints → `archive.convert` | W2-C → prototype primitive | Integrated | test_checkpoints (writer-open backup, corruption, no resurrection) |
| governance → beliefs (post-deletion revision) | W2-C → W2-A | Integrated after fix `a5f4832` | test_journeys::test_forget_records_the_real_post_deletion_belief_state |
| CLI → governance.sweep before each command | architect | Integrated | test_journeys::test_cli_sweeps_expired_evidence_before_reading |
| Pocket web ↔ bridge (mute/unmute) | W2-D | Integrated | test_mobile; 13 Playwright e2e (re-run by integrator) |
| console → instance service | W3 | Not started | — |
| acceptance → instance service | W3 | Not started | — |
