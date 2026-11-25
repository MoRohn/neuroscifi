# Schema versions, upgrades and imports

## 1. How the instance database is versioned

The instance database (one SQLite file per individual) stores its schema version in `PRAGMA user_version`. The software holds an append-only list of migrations in `src/neuroscifi/instance/schema.py`; the current schema version is the length of that list (17 in this release; 13 in 0.7.0).

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
reading of the epoch by sample on every read. Software of an earlier schema refuses a version-16 file; rollback is a restore
of the pre-upgrade checkpoint.

**Version 17 (the imagined store, ADR-049).** It adds three empty tables: `imagined` (content the system produced
itself that is not evidence: simulations, speculations, model-written inferences), `imagined_refs` (what each record
was derived from) and `imagined_forgotten` (payload-free tombstones), with their indexes and triggers, and on eleven
evidence-side tables (evidence, consolidated readings, belief revisions, web sources, claims, claim links, knowledge,
hypotheses, predictions, feedback, calibration snapshots) a pair of triggers that refuse an id of the form `imagined:`
plus 32 hex digits in a reference column. No existing row is read or written; the migration takes milliseconds. The
store stays empty until a mechanism writes to it; nothing does in this release. Two settings come with it,
`max_imagined_rows` (1000) and `imagined_lifetime_seconds` (7 days), changeable with `nsf config
--max-imagined-rows N` and `--imagined-lifetime-days D`; a file saved by older software reads them as these
defaults. `nsf imagined list` shows what is held. Software of an earlier schema refuses a version-17 file; rollback is
a restore of the pre-upgrade checkpoint.

## 2. Upgrade procedure

**Read this first when upgrading from 0.7.0 (schema 13) or older.** From schema 14 an event that reaches its lifetime no longer disappears entirely: what a learning event taught is kept without limit as a consolidated reading, with its root name and sample id (ADR-044). This is on by default, also for an individual created by older software. To keep the old meaning of expiry, **stop every service first** (the console and the Pocket bridge expire due evidence whenever they are used, so a service that restarts after the package upgrade consolidates before you can opt out), then make `nsfs config --no-consolidation` the first command you run: it migrates the file and sets the switch without expiring anything. Only then start the services. If readings were kept before you opted out, list them with `nsfs memory consolidated` and forget them (RUNBOOK section 6).

1. Stop the services: `sudo systemctl stop neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.timer`.
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
3. Restore the pre-upgrade checkpoint to a new file and swap it in, as in RUNBOOK section 5. The checkpoint holds the old schema version, so the old software opens it.
4. Anything recorded after the checkpoint is lost. A restore re-applies only the tombstones held by the database it runs against, so repeat from your records any forget requests made since the checkpoint (RUNBOOK section 6).
5. Keep the new-schema file aside until the old system is confirmed good.

If `nsf` refuses to open the live database because it is newer than the old software, `restore` cannot read its registry. Use `nsf archive unpack SOURCE DESTINATION` on the archive file instead (see `nsf archive --help`); it does not apply tombstones, so repeat any forgets since.

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
