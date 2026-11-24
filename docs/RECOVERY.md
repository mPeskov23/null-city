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
   Preserve `twofa.key` (or the `TWOFA_SECRET` value) too; see
   [the two-factor key](#two-factor-key-twofakey-and-recovery-codes).
3. Run the recovery drill above on the selected verified snapshot into a new path.
4. Move the old DB and matching sidecars out of the active location, keeping that
   incident copy. Put the verified restored DB at `DATABASE_PATH`, with the same
   owner and mode 0600. Never retain the old WAL beside the restored database.
   Keep the matching `twofa.key` in the same directory (mode 0600), or keep the same
   `TWOFA_SECRET`, so recovery codes issued before the incident still verify.
5. Restart, inspect `/api/health` and `/api/status`, sign in, load a known save and
   inspect match history. Confirm a new automatic snapshot completes.
6. If verification fails, stop again and restore the preserved original set;
   investigate using copies. Do not destroy the only remaining source.

The test suite verifies the recovery logic. A full deployment drill and external
storage policy must be exercised by the team on the actual evaluation host.

## Two-factor key (`twofa.key`) and recovery codes

Email 2FA stores only keyed digests (HMAC-SHA256) of challenge tokens, emailed codes and
recovery codes. The key is **not in the database**, so it is **not in any snapshot**.

Where it is:

- Docker: `/app/data/twofa.key` in the `city-data` volume, next to the database.
  `compose.yaml` does not pass `TWOFA_SECRET` to the container, so in Docker the file is the key.
- Native (`python3 scripts/serve.py`): `twofa.key` in the directory of `DATABASE_PATH`
  (`data/twofa.key` by default), or `TWOFA_SECRET` (at least 32 characters) from `.env`.
- It is created on first start (32 random bytes as hex, mode 0600). An empty, short or non-hex
  file makes the server refuse to start.

Back it up **together with every database backup**, but do not store it in the same place
as the snapshots. With the database alone the digests cannot be attacked; with both, a
recovery code (about 49 bits) could be brute-forced offline.

```sh
docker compose cp nullcity:/app/data/twofa.key ./twofa.key.backup   # then chmod 600 and move it off this machine
```

`docker compose down -v`, `make fclean` and `make re` delete `city-data` (accounts, snapshots
**and** the key); the automatic snapshots live in that same volume (`data/automatic-backups`), so
they are lost too. Copy a snapshot and the key off the volume first.

### What losing the key does

If `twofa.key` is missing (and `TWOFA_SECRET` is unset) the server **silently generates a new key**
at the next start; nothing in the logs warns about it.

| Item | Effect |
|---|---|
| Accounts, passwords, saves | unaffected |
| `users.twofa` flag | unchanged: accounts that had 2FA still require a code |
| Codes sent by email after the restart | work (they are issued with the new key) |
| **Recovery codes issued before** | **stop working**: their stored digests no longer match. They are not recomputable |
| Challenges pending at the time | invalid (they expire within 10 minutes anyway) |
| User who lost mailbox access too | locked out: there is no password reset and no in-app recovery |

Remedy for an affected user who can still receive email: sign in with the emailed code,
disable 2FA (password plus emailed code) and enable it again to obtain 8 new recovery codes.
Restoring the original `twofa.key` (or `TWOFA_SECRET`) makes the old recovery codes valid again.
Using the wrong key has the same effect as losing it.

### What an old snapshot does

A snapshot is the database as it was at that moment. Restoring it also restores the 2FA state of that moment:

- **Recovery codes already used since the snapshot become usable again** (`used` goes back to 0).
  Treat every recovery code as compromised if it was spent after the snapshot, and ask users to
  disable and re-enable 2FA.
- Accounts created after the snapshot, 2FA enabled after it, and codes issued after it disappear.
  A user who enabled 2FA after the snapshot signs in with password only until they enable it again.
- 2FA disabled after the snapshot comes back on, with the recovery codes of that time.
- Sessions revoked after the snapshot (for example when 2FA was enabled) reappear if they have not expired.
  Consider clearing the `sessions` table after a restore.

The key must match the snapshot's recovery codes: take the key that was current when the snapshot was
taken. The key never changes after creation, so any copy of the same file works.

### Not backed up

SMTP certificates and login (`smtp-pki`, `smtp-shared` volumes) are regenerated by the `smtp-init` job
and need no backup. Captured emails live in RAM in the `smtp` container and are lost on restart.

