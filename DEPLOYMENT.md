# STIR deployment operations

Single-host Docker Compose. One operator, few commands - you should not need to know any
module's internals to run these. All commands run from this directory (`stir-main`).

## Initial deploy (production)

1. Provision a host reachable at the domain you'll use, with Docker + Docker Compose installed,
   and that domain's DNS A/AAAA record already pointing at it (see DNS section below).
2. `python scripts/initialize.py` - pins public sources, generates local secrets (once; never
   regenerates an existing secret).
3. `mkdir -p .local && touch .local/production` - the marker every other script in this directory
   checks before touching the production compose project or its volumes.
4. Create `.env` in this directory (never commit it) with at minimum:
   ```
   STIR_PUBLIC_HOSTNAME=stir.es
   STIR_PUBLIC_BASE_URL=https://stir.es
   STIR_SITE_NAME=STIR
   STIR_TENANT_CODE=stir
   STIR_ADMIN_EMAIL=you@yourdomain.example
   STIR_SUPPORT_CONTACT=you@yourdomain.example
   ```
5. `docker compose -f compose.yml -f compose.production.yml up -d`
6. Wait for health: `python scripts/status.py` until every service reads `OK`.
7. `python scripts/smoke.py` - confirms the deployment actually works end to end.
8. Set up the backup cron job (see Backup below).

The FIRST admin account (email/tenant from `.env` above) is provisioned automatically on first
boot - this is "bootstrap instance", not "create a normal user". Every subsequent real user is
created by that admin through the Shell's own user-management screen (or
`POST /api/shell/v1/tenants/{tenant}/users`), which is "create normal user". STIR 0.5 ships no
public self-registration form - see stir-doc/VALIDATION.md's 0.5 section for why, and treat the
first public beta as invitation-only: don't publish the login URL more widely than you intend to
actually admit people.

## Status

`python scripts/status.py` - per-service health, disk space, Postgres/object-storage
reachability, backup count. Read-only.

## Update to a new version

`python scripts/deploy.py --prod` (or no `--prod` for the dev stack) - builds, recreates
services, waits for health, runs a non-destructive smoke check, and automatically rolls back to
the previous images if either check fails. Never touches volumes.

## Backup

`python scripts/backup.py` - Postgres dump (all schemas) + object storage archive, timestamped
under `.local/backups/`, retaining the last 14 runs by default (`--keep N` to change). Cron
example (daily at 03:15):
```
15 3 * * * cd /path/to/stir-main && python3 scripts/backup.py >> .local/backups/backup.log 2>&1
```
Private browser signing keys are never part of any backup - they never leave the browser.

## Restore

`python scripts/restore.py .local/backups/<timestamp>` - **destructive**, replaces all current
data. In production it requires typing the compose project name (`stir-prod`) to confirm; `--yes`
alone is not honored there, so a copy-pasted dev command can never silently wipe live data.

## Logs

`docker compose -f compose.yml -f compose.production.yml logs -f <service>` for one service, or
omit `<service>` for all. Docker's own log rotation is configured per-service (see compose.yml) so
logs never grow unbounded on disk.

## Rollback

Automatic as part of `deploy.py` on a failed health/smoke check. To roll back manually to
whatever `deploy.py` last tagged as `previous`:
```
for s in shell stir stir-ui ostris ostris-ui ledger ledger-ui; do
  docker tag stir-rollback/$s:previous stir-prod-$s
done
docker compose -f compose.yml -f compose.production.yml up -d --no-build
```

## Disaster test

Before trusting this in production, `python scripts/backup_restore_e2e.py` against the
development stack proves the actual round trip (create data+photo -> backup -> destroy both
volumes -> restore -> verify byte-identical). See stir-doc/VALIDATION.md for the last executed
run's evidence. Re-run it if the volume/storage architecture ever changes.
