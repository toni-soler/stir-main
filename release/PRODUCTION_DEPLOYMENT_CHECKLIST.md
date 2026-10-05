# STIR production deployment — checklist

Companion to `PRODUCTION_DEPLOYMENT_OPERATOR_RUNBOOK.md`. Tick a box only after the step has been run and its expected
result observed. Record the evidence (timestamp, SHA-256, identifier) next to the box. Never record secret values.

Status: **NOT STARTED.** Claude has not run any step in this checklist on production.

Companion runbook is **READY FOR OPERATOR EXECUTION** (see its status line for what that does and does not mean).

| Field | Value |
|---|---|
| Operator | `<OPERATOR_ROLE>` |
| Profile (A / B) | `<PROFILE>` |
| Release id | `<RELEASE_ID>` |
| Manifest SHA-256 | `<MANIFEST_SHA256>` |

## 1. Inventory
- [ ] Production STIR version, image ids and Compose project recorded (`RUN ON: PROD-STIR`).
- [ ] Host disk free, memory and CPU recorded for every host in the profile.
- [ ] CP1 (runbook section 2): independent hosts counted -> Profile `<A/B>` recorded, with reason if not >=3 hosts.
- [ ] CP3: validator host/operator table filled in; no two rows share an operator or host under Profile B.

## 2. Backup
- [ ] `pg_dump -Fc` of the production `idax` database taken to a disk other than the database disk.
- [ ] Backup SHA-256 recorded; `SHA256SUMS` written.
- [ ] Restore into a disposable container completed with zero errors; disposable container removed.

## 3. XRPL network identity
- [ ] `server_info` on every node checked; no existing network identity found (runbook section 4). **STOP if found.**
- [ ] Network id `<NETWORK_ID>` agreed and different from DEV `2181844733`.
- [ ] No bridge or route between production XRPL and any DEV network.

## 4. Validator identity (per validator host, on the target machine)
- [ ] Validator 01 host created its keys locally; only the public key `<VALIDATOR_01_PUBLIC_KEY>` was shared.
- [ ] Validator 02 host created its keys locally; only the public key `<VALIDATOR_02_PUBLIC_KEY>` was shared.
- [ ] Validator 03 host created its keys locally; only the public key `<VALIDATOR_03_PUBLIC_KEY>` was shared.
- [ ] Each validator's secret stays on its own host (mode 600 / directory 700). None copied into Git, chat or this checklist.
- [ ] The agreed `validators.txt` lists exactly the three public keys above, identical on every node.

## 5. Consensus start
- [ ] XRPL started once with the Compose command in runbook section 7 (no per-node restarts).
- [ ] `xrpl_health.py` reports HEALTHY: validators `proposing`, at least 2 peers each, API node `full`.
- [ ] Validated ledger hashes identical across all nodes.

## 6. Anchoring account
- [ ] Anchor address `<ANCHOR_ACCOUNT>` recorded; seed generated offline; seed never on a networked host.
- [ ] Seed file on `PROD-STIR` is mode 600 and owned by the ledger runtime user.
- [ ] Funding came from the production funding source; no DEV seed was used.

## 7. Transfer and integrity
- [ ] Every archive transferred; `sha256sum -c` passed on each target host after transfer.
- [ ] Image archives contain no validator keys, node keys, anchor seed, JWT private material, DB passwords or client secrets.

## 8. IDAX Ledger readiness
- [ ] Ledger Flyway migrations (`idax_ledger`) and core migrations completed, including the outbox replay migration.
- [ ] Platform principal `ostris-ledger-delivery` created through the canonical function only.
- [ ] Ledger readiness endpoint UP.
- [ ] A token request for the ostris principal, for an enabled tenant (section 8.3), succeeds.

### 8a. Per-tenant ledger enablement (repeat this block for every tenant to enable - runbook section 8.3)
- [ ] Tenant `<TENANT_CODE>` / `<TENANT_ID>` confirmed as a real platform tenant via `/api/shell/v1/platform/tenants`.
- [ ] Ledger tenant mirror written for `<TENANT_ID>` (referential fields only - no users/memberships/roles/secrets).
- [ ] Grant created via `idax_core.service_principal_create_grant` only: exactly `LEDGER_PROOF_CREATE`, `LEDGER_READ`,
      `LEDGER_PROOF_VERIFY` on audience `idax-ledger`; no admin, wildcard, all-tenants, governance, role or SuperAdmin capability.
- [ ] Grant verified by query to carry exactly the intended tenant_id and permissions.
- [ ] One safe proof test for this tenant reached `ANCHORED` (identifiers recorded in section 10 below).
- [ ] A different, real, existing tenant with NO grant was confirmed still denied (same uniform 401) before declaring
      this tenant's isolation correct.

## 9. Migrations and STIR readiness
- [ ] STIR Flyway at V21; `idax_core` at V91.
- [ ] `OSTRIS_LEDGER_ENABLED=false` confirmed before activation.
- [ ] STIR services healthy; `docker compose ... config --quiet` passed before the last recreate.

## 10. First ledger proof
- [ ] One controlled, real, signed transaction completed through the product path (approved test tenant).
- [ ] Outbox row `ANCHORED`; proof digest matches the journal commit.
- [ ] XRPL transaction validated; validated ledger index recorded.
- [ ] Recorded: agreement id `<AGREEMENT_ID>`, osTRIS transaction `<OSTRIS_TX_ID>`, outbox `<OUTBOX_ID>`, XRPL tx `<XRPL_TX_HASH>`, ledger `<LEDGER_INDEX>`.
- [ ] Anchored memo contains only the digest reference, no business data.

## 11. Browser smoke
- [ ] Release browser smoke passed against the production public URL with a test tenant.

## 12. Audit health
- [ ] Audit verifier `/security-status`: `securityState` CLEAR, `openIncidentCount` 0.

## 13. Monitoring and retention
- [ ] Alerts configured: disk free below 20%; validator growth above 24-hour projection; `FAILED_RETRYABLE` growing for more than 15 minutes.
- [ ] CP4: 24-hour growth measured on production; `retention_days_possible` computed; >= 30 or explicit operator sign-off recorded.

## 14. Sign-off
- [ ] Open items carried forward: AUD-012 MITIGATED — DETECTED ONLY, OPEN; AUD-007 OPEN.
- [ ] `FAILED_PERMANENT` rows for tenants deliberately NOT ledger-enabled are left as-is (expected, not an incident).
- [ ] Production activation decision recorded by `<OPERATOR_ROLE>` on `<DATE>`.
