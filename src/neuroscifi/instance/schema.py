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
