# Schema versions, upgrades and imports

## 1. How the instance database is versioned

The instance database (one SQLite file per individual) stores its schema version in `PRAGMA user_version`. The software holds an append-only list of migrations in `src/neuroscifi/instance/schema.py`; the current schema version is the length of that list (21 with OMNIMIND's form outcomes; 20 with its artifact store; 19 before it; 13 in 0.7.0).

- **Forward only.** Opening an instance with `nsf` applies every pending migration in order, each in one transaction that also sets `user_version`. The version is read again under the write lock, so two processes opening an old file at once apply each migration once; a migration that fails is rolled back. There are no down-migrations. A released migration is never edited; changes arrive as a new migration.
- **Newer than the software is refused.** If `user_version` is greater than the software's version, the command stops with "Instance schema N is newer than this software (M)" and changes nothing. Do not run older software on a database an upgrade has opened.
- Because any `nsf` command that opens the database can migrate it, the first command after installing new software is the upgrade. Plan it; do not let the nightly timer or the console be the first thing to touch it.

Read the version with `sqlite3 /var/lib/neuroscifi/instance.sqlite 'PRAGMA user_version'` if the sqlite3 tool is installed *(not part of the project)*.

**Version 14 (consolidation, ADR-044).** It adds one empty table, `evidence_consolidated`, with four indexes (by epoch and seq, the tallies, the copies of a sample with their readings, by root), and an index on the tombstones by epoch and seq. A file that was opened by an earlier build of this unreleased version is brought to this set of indexes when it is next opened. No existing row is changed; building the tombstone index reads every tombstone once (well under a second for a million). What changes is the next sweep: with the default setting, an expiring learning event leaves a consolidated reading instead of nothing (RUNBOOK section 6). An individual upgraded from 0.7.0 has no `consolidation` setting saved and gets the default, on. To keep the 0.7.0 meaning of expiry, run `nsfs config --no-consolidation` as the first command after the upgrade, with the services still stopped: `nsf config` is the one instance command besides `nsf sweep` that does not expire due evidence before it runs, so the setting is in place before the first sweep (a test does exactly this on a version-13 file whose events are all past their lifetime). A checkpoint restored later keeps the setting of the file it is restored from, whatever the archive held. 0.7.0 software refuses a version-14 file.

**Version 15 (forgetting reaches what was left behind, ADR-048).** It adds one empty table, `forgotten_scopes`
(the instance-salted digest and kind of a camera or partner name forgotten while the other channel still holds the
name), and replaces the graph identity trigger so that exactly one update of an invalidated revision is allowed: its
weights erased and the digest of its weights set to `erased` when `forget_dataset` removes the data it learned from.
Every other change to a revision's identity is refused as before, a new trigger keeps an erased revision
invalidated, and another refuses deleting a revision (which also refuses INSERT OR REPLACE over one). Nothing is
written to existing rows by the migration. The first writable open after it finishes, once, the forgets of datasets
an earlier version made, which kept the weights learned from them (logged as dataset deletions with `by: upgrade`;
valid checkpoints become stale, as after any forget): plan the checkpoint rebuild after the upgrade. A file that a
development build of version 15 migrated (an identity trigger without the `erased` digest, no delete trigger) gets
the three triggers of the release at its next writable open (`schema.ensure_triggers`, as `ensure_indexes` does for
14); a step at open that a check of the file refuses is logged and tried again at the next open, never failing it.

**Version 16 (which consolidated readings may be a later copy of a sample, ADR-044).** It adds a column `copied` to
`evidence_consolidated`, a partial index of the flagged readings and three triggers, and flags each reading already
held that has an earlier reading of the same sample (one indexed lookup per reading with a sample id, once: about a
second per million). Nothing else is written; no reading is added, removed or changed in what it says. A belief
computation then finds the samples recorded more than once from the flagged readings instead of grouping every
reading of the epoch by sample on every read. Software of an earlier schema refuses a version-16 file; rollback is the owner's own
copy taken before the upgrade (section 2, \"Rollback\").

**Version 17 (the imagined store, ADR-049).** It adds three empty tables: `imagined` (content the system produced
itself that is not evidence: simulations, speculations, model-written inferences), `imagined_refs` (what each record
was derived from or what its outcome names) and `imagined_forgotten` (payload-free tombstones), with their indexes
and triggers, and on fifteen evidence-side tables (evidence, consolidated readings, belief revisions, tasks, events,
web sources, claims, claim links, knowledge, mentions, hypotheses, predictions, feedback, voice segments, monitors) a
pair of triggers that refuse an id of the form `imagined:` plus 32 hex digits in a reference column (free text is
never tested). No existing row is read or written; the migration takes milliseconds. Version 17 was amended twice
before release: a file migrated by either earlier build gets the current triggers, `imagined_refs.observed` and the
tombstone index at its next open, in one short transaction (`schema.ensure_triggers`; a second process opening it at
the same moment waits and finds it done); records observed under the first build are removed in that transaction,
each with a tombstone and one deletion-log row (kind `imagined`), as their outcomes kept text no forget reached. The
store stays empty until a mechanism writes to it; at the defaults nothing does (Wave 1: scenario records with
`imagine_scenarios` on, `nsf imagine ... --keep` when the owner asks, a reflection's model summary with
`reflection_model_summary` on). Two settings come with it,
`max_imagined_rows` (1000) and `imagined_lifetime_seconds` (7 days), changeable with `nsf config
--max-imagined-rows N` and `--imagined-lifetime-days D`; a file saved by older software reads them as these
defaults. `nsf imagined list` shows what is held. Software of an earlier schema refuses a version-17 file; rollback is
the owner's own copy taken before the upgrade (section 2, \"Rollback\").

**Version 18 (knowledge that knows what it rests on, ADR-055).** It adds five empty tables: `web_hypothesis_details`
(the assumptions, counter-evidence, expected information gain, cost and revision record of each hypothesis recorded
after the upgrade; older hypotheses get none), `web_premises` (what a knowledge item or hypothesis rests on; a CHECK
refuses an imagined id), `web_rechecks` ("to re-check" marks; a mark changes no belief), `web_reflections` (reflection
reports) and `improvement_ledger` (append-only: a trigger refuses an update), with their indexes. `IF NOT EXISTS`
throughout, so the migration can run again on a file that already holds these objects. No existing row is read or
written; the migration takes milliseconds. Forgetting covers the new rows (`governance._premise_cascade` in the web
forget and in a restore's replay). The settings that write to them are WebConfig switches: `hypothesis_fields` and
`premise_recheck` on (records only), `idle_recheck`, `hypothesis_abandon` and `reflection` off, `strategy_selection`
"gate" (off until a promotion passes the gate). The improvement ledger also refuses a DELETE (trigger
`improvement_ledger_no_delete`, added to version 18 before release after the Wave 1 review, S7): a file migrated by
18's first build gets that trigger at its next open (`schema.ensure_triggers`, `_TRIGGERS_18`). Software of an earlier
schema refuses a version-18 file; rollback is the owner's own copy taken before the upgrade (section 2, \"Rollback\").

**Version 19 (an index for reads by kind and time, Wave 1 review A2/A3).** One statement: `CREATE INDEX IF NOT
EXISTS web_events_kind_time ON web_events(kind, created_at)`. It changes no row; building it reads `web_events` once
(milliseconds on a small file; proportional to the number of web events on a large one). It serves Presence's
predictions and findings, the selective broadcast's hourly count, the autonomy windows and the self-description's
decision reads (measured: Presence derive at 500,000 web events 41.15 → 0.15 ms; one more index costs about 10 % per
web-event insert, 5.39 → 5.94 µs; `genesis_fix1a.json`). No setting comes with it and nothing changes at the defaults.
Software of an earlier schema refuses a version-19 file; rollback is the owner's own copy taken before the upgrade (section 2, \"Rollback\").

**Version 20 (the artifact store's index, OMNIMIND lane A, ADR-071).** Five tables, IF NOT EXISTS throughout:
`artifacts`, `artifact_links`, `artifact_events` (append-only by trigger), `artifact_forgotten` (tombstones) and
`artifact_gaps`, plus, on every reference column ADR-049 protects, triggers that refuse an artifact id
(`schema.NO_ARTIFACT_TRIGGERS`). It reads and writes no existing row; it takes milliseconds. The bytes are not in the
file: they live in `<stem>.artifacts.sqlite` beside it, which no checkpoint holds and a restore rebuilds for the copy.
Nothing writes to the tables unless the owner runs `nsf artifact add`. Software of an earlier schema refuses a
version-20 file; rollback is the owner's own copy taken before the upgrade (section 2, \"Rollback\").

**Version 21 (the outcomes of made forms, OMNIMIND lane L, ADR-078).** One table, `form_outcomes`, with two indexes and
three triggers (a row names a made artifact the file holds; a row is never rewritten; a row goes when its artifact's
row is deleted), IF NOT EXISTS throughout. It reads and writes no existing row; it takes milliseconds. Nothing writes
to it unless the owner sets `nsf config --form-learning record` (or `apply`) and gives a reaction, or a production
records a check verdict. Software of an earlier schema refuses a version-21 file; rollback is the owner's own copy taken before
the upgrade (section 2, \"Rollback\").

## 2. Upgrade procedure

**Read this first when upgrading from 0.7.0 (schema 13) or older.** From schema 14 an event that reaches its lifetime no longer disappears entirely: what a learning event taught is kept without limit as a consolidated reading, with its root name and sample id (ADR-044). This is on by default, also for an individual created by older software. To keep the old meaning of expiry, **stop every service first** (the console and the Pocket bridge expire due evidence whenever they are used, so a service that restarts after the package upgrade consolidates before you can opt out), then make `nsfs config --no-consolidation` the first command you run: it migrates the file and sets the switch without expiring anything. Only then start the services. If readings were kept before you opted out, list them with `nsfs memory consolidated` and forget them (RUNBOOK section 6).

1. Stop the services: `sudo systemctl stop neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.timer`.
   Then copy the individual's file and its artifact sidecar to a folder only you can read, e.g.
   `install -d -m 700 ~/nsf-before-upgrade && cp -p INDIVIDUAL.sqlite INDIVIDUAL.artifacts.sqlite ~/nsf-before-upgrade/`
   (the sidecar exists once a file was kept; with the services stopped, there is no `-wal` file to copy). This copy
   is the way back that the rollback below names.
2. Take and verify a checkpoint with the **current** software (the pre-upgrade checkpoint):
   ```sh
   nsfs checkpoint create --policy cold
   nsfs checkpoint verify ID
   ```
   Note ID and the current version (`/opt/neuroscifi/venv/bin/pip show neuroscifi`).
3. Keep a copy of the current software, such as the wheel you installed, so you can reinstall it.
4. Install the new software: `sudo bash deploy/install.sh --wheel NEW.whl` (or `--source DIR`). It reuses the venv and the env file and refreshes the unit files; read the diff of the units if you edited them. It does not start anything.
5. Open the instance once to migrate it, then check it: `nsfs birth`, `nsfs regulation`, `nsfs checkpoint list`. If you do not want consolidation, the command that opens it first must be `nsfs config --no-consolidation` (see the top of this section).
6. Take a new checkpoint with the new software and verify it. Start the services and watch the journal.

The Pocket database needs no step of its own (section 4).

**Rollback.** Migrations cannot be reversed, so rollback is restore, never downgrade-in-place:

1. Stop the services.
2. Reinstall the previous software (`install.sh --wheel OLD.whl`).
3. Put back the file as it was before the upgrade. There are exactly two ways, and `nsf checkpoint restore` is neither
   (corrected by OMNIMIND fix lane F1 after code review M1; pinned by `tests/instance/test_migrations.py`):
   - the owner's own copy of the individual's file and its sidecar, taken in step 1 of the upgrade above; or
   - `nsf archive unpack SOURCE DESTINATION` with the OLD software, run on the archive file of a checkpoint taken
     before the upgrade (see `nsf archive --help`).
   Why not `restore`: the old software refuses the upgraded live file ("newer than this software"), so it cannot read
   the checkpoint registry; and a restore made by the NEW software migrates the copy, so it yields the new schema,
   which the old software refuses too.
4. Either way brings back whatever was forgotten since that copy or checkpoint was taken: neither applies the
   tombstones of later forgets. Repeat from your records every forget request made since (RUNBOOK section 6), and
   delete the artifact sidecar (`<stem>.artifacts.sqlite`) of the new-schema file rather than reusing it.
5. Keep the new-schema file aside until the old system is confirmed good.

## 3. Importing a prototype database

The earlier prototype (`python -m neuroscifi.legacy_live`) kept epochs and observations in its own SQLite file. `nsf import-legacy` copies them into an individual. The prototype file is opened read-only and is never modified.

```sh
nsfs checkpoint create --policy cold          # protect the target first
nsfs import-legacy /path/to/prototype.sqlite
nsfs birth
```

- Epochs and observations are copied as observed, learnable evidence marked `promoted`, so imported history does not silently expire.
- It can be repeated: identical rows are skipped (`already_present`), ids that were forgotten stay forgotten (`forgotten_skipped`), and an id bound to a different observation stops with a conflict.
- The result reports the counts created. Beliefs are computed on read, so `nsfs status EPOCH` and `nsfs explain EPOCH` reflect the import immediately.
- Import is a write and is refused while writes are paused (RUNBOOK section 7).
- Make the file readable by the service user first (`neuroscifi` cannot read your home folder; copy it to a path it can read, then delete the copy).

## 4. The Pocket database

Pocket's own database (`pocket.sqlite`) has no `user_version`. Its tables are created idempotently every time the bridge starts (`CREATE TABLE IF NOT EXISTS`). The `muted` table (per-pairing "stop learning this pattern") was added after the first Pocket release. An older Pocket database is upgraded automatically at the first start of the new software: the table is created empty, nothing is lost and no operator step is needed. Older software ignores a table it does not know, so Pocket data can move back and forth, but a device's muted channels are then not honored by the older software.

Pocket data lives 24 hours. If in doubt, stop the service, move the file aside, start again, and re-pair the phones.

## 5. Where schema changes are recorded

New migrations append to `MIGRATIONS` in `schema.py`, with a test in `tests/instance`, and an entry in `docs/architecture/ARCHITECTURAL_DECISIONS.md` when the data format changes (ADR-003). Operators need nothing more than the steps above.
