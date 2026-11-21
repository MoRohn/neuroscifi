"""Instance database schema and forward-only migrations.

One SQLite file is one individual. Tables are grouped by owning module; only the
owning module writes its tables, other modules read them or call the owner's API.
Schema version is tracked in PRAGMA user_version. Migrations are append-only:
never edit a released migration, add a new one.
"""

MIGRATIONS = [
    # ---- version 1 ---------------------------------------------------------
    """
    -- store.py: identity, configuration, birth record, counters
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);

    -- evidence.py: calibration contexts and the evidence journal
    CREATE TABLE epochs(
      name TEXT PRIMARY KEY CHECK(length(name) BETWEEN 1 AND 64),
      created_at REAL NOT NULL,
      note TEXT);
    CREATE TABLE evidence(
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      id TEXT UNIQUE NOT NULL CHECK(length(id) BETWEEN 1 AND 128),
      epoch TEXT NOT NULL REFERENCES epochs(name),
      channel TEXT NOT NULL CHECK(channel IN ('camera','word','reference')),
      action INTEGER NOT NULL CHECK(action IN (0,1)),
      bit INTEGER NOT NULL CHECK(bit IN (0,1)),
      root TEXT NOT NULL CHECK(length(root) BETWEEN 1 AND 128),
      sample_id TEXT,
      actor TEXT NOT NULL DEFAULT 'owner',
      consent TEXT NOT NULL DEFAULT 'learn' CHECK(consent IN ('learn','memory_only')),
      provenance TEXT NOT NULL DEFAULT 'observed' CHECK(provenance IN ('observed','simulated')),
      acquired_at REAL,
      recorded_at REAL NOT NULL,
      retention TEXT NOT NULL DEFAULT 'standard' CHECK(retention IN ('standard','promoted')),
      expires_at REAL);
    CREATE INDEX evidence_epoch ON evidence(epoch, seq);
    CREATE INDEX evidence_expiry ON evidence(expires_at) WHERE expires_at IS NOT NULL;
    -- governance.py: forgotten or expired ids. No payload survives; ids cannot be resurrected.
    CREATE TABLE tombstones(
      id TEXT PRIMARY KEY, seq INTEGER, epoch TEXT, deleted_at REAL NOT NULL,
      reason TEXT NOT NULL CHECK(reason IN ('forgotten','expired','epoch_forgotten','partner_forgotten')));

    -- beliefs.py / executive.py: semantic revision history, open questions, decision audit
    CREATE TABLE belief_revisions(
      id INTEGER PRIMARY KEY, epoch TEXT NOT NULL, state_version INTEGER NOT NULL,
      created_at REAL NOT NULL, cause TEXT NOT NULL, summary TEXT NOT NULL);
    CREATE TABLE pending(
      id INTEGER PRIMARY KEY, epoch TEXT, context TEXT,
      kind TEXT NOT NULL CHECK(kind IN ('ask','test_reference','calibrate','new_context')),
      scope TEXT NOT NULL, question TEXT NOT NULL, created_at REAL NOT NULL,
      resolved_at REAL, resolution TEXT);
    CREATE TABLE decisions(
      id INTEGER PRIMARY KEY, epoch TEXT NOT NULL, state_version INTEGER NOT NULL,
      decision TEXT NOT NULL CHECK(decision IN ('act','observe','ask','test_reference','defer')),
      scope TEXT, reason TEXT NOT NULL, created_at REAL NOT NULL);

    -- rules.py: immutable scoring rules (a learner cannot change its own exam)
    CREATE TABLE scoring_rules(
      version TEXT PRIMARY KEY, definition TEXT NOT NULL, sha256 TEXT NOT NULL,
      created_at REAL NOT NULL, created_by TEXT NOT NULL);
    CREATE TRIGGER scoring_rules_immutable_u BEFORE UPDATE ON scoring_rules
      BEGIN SELECT RAISE(ABORT,'scoring rules are immutable'); END;
    CREATE TRIGGER scoring_rules_immutable_d BEFORE DELETE ON scoring_rules
      BEGIN SELECT RAISE(ABORT,'scoring rules are immutable'); END;

    -- datasets.py: population trajectories (npz, no pickle) used to learn and score graphs
    CREATE TABLE datasets(
      id INTEGER PRIMARY KEY, context TEXT NOT NULL,
      split TEXT NOT NULL CHECK(split IN ('train','validation','heldout')),
      provenance TEXT NOT NULL CHECK(provenance IN ('observed','simulated')),
      rows INTEGER NOT NULL, cols INTEGER NOT NULL, sha256 TEXT NOT NULL,
      blob BLOB, note TEXT, created_at REAL NOT NULL, deleted_at REAL);

    -- graphs.py: versioned executable graph (NSG1 snapshots) and promotion decisions
    CREATE TABLE graph_revisions(
      id INTEGER PRIMARY KEY, context TEXT NOT NULL,
      parent_id INTEGER REFERENCES graph_revisions(id),
      status TEXT NOT NULL CHECK(status IN ('candidate','active','archived','rejected','invalidated')),
      bits INTEGER NOT NULL, degree INTEGER NOT NULL, rows INTEGER NOT NULL, cols INTEGER NOT NULL,
      byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL, blob BLOB NOT NULL,
      rule_version TEXT NOT NULL REFERENCES scoring_rules(version),
      origin TEXT NOT NULL CHECK(origin IN ('learner','founding','import')),
      metrics TEXT NOT NULL DEFAULT '{}', decision TEXT,
      created_at REAL NOT NULL, decided_at REAL);
    CREATE UNIQUE INDEX graph_one_active ON graph_revisions(context) WHERE status='active';
    CREATE TRIGGER graph_identity_immutable BEFORE UPDATE OF blob, sha256, rule_version, context, origin
      ON graph_revisions BEGIN SELECT RAISE(ABORT,'graph snapshot identity is immutable'); END;
    CREATE TABLE graph_lineage(
      revision_id INTEGER NOT NULL REFERENCES graph_revisions(id),
      dataset_id INTEGER NOT NULL REFERENCES datasets(id),
      role TEXT NOT NULL CHECK(role IN ('train','validation','heldout')),
      PRIMARY KEY(revision_id, dataset_id));
    CREATE TABLE graph_effects(
      id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES graph_revisions(id),
      dataset_id INTEGER NOT NULL REFERENCES datasets(id), kind TEXT NOT NULL,
      src INTEGER, dst INTEGER, effect REAL NOT NULL, detail TEXT, created_at REAL NOT NULL);
    CREATE TABLE graph_leases(
      context TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at REAL NOT NULL);

    -- checkpoints.py / governance.py: archives and the deletion record
    CREATE TABLE checkpoints(
      id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, policy TEXT NOT NULL,
      sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, state_version INTEGER NOT NULL,
      max_evidence_seq INTEGER NOT NULL, created_at REAL NOT NULL, verified_at REAL,
      status TEXT NOT NULL CHECK(status IN ('valid','stale','deleted','missing')));
    CREATE TABLE deletion_log(
      id INTEGER PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
      requested_at REAL NOT NULL, effects TEXT NOT NULL, failure TEXT);
    """,
    # ---- version 2 ---------------------------------------------------------
    """
    -- governance.py: digests of camera/word roots whose last evidence was removed. The root itself is never
    -- stored; derived memory names it as '<kind>:#<digest>' with digest = sha256(instance_id ':' root)[:16].
    CREATE TABLE forgotten_roots(
      digest TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('camera','partner')), forgotten_at REAL NOT NULL);

    -- rules.py: INSERT OR REPLACE must not overwrite a registered rule (connect() also turns on
    -- recursive_triggers, so REPLACE's implicit delete fires scoring_rules_immutable_d).
    CREATE TRIGGER scoring_rules_immutable_i BEFORE INSERT ON scoring_rules
      WHEN EXISTS (SELECT 1 FROM scoring_rules WHERE version=NEW.version)
      BEGIN SELECT RAISE(ABORT,'scoring rules are immutable'); END;
    """,
    # ---- version 3 ---------------------------------------------------------
    """
    -- beliefs.py / executive.py: owner answers about changes (confirmed changepoints and rejections).
    -- Same statements as beliefs.DDL. IF NOT EXISTS because a version-2 file written by software that
    -- created these lazily (beliefs.ensure_tables) may already have them.
    CREATE TABLE IF NOT EXISTS changepoints(id INTEGER PRIMARY KEY, epoch TEXT NOT NULL, scope TEXT NOT NULL, since_seq INTEGER NOT NULL, pending_id INTEGER, created_at REAL NOT NULL, confirmed INTEGER NOT NULL);
    -- Covering index for the per-epoch tallies (learning rows, sample dedupe and memory-only counts).
    CREATE INDEX IF NOT EXISTS evidence_tally ON evidence(epoch, consent, sample_id, channel, root, bit, action);
    """,
    # ---- version 4 ---------------------------------------------------------
    """
    -- executive.py writes, beliefs.py reads: an epoch that continues an earlier setup ("continue there").
    -- segments is JSON {factor: segment label}, e.g. {"camera:cam-a": "cam-a@0", "mechanism": "mechanism@0"};
    -- labels name roots, so governance deletes a link when one of its roots is forgotten.
    CREATE TABLE epoch_links(
      epoch TEXT PRIMARY KEY, source_epoch TEXT NOT NULL, segments TEXT NOT NULL,
      pending_id INTEGER, created_at REAL NOT NULL);
    """,
    # ---- version 5 ---------------------------------------------------------
    """
    -- evidence.py writes, beliefs.py reads: the graph context of an epoch's panel (decision D-1).
    CREATE TABLE epoch_contexts(
      epoch TEXT PRIMARY KEY REFERENCES epochs(name), context TEXT NOT NULL, created_at REAL NOT NULL);
    -- evidence.py: short population streams observed in an epoch (npz of float64 states and targets, read with
    -- allow_pickle=False). Evidence about which panel this is, never training data; they expire with the evidence
    -- TTL and are forgotten with their epoch.
    CREATE TABLE epoch_streams(
      id INTEGER PRIMARY KEY, epoch TEXT NOT NULL REFERENCES epochs(name), blob BLOB NOT NULL,
      rows INTEGER, cols INTEGER, sha256 TEXT NOT NULL,
      provenance TEXT NOT NULL CHECK(provenance IN ('observed','simulated')),
      created_at REAL NOT NULL, expires_at REAL);
    """,
    # ---- version 6 ---------------------------------------------------------
    """
    -- webcortex/state.py: Web Cortex memory (docs/web-cortex/ARCHITECTURE.md §6, ADR-029). Vocabularies are the
    -- literals of webcortex/types.py at the time of this migration (tests/webcortex/test_state.py checks them).
    -- Raw page bodies are not here: they live in the separate public cache (ADR-023).
    -- Working memory: one research task per row; budget/usage/checkpoint/result/executed_on are JSON.
    CREATE TABLE web_tasks(
      id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 1 AND 64),
      objective TEXT NOT NULL CHECK(length(objective) BETWEEN 1 AND 2000),
      mode TEXT NOT NULL CHECK(mode IN ('quick_lookup','deep_research','continuous','comparative','scientific',
        'technical','change_detection','fact_verification','gap_resolution')),
      status TEXT NOT NULL CHECK(status IN ('queued','discovering','reading','evaluating','synthesizing',
        'completed','partial','failed','cancelled')),
      stage TEXT, origin TEXT NOT NULL, family TEXT, parent_id TEXT,
      budget TEXT NOT NULL DEFAULT '{}', usage TEXT NOT NULL DEFAULT '{}', checkpoint TEXT, result TEXT,
      error TEXT, executed_on TEXT,
      created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL,
      cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
      lease_holder TEXT, lease_expires_at REAL);
    CREATE INDEX web_tasks_status ON web_tasks(status, updated_at);
    CREATE INDEX web_tasks_family ON web_tasks(family, created_at);
    -- Episodic memory: one row per stage event of a task (task_id NULL for monitor/prediction events).
    CREATE TABLE web_events(
      id INTEGER PRIMARY KEY, task_id TEXT,
      kind TEXT NOT NULL CHECK(kind IN ('submitted','started','stage','query','search','fetch','extract','render',
        'crawl','claim','error','decision','finding','synthesized','consolidated','prediction','prediction_error',
        'feedback','completed','partial','failed','cancelled','resumed','cancel_requested')),
      stage TEXT, detail TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
    CREATE INDEX web_events_task ON web_events(task_id, id);
    -- Provenance: each document a task read (content_sha256 keys the public cache).
    CREATE TABLE web_sources(
      id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, url TEXT NOT NULL, final_url TEXT,
      canonical_url TEXT NOT NULL, domain TEXT NOT NULL, title TEXT,
      kind TEXT NOT NULL CHECK(kind IN ('page','pdf','api','feed','text','json')),
      published_at REAL, modified_at REAL, retrieved_at REAL NOT NULL,
      retrieval_mode TEXT NOT NULL CHECK(retrieval_mode IN ('live','cache','api','feed','rendered')),
      content_sha256 TEXT, provider TEXT, flags TEXT NOT NULL DEFAULT '{}', metadata TEXT NOT NULL DEFAULT '{}',
      created_at REAL NOT NULL, UNIQUE(task_id, canonical_url));
    CREATE INDEX web_sources_canonical ON web_sources(canonical_url);
    CREATE INDEX web_sources_domain ON web_sources(domain);
    -- Extracted claims (raw, not accepted). The same claim id may be extracted by several tasks.
    CREATE TABLE web_claims(
      id TEXT NOT NULL, task_id TEXT NOT NULL, source_id INTEGER, text TEXT NOT NULL, key TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('finding','interpretation','statement','metadata')),
      polarity INTEGER NOT NULL CHECK(polarity IN (-1,1)), claim_values TEXT NOT NULL DEFAULT '[]',
      source_url TEXT NOT NULL, canonical_url TEXT, domain TEXT NOT NULL, title TEXT,
      published_at REAL, event_at REAL, retrieved_at REAL NOT NULL, verified_at REAL, relevance REAL,
      doc_sha256 TEXT, sentence_index INTEGER,
      cluster_status TEXT CHECK(cluster_status IN ('single_source','corroborated','contested','insufficient')),
      confidence REAL, flags TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
      PRIMARY KEY(task_id, id));
    CREATE INDEX web_claims_key ON web_claims(key, task_id);
    CREATE INDEX web_claims_id ON web_claims(id);
    CREATE INDEX web_claims_url ON web_claims(canonical_url);
    -- Knowledge graph edges between claims.
    CREATE TABLE web_claim_links(
      id INTEGER PRIMARY KEY, task_id TEXT, src TEXT NOT NULL, dst TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('supports','contradicts','supersedes','duplicates')),
      detail TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
    CREATE INDEX web_claim_links_src ON web_claim_links(src);
    CREATE INDEX web_claim_links_dst ON web_claim_links(dst);
    CREATE INDEX web_claim_links_task ON web_claim_links(task_id);
    -- Semantic + temporal memory: versioned statements; a new value supersedes, never overwrites (ADR-026).
    CREATE TABLE web_knowledge(
      id INTEGER PRIMARY KEY, key TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 1),
      statement TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('provisional','accepted','stale','archived','rejected','superseded')),
      confidence REAL NOT NULL, uncertainty REAL NOT NULL,
      freshness_class TEXT NOT NULL CHECK(freshness_class IN ('breaking','volatile','release','scientific','stable')),
      first_verified_at REAL NOT NULL, last_verified_at REAL NOT NULL, valid_from REAL, valid_until REAL,
      superseded_by INTEGER, supporting TEXT NOT NULL DEFAULT '[]', contradicting TEXT NOT NULL DEFAULT '[]',
      sources TEXT NOT NULL DEFAULT '[]', claim_values TEXT NOT NULL DEFAULT '[]',
      polarity INTEGER NOT NULL DEFAULT 1 CHECK(polarity IN (-1,1)), note TEXT,
      pending_id INTEGER, task_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
      UNIQUE(key, version));
    CREATE INDEX web_knowledge_status ON web_knowledge(status, key);
    -- Knowledge graph: entities resolved by casefolded normalised name, and where they are mentioned.
    CREATE TABLE web_entities(
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL CHECK(kind IN ('name','package','acronym','identifier')),
      first_seen REAL NOT NULL, last_seen REAL NOT NULL);
    CREATE TABLE web_mentions(
      id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL REFERENCES web_entities(id) ON DELETE CASCADE,
      claim_id TEXT, knowledge_id INTEGER, task_id TEXT, created_at REAL NOT NULL);
    CREATE INDEX web_mentions_entity ON web_mentions(entity_id);
    CREATE INDEX web_mentions_task ON web_mentions(task_id);
    CREATE INDEX web_mentions_knowledge ON web_mentions(knowledge_id);
    -- Procedural memory: provider health, circuit-breaker state and mode outcome statistics.
    CREATE TABLE web_strategies(name TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL);
    -- Continuous investigation.
    CREATE TABLE web_monitors(
      id INTEGER PRIMARY KEY, topic TEXT NOT NULL CHECK(length(topic) BETWEEN 1 AND 2000), plan TEXT,
      mode TEXT NOT NULL CHECK(mode IN ('quick_lookup','deep_research','continuous','comparative','scientific',
        'technical','change_detection','fact_verification','gap_resolution')),
      interval_seconds REAL NOT NULL CHECK(interval_seconds >= 3600), budget TEXT NOT NULL DEFAULT '{}',
      baseline TEXT NOT NULL DEFAULT '{}', authorized_by TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('proposed','active','paused','stopped')),
      pending_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL, last_run_at REAL,
      next_run_at REAL, last_task_id TEXT, runs INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX web_monitors_due ON web_monitors(status, next_run_at);
    -- Prediction: an expectation about a knowledge item, checked by a later refresh.
    CREATE TABLE web_predictions(
      id INTEGER PRIMARY KEY, knowledge_id INTEGER NOT NULL, expectation TEXT NOT NULL,
      check_after REAL NOT NULL, created_at REAL NOT NULL, task_id TEXT, checked_at REAL,
      outcome TEXT CHECK(outcome IN ('confirmed','error','inconclusive')), detail TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX web_predictions_due ON web_predictions(outcome, check_after);
    -- Metacognition: what each investigation did to a knowledge item.
    CREATE TABLE web_feedback(
      id INTEGER PRIMARY KEY, task_id TEXT, knowledge_id INTEGER, key TEXT NOT NULL,
      effect TEXT NOT NULL CHECK(effect IN ('strengthened','weakened','unchanged','reversed','new')),
      before_confidence REAL, after_confidence REAL, info_gain REAL NOT NULL DEFAULT 0,
      detail TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
    CREATE INDEX web_feedback_key ON web_feedback(key, created_at);
    """,
    # ---- version 7 (Web Cortex remediation) ------------------------------------------------------------------
    """
    -- Payload-free tombstones of forgotten web memory (ADR-029): digest = governance.web_digest (instance-salted
    -- sha256 of the task id, knowledge id+key, canonical URL or domain). checkpoints.restore replays them on a
    -- restored copy so nothing forgotten comes back.
    CREATE TABLE web_forgotten(
      kind TEXT NOT NULL CHECK(kind IN ('task','knowledge','source_url','source_domain')),
      digest TEXT NOT NULL, forgotten_at REAL NOT NULL, PRIMARY KEY(kind, digest));
    -- Index-friendly "a newer release was seen since" scans (knowledge.newer_release_seen).
    CREATE INDEX web_claims_created ON web_claims(created_at);
    """,
    # ---- version 8 (knowledge evolution, ADR-038; docs/knowledge/KNOWLEDGE_EVOLUTION.md) ----------------------
    """
    -- Provenance kind, verification record (ADR-035 state, category, basis, groups) and the separate times of a
    -- knowledge version. Rows written before this migration passed the ADR-026 bar: their verification records
    -- that policy. Publication and retrieval times are copied from the row's own sources; event time is unknown
    -- for them (NULL), never invented. Effective time stays valid_from; verification time last_verified_at.
    ALTER TABLE web_knowledge ADD COLUMN provenance TEXT NOT NULL DEFAULT 'verified_conclusion'
      CHECK(provenance IN ('verified_conclusion','user_asserted'));
    ALTER TABLE web_knowledge ADD COLUMN verification TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE web_knowledge ADD COLUMN published_at REAL;
    ALTER TABLE web_knowledge ADD COLUMN event_at REAL;
    ALTER TABLE web_knowledge ADD COLUMN retrieved_at REAL;
    UPDATE web_knowledge SET verification='{"policy":"ADR-026"}',
      published_at=(SELECT max(json_extract(j.value,'$.published_at')) FROM json_each(web_knowledge.sources) j),
      retrieved_at=(SELECT max(json_extract(j.value,'$.retrieved_at')) FROM json_each(web_knowledge.sources) j);
    -- Provisional claims kept as research opportunities and the hypotheses of the predictive loop. Never
    -- verified knowledge: a hypothesis becomes knowledge only through consolidation and the owner's review.
    CREATE TABLE web_hypotheses(
      id INTEGER PRIMARY KEY, key TEXT NOT NULL, statement TEXT NOT NULL,
      origin TEXT NOT NULL CHECK(origin IN ('insufficient','conflicting','temporally_unresolved','disputed',
        'not_evaluated','gap','prediction_error','owner')),
      status TEXT NOT NULL CHECK(status IN ('open','supported','refuted','revised','abandoned')),
      verification TEXT NOT NULL DEFAULT '{}', claims TEXT NOT NULL DEFAULT '[]',
      claim_values TEXT NOT NULL DEFAULT '[]', polarity INTEGER NOT NULL DEFAULT 1 CHECK(polarity IN (-1,1)),
      alternatives TEXT NOT NULL DEFAULT '[]', knowledge_id INTEGER, parent_id INTEGER, task_id TEXT, note TEXT,
      created_at REAL NOT NULL, updated_at REAL NOT NULL, resolved_at REAL);
    CREATE INDEX web_hypotheses_key ON web_hypotheses(key, status);
    CREATE INDEX web_hypotheses_task ON web_hypotheses(task_id);
    -- Predictions about a knowledge item OR a hypothesis, with resolution criteria, a deadline and the forecast
    -- probability (calibration). Rebuilt because knowledge_id was NOT NULL; every row and id is copied.
    CREATE TABLE web_predictions_v8(
      id INTEGER PRIMARY KEY, knowledge_id INTEGER, hypothesis_id INTEGER, expectation TEXT NOT NULL,
      criteria TEXT NOT NULL DEFAULT '{}',
      probability REAL CHECK(probability IS NULL OR (probability >= 0 AND probability <= 1)),
      check_after REAL NOT NULL, deadline REAL, created_at REAL NOT NULL, task_id TEXT, checked_at REAL,
      outcome TEXT CHECK(outcome IN ('confirmed','error','inconclusive')), detail TEXT NOT NULL DEFAULT '{}',
      CHECK(knowledge_id IS NOT NULL OR hypothesis_id IS NOT NULL));
    INSERT INTO web_predictions_v8(id,knowledge_id,expectation,check_after,created_at,task_id,checked_at,outcome,
      detail) SELECT id,knowledge_id,expectation,check_after,created_at,task_id,checked_at,outcome,detail
      FROM web_predictions;
    DROP TABLE web_predictions;
    ALTER TABLE web_predictions_v8 RENAME TO web_predictions;
    CREATE INDEX web_predictions_due ON web_predictions(outcome, check_after);
    CREATE INDEX web_predictions_hypothesis ON web_predictions(hypothesis_id);
    -- Knowledge graph edges between typed nodes (claim, source, knowledge, interval, entity, hypothesis,
    -- prediction, observation, error). Rebuilt to add the node kinds and edge kinds; every row is copied as
    -- claim -> claim.
    CREATE TABLE web_claim_links_v8(
      id INTEGER PRIMARY KEY, task_id TEXT,
      src_kind TEXT NOT NULL DEFAULT 'claim' CHECK(src_kind IN ('claim','source','knowledge','interval','entity',
        'hypothesis','prediction','observation','error')), src TEXT NOT NULL,
      dst_kind TEXT NOT NULL DEFAULT 'claim' CHECK(dst_kind IN ('claim','source','knowledge','interval','entity',
        'hypothesis','prediction','observation','error')), dst TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('supports','contradicts','supersedes','duplicates','derived_from',
        'evidence_for','valid_during','related_to','predicts','observed','error_of','revised_to')),
      detail TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
    INSERT INTO web_claim_links_v8(id,task_id,src,dst,kind,detail,created_at)
      SELECT id,task_id,src,dst,kind,detail,created_at FROM web_claim_links;
    DROP TABLE web_claim_links;
    ALTER TABLE web_claim_links_v8 RENAME TO web_claim_links;
    CREATE INDEX web_claim_links_src ON web_claim_links(src);
    CREATE INDEX web_claim_links_dst ON web_claim_links(dst);
    CREATE INDEX web_claim_links_task ON web_claim_links(task_id);
    -- Metacognition snapshots: numbers only (calibration, evolution metrics, procedural research-policy
    -- statistics and model-adaptation guard decisions); never statements, objectives or URLs.
    CREATE TABLE web_metrics(
      id INTEGER PRIMARY KEY,
      kind TEXT NOT NULL CHECK(kind IN ('calibration','evolution','procedural','adaptation')),
      value TEXT NOT NULL DEFAULT '{}', computed_at REAL NOT NULL);
    CREATE INDEX web_metrics_kind ON web_metrics(kind, id);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def migrate(db):
    """Apply pending migrations inside one transaction. Refuse a newer schema."""
    from .errors import Conflict
    current = db.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise Conflict(f"Instance schema {current} is newer than this software ({SCHEMA_VERSION})")
    for version in range(current, SCHEMA_VERSION):
        db.executescript("BEGIN IMMEDIATE;" + MIGRATIONS[version] + f";PRAGMA user_version={version+1};COMMIT;")
    return SCHEMA_VERSION
