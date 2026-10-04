# STIR production deployment runbook (release candidate package)

Status of this package: **prepared, not deployed**. Production SSH to stir.es is not available to this
work, so nothing in this document has been executed against production. Every production-owned value
is marked `UNKNOWN` and must be read from the live host by its operator before use. No secret value is
recorded here; only secret names and locations.

## 1. Preconditions (all must hold before any production change)

1. `release/manifest.json` is the release being deployed; its commits and image digests match what was
   validated on the DEV VM (`release/RELEASE_CANDIDATE_EVIDENCE.md`).
2. The production SSH host key fingerprint has been verified through an authoritative independent source
   (not `accept-new` alone). Record the source and the fingerprint in the change ticket.
3. A current, restorable backup of the production PostgreSQL and object-storage volumes exists and a
   restore has been rehearsed on a disposable project (see section 5).
4. The operator has the production inventory the manifest marks `UNKNOWN` (section 6).

## 2. Artifacts

| Artifact | Source | Identity field in `manifest.json` |
|---|---|---|
| STIR backend (ordinary runtime) | `stir-backend` commit | image id / digest |
| STIR verifier (governed writes) | same backend commit, profile `governed-verifier` | image id / digest |
| STIR frontend | `stir-frontend` commit | image id / digest |
| Shell, ostris, ledger, UIs | vendor pins in `stir-main/upstream.lock.json` | image ids |
| SILO object storage | source-built `stir-claude/silo:RELEASE.2026-09-16T00-00-00Z-claude-1` | image id, `/usr/bin/silo` sha256 |
| XRPL node | `idax/xrpld:3.3.0-source` | **not part of the release topology** (see section 7) |
| Flyway migrations | `stir-backend` `db/migration-stir` through V21 | last version |

Build once on the DEV VM, promote unchanged. Production must pull or load the exact image ids listed in
the manifest and set `pull_policy: never` or equivalent, so a rebuild can never substitute code.

## 3. Configuration and secrets (names only)

Environment variables consumed by `compose.yml` / `compose.release.yml` (values are operator-owned):

- `STIR_HTTP_PORT`, `STIR_API_PORT` (loopback ports; production uses its own edge)
- `STIR_PUBLIC_BASE_URL`, `STIR_SITE_NAME`
- `OSTRIS_LEDGER_ENABLED` (must stay `false` for this release)

Docker secrets (files under the operator's secrets directory, never in git):

- `postgres_password` (migrator/superuser, used only by the migration and provision services)
- `runtime_password` (idax_backend, ordinary runtime)
- `governed_verifier_password` (idax_governed_verifier, mounted only into `governed-verifier`)
- `stir_auditor_password` (audit verifier, mounted only into `stir-audit-verifier`)
- `jwt_public_key` (identity verification, mounted into the runtime and the verifier)
- `login_password` and the MinIO credentials, as defined by `scripts/initialize.py`

The ordinary `stir` container must not receive `governed_verifier_password` or any `governed` environment
variable. The release gate asserts this (`scripts/governed_boundary_e2e.py`).

## 4. Deployment sequence

1. Freeze writes to STIR (maintenance window) and take the backup in section 5.
2. Load or pull the manifest image ids; verify each id matches `manifest.json`.
3. Run the one-shot services in order: `core-migrations`, `module-migrations` (Flyway, latest V21),
   `runtime-provision`, `audit-provision`, `governed-verifier-provision`.
4. Start `postgres`, `minio`, `shell`, `stir`, `governed-verifier`, `stir-audit-verifier`, `ostris`,
   `ledger`, the UIs and `proxy`. Wait for `healthy` on each.
5. Run the post-deployment smoke (section 8).
6. Keep the previous release's images and volumes until the smoke and one business day of observation pass.

## 5. Backup and rollback

Backup checklist (before step 1 above):

- `pg_dump` of database `idax` (custom format) and a `pg_dumpall --globals-only` file;
- object-storage bucket snapshot or `mc mirror` of the MinIO bucket;
- copy of the secrets directory, stored outside the host;
- record of every running image id and the current Flyway history (`flyway_schema_history*` tables).

Restore rehearsal: `scripts/backup_restore_e2e.py` is destructive to its target stack and is therefore
run only on a disposable Compose project, never on a validated or production stack. Its result is
recorded in `release/RELEASE_CANDIDATE_EVIDENCE.md`.

Rollback (if the smoke fails):

1. Stop the new services; restart the previous image ids from the rollback record.
2. Flyway migrations V1 to V21 are **forward-only**. Do not run undo scripts. If the database must return to
   the prior schema, restore the pre-deployment `pg_dump` into a new database and repoint the runtime, after
   verifying the restored history matches the rollback record.
3. Confirm `GET /api/stir/instance` reports the previous version and that `stir-audit-verifier` reports
   `securityState=CLEAR` before reopening writes.

## 6. Production values marked UNKNOWN

| Item | Value |
|---|---|
| Current production release, image ids | UNKNOWN |
| Current production Flyway version | UNKNOWN (DEV VM at V16 before this work; the RC requires V21) |
| Production host fingerprint (independent source) | UNKNOWN |
| Production backup location and retention | UNKNOWN |
| Edge/TLS configuration on stir.es | UNKNOWN |
| Production secrets presence | UNKNOWN (never requested in chat) |

## 7. XRPL

XRPL is not part of this release topology. The runtime does not connect to a node (`OSTRIS_LEDGER_ENABLED=false`).
A source-built `xrpld` 3.3.0 binary is verified on the DEV VM, but activating XRPL requires a network identity,
genesis and validator design that the architecture proposal (`stir-doc/docs/network/`) marks as not yet decided.
That decision is a stop item before any production XRPL activation.

## 8. Post-deployment smoke

Run from a host with loopback access to the proxy, against the production URL only after step 4:

```
curl -fsS http://127.0.0.1:<proxy-port>/actuator/health/readiness
curl -fsS http://127.0.0.1:<proxy-port>/api/stir/instance
docker exec <stir-audit-verifier> wget -qO- http://127.0.0.1:9090/security-status
```

Expected: readiness `UP`; instance version matches the manifest; audit `securityState` is `CLEAR` with
`openIncidentCount` 0. The full E2E battery is not run against production (it creates test tenants). Use only
these read-only checks.

## 9. Health-check commands

`docker compose ps` must show `healthy` for `postgres`, `stir`, `governed-verifier`, `stir-audit-verifier` and
`proxy`. Restart counts must stay at zero for at least the observation window.
