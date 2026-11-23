# System architecture

## 1. Shape of the system

NeuroSciFi runs as a modular monolith: one Python package (`neuroscifi`), one SQLite file per individual, and two small HTTP servers with different trust boundaries. Nothing calls a cloud service. ADR-002 explains why there are no microservices.

```
                Phone / tablet (browser)                         Owner at the Spark (loopback)
       ┌───────────────────────────────────┐           ┌──────────────────────────────────┐
       │ Pocket PWA (pocket/web)           │           │ Console (console/web)            │
       │  sensors → coarse category → review│           │  birth · beliefs · evidence ·    │
       │  field episodes + forecast (local)│           │  decisions · graphs · checkpoints│
       │  AES-GCM vault · service worker   │           └──────────────┬───────────────────┘
       └──────────────┬────────────────────┘                          │ Bearer session token,
                      │ HTTPS on LAN/VPN (TLS required off-loopback)  │ 127.0.0.1 only
       ┌──────────────▼────────────────────┐           ┌──────────────▼───────────────────┐
       │ nsf pocket serve (pocket/server)  │           │ nsf console (console/server)     │
       │ pairing · 4-field receipts ·      │           │ JSON API → instance service layer│
       │ field episodes · 24 h expiry      │           └──────────────┬───────────────────┘
       └──────────────┬────────────────────┘                          │
                      │ mobile-private.sqlite (separate             │
                      │ privacy boundary, ADR-005)       ┌───────────▼─────────────────────┐
                                                          │ neuroscifi.instance             │
   nsf CLI ─────────────────────────────────────────────▶ │ store · evidence · beliefs ·    │
   (init, observe, decide, graph …, forget, checkpoint,   │ executive · datasets · learner ·│
    acceptance, bench)                                    │ graphs · recall · rules ·       │
                                                          │ memory · governance · checkpoints│
                                                          └───────────┬─────────────────────┘
                                                                      │ individual.sqlite (WAL)
                                                          ┌───────────▼─────────────────────┐
                                                          │ primitives: graph_store (NSG1), │
                                                          │ archive (NSFA), lab.* (research)│
                                                          └─────────────────────────────────┘
```

## 2. Packages and responsibilities

| Package | Responsibility | Reuse class (see PROTOTYPE_AUDIT) |
|---|---|---|
| `neuroscifi.lab` | The prototype's research experiments. `scripts/reproduce.py` verifies them bit-for-bit. | Reused directly |
| `neuroscifi.graph_store` | NSG1 snapshot codec: rows, sources, 4/8-bit or FP32, SHA-256 trailer. | Reused directly |
| `neuroscifi.archive` | NSFA lossless archive: per-block codec, checksums, footer, consistent SQLite checkpoint. | Reused directly |
| `neuroscifi.legacy_live` | The prototype's persistent binary learner. Kept as a parity oracle and for `import-legacy`. | Reused (oracle) |
| `neuroscifi.instance` | The integrated individual: store, evidence, beliefs, executive, datasets, learner, graphs, recall, rules, memory, governance, checkpoints. | New; extends `legacy_live`, `graph_bench`, `context_recall` |
| `neuroscifi.pocket` | Bridge server and PWA. | Refactored and extended |
| `neuroscifi.console` | Loopback owner console: HTTP API and web UI in Pocket's visual language. | New |
| `neuroscifi.acceptance` | Executable first-release milestone scenarios (simulated). | New |

## 3. Data architecture

**Individual (`individual.sqlite`).** The schema is in `instance/schema.py`. Its versioning is described in API_CONTRACTS §0.
- **Journal.** `evidence` is append-only. Rows leave it only by forget or expiry, and each departure leaves a payload-free `tombstones` row.
- **Derived state.** Beliefs are computed on read from active evidence, so a deletion takes effect on the next read without any cache invalidation. `belief_revisions` records each change of the MAP assignment.
- **Executable state.** `graph_revisions` holds immutable NSG1 snapshots, with lineage to `datasets` and a pinned `scoring_rules` version. At most one revision per context is active, enforced by a unique partial index.
- **Governance.** `deletion_log`, `checkpoints` (status `valid`, `stale`, `deleted` or `missing`) and `counters`.
- **Pragmas.** WAL, `foreign_keys`, `secure_delete`, a 10-second busy timeout. Files are created mode 0600.

**Pocket (`mobile-private.sqlite`).** A separate database with per-pairing isolation, 24-hour expiry and tombstones (prototype design). The runbook excludes it from backups (OPS-05).

**Local browser vault.** An AES-GCM IndexedDB/localStorage record keyed by a passphrase. It never leaves the device.

## 4. Key flows

1. **Teach and observe.** `nsf observe` or `POST /api/observe` → `evidence.observe` validates the payload, checks regulation, tombstones and idempotency, appends the row, recomputes beliefs and writes a revision when needed. `executive.decide` then returns act, observe, ask, test_reference or defer.
2. **Camera remounted, no announcement.** Camera readings start disagreeing. `beliefs.detect_change` finds the `camera:<root>` changepoint. The executive asks for calibration of exactly that scope and records a pending question. The owner starts a new epoch for the remounted setup. The old epoch's beliefs stay intact for tomorrow.
3. **Graph growth.** Datasets → `graphs.learn` (under a lease) → candidate (pinned rule) → `graphs.promote` gate. On a pass the incumbent is archived. If the candidate fails retention, the gate recommends a new context and the old skill is preserved. `learn` fits two graphs on the stream, a dense one and a sparse one whose inputs are rewired by edge exchange, and the pinned rule decides which is packed: a new individual is born with `gate-v1` and then `best-learner-v1`, under which the snapshot is the smallest file within tolerance of the better learner (an individual born earlier keeps `gate-v1`, which packs the dense fit, until its owner adopts the newer rule). Whatever the rule, the gate rejects a snapshot that predicts no better than a graph with no edges (ADR-045).
4. **Which skill applies now?** `recall.run` streams observations, and every context's active graph predicts before each outcome. The rolling error selects a graph, or reports `unknown_context` as an owner question.
5. **Forget.** `governance.forget_*` deletes the evidence or dataset, tombstones it, recomputes beliefs, supersedes affected pending questions, invalidates derived graphs (re-activating an eligible older one or recording the failure) and marks checkpoints stale. `rebuild_stale` produces a clean archive and unlinks the stale ones. `restore` re-applies current tombstones. After a voice or web forget, `checkpoints.rewrite` rewrites each archive made stale only by such forgets in place, with the same replay, crash-safe (journal and atomic rename), and marks it valid again.

## 5. Trust boundaries and security controls

| Boundary | Controls |
|---|---|
| Phone ↔ Pocket server (LAN) | TLS required off loopback. One-use 5-minute invitation compared with `hmac.compare_digest`. Per-pairing bearer stored as a SHA-256 fingerprint. Strict field allowlists and vocabularies. 512-byte bodies. Origin check. Strict CSP (`default-src 'none'`). `no-store`. No access logs. Per-pairing storage isolation. |
| Owner ↔ Console | Binds to 127.0.0.1 only and refuses other addresses. Per-start random session token passed in the URL fragment (never sent in requests as a URL). Bearer header. Host and Origin checks. 4 KiB bodies. Same CSP family. No access logs. |
| CLI ↔ instance file | OS file permissions (0600 file, 0700 checkpoint directory). The systemd unit runs as a dedicated service user with `ProtectSystem=strict` and an explicit `StateDirectory`. |
| Learner ↔ its exam | Scoring rules are immutable (triggers). The candidate pins its rule. Rules are added only through an owner command with `--owner-approved`. |
| Evidence ↔ derived artifacts | Lineage tables, the deletion cascade, and tombstones re-applied on restore. |

AI-agent action controls (from the mandate): the executive never acts on external systems. It returns a decision and a reason, and records pending questions for the owner. Graph creation for unknown contexts requires the owner (GRA-06). Promotion is gated and auditable through the `decision` JSON.

## 6. Operational model

- **Processes.** `nsf pocket serve` (LAN-capable) and `nsf console` (loopback), each under its own systemd unit. Learning and checkpointing are explicit jobs (CLI or console POST), so the interactive request path never runs them (ADR-012).
- **Observability.** JSON outputs. `nsf regulation` reports backlog, write volume, pause state and open questions. `nsf birth` reports the inventory and development counters. `nsf bench` reports decision latency percentiles on the host. The servers keep no content logs by design.
- **Recovery.** `nsf checkpoint create|verify|restore`. Restore never overwrites and never resurrects forgotten ids.

## 7. Technology choices

Python 3.12 with numpy 2.3.5 and zstandard 0.25.0 (prototype pins). SQLite from the standard library. The web side is vanilla ES modules with no build step and a strict CSP. Tests use pytest, Node for the JavaScript checks, and Playwright with Chromium plus axe-core for end-to-end and accessibility. Linting is ruff with error-class rules only (ADR-004).
