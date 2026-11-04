# Health, automatic backups and disaster recovery

The HTTPS `/api/health` endpoint verifies database connectivity. `/api/status`
checks database integrity and reports scheduler health without exposing paths,
account data, credentials or backup downloads. `status.html` displays that state
and refreshes every 30 seconds. A failed/stale backup returns HTTP 503 from status;
health remains a liveness check and does not restart a functioning game simply
because its backup storage is full.

On every application start, a background worker immediately takes a snapshot.
It then repeats every `BACKUP_INTERVAL_SECONDS` (default 21600 / six hours).
`BACKUP_RETAIN` defaults to seven verified snapshots. Snapshot retention applies
only to generated `snapshot-*.sqlite3` files in the dedicated directory. The
worker uses SQLite's online backup API, so committed WAL data is included.
Each snapshot passes `integrity_check` and `foreign_key_check` before an atomic
rename from a temporary file. Files are created with mode 0600. Failures are
visible on the status page and the next scheduled attempt retries.

## Recovery objectives

- Default maximum interval of potential lost changes: six hours, assuming backups
  succeed. Check status and investigate failures; this is not a guaranteed SLA.
- Restore time depends on deployment, database size and operator access. The
  isolated automated drill checks consistency and preserved rows, not a promised
  production RTO.
- Copies live in `data/automatic-backups` (the persistent Docker volume). This
  protects against application data damage, **not loss of the whole disk/host**.
  The deployment owner must periodically copy a verified snapshot to independent,
  access-controlled storage. Do not publish backups in the web root.

## Non-destructive recovery drill

Choose an actual snapshot path and a **new** destination. The command refuses to
replace existing files, verifies source and restored DB integrity, checks foreign
keys and reports table row counts. It does not print private row contents.

```sh
python3 server/recovery.py data/automatic-backups/snapshot-EXACT-NAME.sqlite3 /tmp/nullcity-recovery-drill.sqlite3
```

In a container:

```sh
docker compose exec nullcity python3 server/recovery.py /app/data/automatic-backups/snapshot-EXACT-NAME.sqlite3 /tmp/recovery-drill.sqlite3
```

Use a fresh destination for each drill. Tests build a temporary WAL database,
restore accounts and saves, check relationships, reject corrupted backups and
prove that neither the live database nor a manual backup is overwritten.

## Restoring service after an incident

1. Stop the application (`docker compose stop` or Ctrl+C in the native server).
   Do not restore into a running SQLite database.
2. Preserve the current DB and its WAL/SHM sidecars in a private incident directory.
   Preserve TLS keys and configuration separately; DB snapshots do not contain them.
3. Run the recovery drill above on the selected verified snapshot into a new path.
4. Move the old DB and matching sidecars out of the active location, keeping that
   incident copy. Put the verified restored DB at `DATABASE_PATH`, with the same
   owner and mode 0600. Never retain the old WAL beside the restored database.
5. Restart, inspect `/api/health` and `/api/status`, sign in, load a known save and
   inspect match history. Confirm a new automatic snapshot completes.
6. If verification fails, stop again and restore the preserved original set;
   investigate using copies. Do not destroy the only remaining source.

The test suite verifies the recovery logic. A full deployment drill and external
storage policy must be exercised by the team on the actual evaluation host.
