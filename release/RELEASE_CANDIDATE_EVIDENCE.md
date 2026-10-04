# STIR 0.5.0-rc1 release candidate - evidence

Scope: governed-state effectivity boundary (AUD-012 constitutional vector), community bootstrap authority, JWT
identity binding, verifier credential isolation, V20/V21 database boundary, release packaging. Every item below was
executed on the DEV VM (`devstires`, SSH port 12522). Nothing ran on the Windows workstation. Production was not
accessed. Machine-readable identifiers are in `release/manifest.json`.

## 1. Status

RELEASE CANDIDATE READY FOR STIR.ES: **YES** (all pre-production gates green on the DEV VM).
STIR.ES RELEASE: **NOT DEPLOYED**. Production SSH is the only deployment blocker; see section 9.

## 2. End-to-end (isolated stack `stir-claude-mvp`, commit stir-main 9bb85f1)

Reference run `gate-RC-FINAL-20261004T183139Z`: `failed=0`, release topology restored healthy.

| Step | Result |
|---|---|
| smoke (as a disposable participant; platform admin cannot write listings, V17) | exit 0 |
| multitenant (live JDBC identities: ordinary runtime, verifier, audit verifier) | exit 0 |
| governed_boundary_e2e (direct verifier attacks + legitimate 7-of-7 mutation) | exit 0 |
| community_authority_e2e (disabled and enabled matrices, no fallback) | exit 0 |
| market_integrity_e2e (6-of-7 blocked, 7-of-7 activated, guardian suspension, recovery) | exit 0 |
| community_references_e2e, ordinary_governance_e2e, participant_independence_e2e | exit 0 |
| webauthn_hardware_custody_e2e, consent_retention_e2e, multi_source_value_evidence_e2e | exit 0 |
| economic_exchange_e2e, marketplace_e2e, community_value_governance_e2e (Playwright on the VM) | exit 0 |
| audit_economic_commit_e2e in the osTRIS fault topology (`deploy/compose.audit-fault.yml`) | exit 0 |

Total: 15 steps, 15 pass, 0 fail. Excluded and why:

- `backup_restore_e2e.py`: destructive by design (removes the stack's volumes, uses the base compose). Backup and
  restore were evidenced separately (section 6).
- `community_catalog_upgrade_e2e.py`, `community_catalog_browser_e2e.py`: depend on the optional community-catalog
  extension, which is not part of the release topology.
- The `*_browser*.py` UI smoke scripts are not in the release gate. The browser stack was exercised only through
  `community_value_governance_e2e.py`, which imports Playwright. Residual gap, stated here rather than hidden.

### Governed-boundary attack matrix (pinned statuses, from `governed_boundary_e2e.py`)

| Attack, sent directly to the verifier from the ordinary container | Status |
|---|---|
| forged actorId | 400 |
| valid JWT of a plain tenant user without bootstrap authority | 403 |
| administrator token on another tenant path | 403 |
| unsigned token (alg none) | 401 |
| JWT signed with an untrusted key | 401 |
| fabricated verification flags | 400 |
| six seats instead of seven | 400 |
| duplicate seat ordinal; replay of a completed bootstrap | 409 |
| exact 6-of-7 valid signatures cannot activate | 409 |
| duplicate signature by an already-signed seat | 409 |
| wrong community / wrong domain / modified payload (signature does not verify) | 400 |
| Guardian counted as seat 7; unknown credential for seat 7 | 409 |
| replay of the executed mutation; replay of its signature | 409 |
| stale sequence on a Guardian suspension | 409 |
| suspended credential of seat 4 cannot sign | 409 |

Legitimate 7-of-7 mutation after bootstrap: effective constitution changed exactly once; audit events recorded; the
audit chain verifies (`/api/.../audit` returns `valid: true`).

Caddy is routing only: the verifier rejected every request above on its own.

### Community authority matrix (`community_authority_e2e.py`, real `idax_core`)

Governance disabled: tenant owner allowed; tenant admin allowed; plain tenant user denied; readonly denied; service
denied; SuperAdmin without tenant membership denied; cross-tenant denied both directions; cross-community denied.

Governance enabled: active eligible member allowed (tenant role `user`, membership alone); tenant owner not in roster
denied; tenant admin not in roster denied (no fallback); SuperAdmin without roster membership denied; cross-community
denied.

Platform permission gates access only; it never conferred community authority in any of these runs.

## 3. Backend

`mvn -B verify` at stir-backend `63806d3` (DEV VM, `backend-verify-rc2-20261004T182536Z`):
**Tests run 263, Failures 0, Errors 0, Skipped 0, BUILD SUCCESS.** The count includes the Testcontainers PostgreSQL
classes, the LocalTokenValidator JWT contract tests, and the new `GovernedVerifierBeanExclusionsTest`.
The previous checkpoint of 262 is superseded; it is not a target.

## 4. Frontend (stir-frontend `84436aa`, `frontend-final-20261004T180712Z`)

- `npm test`: 45 pass, 0 fail. The test script was changed from a directory argument to a glob, because Node 22 no
  longer accepts a directory for `node --test`. Verified on Node 22.23 only; Node 20 was not run.
- `npm run build`: exit 0.
- `npm run i18n:validate`: exit 0.

## 5. Flyway upgrade matrix (`flyway_matrix.sh`, disposable containers, `flyway-final-20261004T180157Z`)

Same chain and Flyway version as the compose stack (core-migrations, then idax_shell, stir, idax_ledger, ostris;
Flyway 11.12.0).

| Stage | Result | Latest stir version | Row loss | Content changes | Grant changes | Ordinary role assumes verifier |
|---|---|---|---|---|---|---|
| empty -> latest | PASS | V21 | none | 0 | 0 | DENIED |
| V16 + realistic rows -> latest | PASS | V21 | none | 0 | 294 lines (expected) | DENIED |
| V19 + realistic rows -> latest | PASS | V21 | none | 0 | 56 lines (expected) | DENIED |

Realistic rows: every `stir` and `idax_core` table whose column list is identical at the staged version and now was copied
from the validated stack (122 tables at V16). Nothing was skipped as "changed"; the staged copy used
`--on-conflict-do-nothing` because core migrations already create reference rows.

Grant review. In the V16 and V19 stages, the only ordinary-role changes are revocations from V20:
`idax_app` (and therefore `idax_backend`) lost INSERT on nine tables: `market_constitution`, `constitutional_authority`,
`constitutional_seat`, `constitutional_credential_history`, `constitutional_proposal`, `constitutional_signature`,
`market_governance_event`, `constitutional_webauthn_credential`, `constitutional_webauthn_challenge`. V17 also removed
`idax_admin` DML. `stir_auditor` gained SELECT for the audit verifier's reads. No grant was added to an ordinary role.

Verifier role after V21: `LOGIN NOINHERIT NOBYPASSRLS NOSUPERUSER NOCREATEROLE`; no role is a member of it.

Migration defect found during the matrix: none in the migrations. Two evaluation-script defects were fixed in the
script (version ordering, boolean format), not in any migration. V20 and V21 were not edited.

## 6. Backup and restore rehearsal (`release/PRODUCTION_RUNBOOK.md` section 5)

- `pg_dump -Fc` of the validated stack database (V21, populated with constitutional, audit and observation rows):
  exit 0; `pg_dumpall --globals-only` captured alongside.
- Restored into a disposable PostgreSQL 17 container with `--no-owner --no-privileges` after the globals: **0 errors**.
- Comparison of governed and audit state (source vs restored): identical.
  constitution rows 48 (digest set hash), seats 175 (public key hash), governance events 686, constitutional signatures
  449, audit mutation events 5873, reference observations 248, stir Flyway version 21.
- The disposable container and its secret were removed afterwards.

MinIO object storage was not restored in this rehearsal; the checklist in the runbook keeps it as a separate step.

## 7. Audit V18

- `stir-audit-verifier` `/security-status`: `securityState CLEAR`, `openIncidentCount 0`; `/health` healthy.
- Verifier-written governed rows carry `session_user_name = idax_governed_verifier` and the application name of the
  JDBC driver. They are attributed by the database session, not by request JSON.
- No ordinary role (`idax_backend`, `idax_app`, `idax_admin`, `idax_governed_verifier`) holds schema USAGE or CREATE on
  `stir_audit`, nor INSERT, UPDATE or DELETE on `stir_audit.mutation_event` (checked with `has_schema_privilege` and
  `has_table_privilege`). TRUNCATE and SELECT were not separately checked for these roles.
- Limit, stated honestly: `request_id` and `correlation_id` are empty for verifier writes, so the request identifier is
  not stored in the audit row. What the audit row proves is which database role and client performed the write and
  which tables and entities changed. It does not prove human intent, and it is not an authorization record.

## 8. Infrastructure

**SILO (source-built): VERIFIED and ACTIVE.** Image `stir-claude/silo:RELEASE.2026-09-16T00-00-00Z-claude-1`, id
`63b86b5bc806`. `silo --version` reports commit `2a4d51406b7ed87af5fe6fe0f801f3290f96eb3c`. `/usr/bin/silo` sha256
`a9b692f5…353459`. It is the MinIO service in both the isolated stack and `stir-dev`. The image was built locally, so
there is no registry digest; the content id above is the release identity.

**XRPL (source-built xrpld 3.3.0): binary VERIFIED, NOT ACTIVE in the STIR release.**
Evidence: `idax/xrpld:3.3.0-source` (id `fb1005cb0856`), `xrpld version 3.3.0`, commit `00a178fb…`, binary sha256
`7f9ae7cf…810c8`. Architecture trace (documents and configuration):

- `ARCHITECTURE.md`: "Proof delivery and XRPL remain disabled pending service-principal/provider provisioning."
- Compose and vendor config: `OSTRIS_LEDGER_ENABLED: "false"`; ostris default `false`.
- `DEV_VM_SETUP.md`: the ledger is present for the topology; "STIR itself does not call it directly".
- `stir-doc/docs/network/STIR_NETWORK_ARCHITECTURE_V0.1.md`: a proposal ready for architectural review, not a
  deployment authorization. The genesis, keys and NetworkID of a community network are not designated.

Classification: CASE C (the current runtime does not consume XRPL). Activating it would require creating a community
network identity and validator design that is not yet decided. That is a true stop before any XRPL activation in
production. It does not block this release candidate, because the release does not depend on XRPL.

## 9. Stir-dev dress rehearsal

Inventory before: no `stir-dev` containers, volumes or networks (verified). `stir-silo-dev` was never touched; its 10
containers stayed up for the whole session.

A first attempt failed and is kept as evidence (`stir-dev-rebuild-20261004T181633Z`):
the governed verifier exited on first boot. Root cause: idax-core's boot-time writer (`ModulePermissionCatalogLifecycle`)
ran in the verifier, which holds no write privilege on `idax_permission`. The isolated stack had hidden this, because the
ordinary runtime had already written the catalog. Fixed in `stir-backend` `a0f43f4`, then corrected for
`@Bean` definitions without a class name in `63806d3` (a NullPointerException found by the isolated stack). The unit
test `GovernedVerifierBeanExclusionsTest` pins both. All failed resources were removed with project-scoped teardown
(zero stir-dev resources remained).

Final rehearsal `stir-dev-rebuild-final-20261004T183428Z`, from the release images, ports 28089 and 28096, fresh volumes:

- `docker compose up --wait` exit 0; all services healthy.
- Migrations from empty: stir V21, idax_core V91.
- Start order: governed-verifier 18:35:15, stir 18:35:50 (verifier first); verifier ERROR lines since boot: 0.
- Smoke (read-only): readiness 200; `/api/stir/instance` 200 with `stirVersion 0.5.0-rc1`; shell login 200; a governed
  write without token 401 from the verifier; audit `securityState CLEAR`; restart counts 0 for stir, verifier, shell,
  postgres, audit verifier and proxy.

## 10. Release images and manifest

Identities (local content ids, from the DEV VM) are in `release/manifest.json`. The runtime commit is stir-main `9bb85f1`,
stir-backend `63806d3`, stir-frontend `84436aa`. Later commits in stir-main only change documentation and release
metadata.

## 11. Security posture

- Constitutional vector (AUD-012, constitutional classes): closed for ordinary credentials. `idax_app` and `idax_backend`
  have no INSERT or UPDATE on the constitutional tables; only the verifier writes them, scoped to its tenant by RLS;
  the verifier and the ordinary runtime share no secret, no environment and no role membership.
- Residual TCB: the verifier database credential, the verifier container, the idax-core JWT validation library
  (binary jar), PostgreSQL, the WebAuthn attestation rules (format none, no trust chain) and the Guardian's key.
- AUD-012 overall: **MITIGATED — OPEN**. Verified on the database: `idax_backend` keeps INSERT on
  `market_integrity_case_event`, `reference_observation` and `ordinary_proposal` (non-constitutional governed classes
  still trust the ordinary runtime).
- AUD-007: **OPEN** (unchanged).
- PILOT READY: **NO**.

## 12. Known gaps stated for the record

1. Browser UI smoke suite (`*_browser*.py`) not in the release gate.
2. MinIO object-storage restore not rehearsed; only the database.
3. No registry digest; images are local content ids. Production must load the exact same images or rebuild from the
   pinned commits with a new evidence run.
4. `request_id` and `correlation_id` are not stored for verifier audit rows.
5. Production inventory, production host fingerprint and production secret presence are unknown (no SSH).
