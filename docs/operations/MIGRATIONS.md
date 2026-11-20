# Schema versions, upgrades and imports

## 1. How the instance database is versioned

The instance database (one SQLite file per individual) stores its schema version in `PRAGMA user_version`. The software holds an append-only list of migrations in `src/neuroscifi/instance/schema.py`; the current schema version is the length of that list (1 in this release).

- **Forward only.** Opening an instance with `nsf` applies every pending migration in order, each in one transaction that also sets `user_version`. There are no down-migrations. A released migration is never edited; changes arrive as a new migration.
- **Newer than the software is refused.** If `user_version` is greater than the software's version, the command stops with "Instance schema N is newer than this software (M)" and changes nothing. Do not run older software on a database an upgrade has opened.
- Because any `nsf` command that opens the database can migrate it, the first command after installing new software is the upgrade. Plan it; do not let the nightly timer or the console be the first thing to touch it.

Read the version with `sqlite3 /var/lib/neuroscifi/instance.sqlite 'PRAGMA user_version'` if the sqlite3 tool is installed *(not part of the project)*.

## 2. Upgrade procedure

1. Stop the services: `sudo systemctl stop neuroscifi-pocket.service neuroscifi-console.service neuroscifi-checkpoint.timer`.
2. Take and verify a checkpoint with the **current** software (the pre-upgrade checkpoint):
   ```sh
   nsfs checkpoint create --policy cold
   nsfs checkpoint verify ID
   ```
   Note ID and the current version (`/opt/neuroscifi/venv/bin/pip show neuroscifi`).
3. Keep a copy of the current software, such as the wheel you installed, so you can reinstall it.
4. Install the new software: `sudo bash deploy/install.sh --wheel NEW.whl` (or `--source DIR`). It reuses the venv and the env file and refreshes the unit files; read the diff of the units if you edited them. It does not start anything.
5. Open the instance once to migrate it, then check it: `nsfs birth`, `nsfs regulation`, `nsfs checkpoint list`.
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
