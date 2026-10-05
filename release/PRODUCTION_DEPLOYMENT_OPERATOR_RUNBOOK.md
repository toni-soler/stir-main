# STIR production deployment — operator runbook (IDAX Ledger / private XRPL)

Status: **DRAFT — NOT EXECUTED.** Written from DEV evidence (see `deploy/idax-ledger/README.md`). Nothing in this
document has been run against production. Every production command below is for the operator to run on the host named in
its `RUN ON:` label. The author did not and must not run these commands.

Companion: `release/PRODUCTION_DEPLOYMENT_CHECKLIST.md` (tick-box version of section 14).

Conventions:
- `RUN ON: <label>` names the machine a step runs on. Labels: `PROD-STIR` (STIR production host), `PROD-XRPL-<n>` (one
  XRPL validator host, one per validator), `PROD-XRPL-API` (the non-validating API node host, Profile B only),
  `OPERATOR-WORKSTATION` (the operator's own machine; no secrets are stored here after transfer).
- Placeholders are written `<LIKE_THIS>`. Never replace a placeholder with a real value inside Git, chat or this file.
- Secret **values** never appear in this document. Only secret **names** and **file paths** do.
- If any step's expected result does not match, STOP and go to section 13 (stop conditions). Do not improvise a fix.

---

## 1. Scope and non-goals

In scope: the STIR release stack (`stir-release/*:rc` images, Flyway V21 for STIR, `idax_core` V91), the IDAX Ledger
service, and a private XRPL network that the Ledger uses to anchor **digests only** (never business data).

Out of scope: any public XRPL network (mainnet/testnet); any public peer; FFM code (not part of STIR); changes to
`stir-silo-dev`; any change to the DEV environment.

Anchoring rule: the chain receives a proof digest and a reference (`ostris:v1:<sha256>`). It never receives agreement
text, prices, participant identities or any other business field.

---

## 2. Decisions the operator must make BEFORE any step (record them in the checklist)

| # | Decision | Options | Default in this runbook |
|---|---|---|---|
| D1 | Topology profile | **A** single-host technical MVP (all XRPL nodes on `PROD-STIR`); **B** distributed (one validator per host) | Profile A for the first production activation only if the operator accepts that one host failure stops anchoring. Profile B is the target. |
| D2 | Genesis / network identity | Create a **new** private network (network id unique, never reused from DEV) | New network. The existence check in section 4 must pass first. |
| D3 | Validator independence | Who operates each validator; separate credentials, separate hosts | Must be documented. Profile A records a known single-operator risk. |
| D4 | Ledger retention | Keep all validated history (`ledger_history=full`) or prune | Keep full history for the MVP. See section 10 for measured growth. |
| D5 | Anchoring account funding | Amount of XRP reserved for AccountSet fees | Fund only from the production funding procedure in section 6. |

---

## 3. Preconditions (all must hold)

- [ ] Backup of the STIR production database taken and restore-tested (section 5).
- [ ] Release images transferred and their SHA-256 verified against the release manifest (section 12).
- [ ] The production `stir` stack is healthy on its **current** version and this runbook is the only change being made.
- [ ] No DEV secret is reused anywhere (genesis funding seed, anchor secret, validator keys, service keys, DB passwords).
- [ ] `OSTRIS_LEDGER_ENABLED` is `false` in production until section 9 passes.

---

## 4. Network identity check — MUST run first (STOP if an existing network is found)

`RUN ON: PROD-XRPL-<n>` for each validator host, and `PROD-XRPL-API` if Profile B.

Read-only. It queries the local node only; it does not contact any public peer.

```sh
# Replace <RPC_PORT> with the node's loopback RPC port (default 5005 inside the container).
curl -s -X POST -d '{"method":"server_info","params":[{}]}' http://127.0.0.1:<RPC_PORT> | head -c 2000
```

Expected for a new network: the connection is refused, or `server_info` shows no peers and `complete_ledgers` empty.

**STOP** if any node reports a `network_id` or validated ledger that already exists anywhere the operator controls. Do
not reuse an existing network identity. Record the finding and escalate to D2.

Also confirm no DEV node can reach production: DEV validators live on a separate subnet and separate host; the production
`xrpl-private` network must not be bridged to DEV networks.

---

## 5. Backup and rollback baseline

`RUN ON: PROD-STIR`

```sh
# Replace <PROJECT> with the production Compose project name and <BACKUP_DIR> with a path on a different disk.
docker compose -p <PROJECT> exec -T postgres pg_dump -Fc -U postgres idax > <BACKUP_DIR>/stir-idax-$(date -u +%Y%m%dT%H%M%SZ).dump
sha256sum <BACKUP_DIR>/*.dump > <BACKUP_DIR>/SHA256SUMS
```

Restore test (disposable container, never the live database):

```sh
docker run --rm -d --name restore-check -e POSTGRES_PASSWORD_FILE=/run/secrets/pw -v <BACKUP_DIR>:/backup postgres:17-alpine
# then pg_restore into the disposable container and check zero errors; remove the container afterward.
```

Record the backup's SHA-256 in the checklist. Rollback = the previous image set plus this backup.

---

## 6. Validator identity and funding (per host, on its own machine)

### 6.1 Generate validator secrets ON THE TARGET MACHINE

`RUN ON: PROD-XRPL-<n>`

Validator master keys are generated on the host that will run that validator. They are never generated on another host,
never copied into Git or chat, and never leave the host except as a **public** key.

```sh
mkdir -p <RUNTIME>/validator-0<n>/secrets && chmod 700 <RUNTIME>/validator-0<n>/secrets
docker run --rm --user "$(id -u):$(id -g)" -v <RUNTIME>/validator-0<n>/secrets:/keys \
  --entrypoint validator-keys <XRPLD_IMAGE> create_keys --keyfile /keys/validator-keys.json
```

Then the operator reads **only the public key** and sends it to the network coordinator:

```sh
python3 -c "import json;print(json.load(open('<RUNTIME>/validator-0<n>/secrets/validator-keys.json'))['public_key'])"
```

Do not print, copy or email the `secret` field.

### 6.2 Anchoring account and genesis funding

`RUN ON: OPERATOR-WORKSTATION` (offline). The anchor seed is generated and kept offline. Only the public address is
configured on `PROD-STIR`.

- Generate the anchoring wallet offline with a PRODUCTION-only tool, store its seed in a password manager or offline
  media, and record only the public address `<ANCHOR_ACCOUNT>`.
- Fund it only from the production funding source defined by the operator. The DEV genesis funding seed must never be used.
- The seed file on `PROD-STIR` is created with mode 600 and owned by the ledger runtime user, from the offline copy:

```sh
install -m 600 -o <LEDGER_UID> -g <LEDGER_GID> <OFFLINE_MEDIA>/anchor.seed <RUNTIME>/ledger/secrets/anchor.seed
```

---

## 7. XRPL network start (single pass, no per-node restarts)

`RUN ON: PROD-XRPL-<n>` on each validator host, then `PROD-XRPL-API` (Profile B).

1. Each host writes its own `xrpld.cfg` from the template with its own key material and the **agreed** list of
   validator public keys (`validators.txt`) and fixed peer addresses. Peers are configured by address on a fixed subnet,
   never by public address, and no public peers are listed.
2. Start once:

```sh
IDAX_LEDGER_RUNTIME_PATH=<RUNTIME> XRPLD_IMAGE=<XRPLD_IMAGE> \
  docker compose -p <XRPL_PROJECT> -f <REPO>/deploy/idax-ledger/compose.xrpl.yml up -d
```

3. Wait for convergence with the read-only report (no restart):

```sh
python3 <REPO>/deploy/idax-ledger/xrpl_health.py
```

Expected: validators `proposing`, at least 2 peers each, API node `full`, all validated ledger hashes identical.

**Do not restart nodes individually to force peering.** If convergence fails, go to section 13.

Keep `ledger_history=full` (D4). Do not run `docker compose down -v` on the XRPL project: it destroys the ledger.

---

## 8. Platform, IDAX Ledger and STIR start

### 8.1 Platform service principal and grant (canonical mechanism only)

`RUN ON: PROD-STIR`

Create the principal and the grant **only** through the canonical platform functions
(`service_principal_create`, `service_principal_create_grant`) inside one transaction that sets the tenant context.
Never insert into `service_principal*` tables directly.

Required grant, exactly: principal `ostris-ledger-delivery`, tenant `<PRODUCTION_TENANT_SLUG>`, audience `idax-ledger`,
permissions `LEDGER_PROOF_CREATE`, `LEDGER_READ`, `LEDGER_PROOF_VERIFY`.

Forbidden in any grant: admin, tenant-wide wildcards, governance capabilities, role management, SuperAdmin, and write
capabilities unrelated to proof delivery and verification.

The credential secret is generated on the host, stored under `<RUNTIME>/secrets/ostris_ledger_client_secret` (mode 600),
and only its BCrypt hash is stored in the platform database. Never record the value.

### 8.2 Ledger tenant mirror (referential only)

`RUN ON: PROD-STIR`, script `deploy/idax-ledger/mirror_platform_tenant.sh`.

The mirror copies only: `tenant_id`, `code`, `name`, `status`, `mfa_required`, timestamps. It does not copy users,
memberships, roles, Seven Keys state, SuperAdmin state, secrets or credentials. The mirror is routing information, not an
authorization source. If the platform row and mirror disagree, the platform row wins; the mirror is never deleted
automatically.

### 8.3 Start order

`RUN ON: PROD-STIR`

```sh
docker compose -p <STIR_PROJECT> -f compose.yml -f compose.release.yml -f <REPO>/deploy/idax-ledger/compose.stir-ledger.yml config --quiet
docker compose -p <STIR_PROJECT> ... up -d
```

Run `config --quiet` before every recreate. Start the ledger stack from `deploy/idax-ledger/compose.ledger.yml` after the
XRPL network is healthy. The ledger's `IDAX_LEDGER_ANCHOR_SEED_FILE` must point at the mode-600 seed from section 6.2.

Set the STIR-side ledger variable only in the override, and keep it `false` until section 9:

```sh
OSTRIS_LEDGER_ENABLED=false   # stays false here
```

---

## 9. First ledger proof (controlled transaction)

Enable delivery only after the service token path is confirmed. `RUN ON: PROD-STIR`.

1. Confirm the service-token endpoint answers the ostris principal with a valid token (a token request must succeed; do
   not paste the token anywhere).
2. Set `OSTRIS_LEDGER_ENABLED=true` in the override and recreate **only** the `ostris` service.
3. Run one controlled, real, signed transaction through the product path (the same flow as
   `scripts/ledger_integration_e2e.py`, run against a production-approved test tenant, not a real business tenant unless
   the operator explicitly approves it).
4. Verify on the database (read-only queries):
   - the `ostris.protocol_proof_outbox` row for the commit is `ANCHORED` (the success state in `ProtocolProofOutboxObservability`);
   - the proof digest matches the journal commit;
   - the XRPL transaction is validated (`tesSUCCESS`, validated ledger index recorded).
5. Record the STIR agreement id, osTRIS transaction id, outbox id, XRPL transaction hash and validated ledger index.

**Acceptance:** all five records present; the anchored memo carries only the digest reference.

---

## 10. Retention and capacity (measured on DEV, must be re-measured on production)

Measured on DEV on 2026-10-05 (single 60-second sample, validator data directory and ledger database):

- Validated ledger progression: about 20 ledgers per minute at the DEV idle baseline (seq 2302 → 2322 in 60 s).
- Node data directory: about 22 MB per validator after the cold-restart test; ledger SQLite WAL about 2 MB.
- Ledger PostgreSQL database: about 12 MB.
- Host disk: 95 GB volume, 41 GB free on DEV.

These numbers are a starting point only. Production must measure growth for at least 24 hours under real load and size
the disk for the agreed retention window (D4) plus 2× headroom. Do not extrapolate the 60-second sample.

Alert thresholds to configure before activation (operator-defined): disk free below 20%; validator data growth above the
measured 24-hour projection; ledger outbox `FAILED_RETRYABLE` count growing for more than 15 minutes.

---

## 11. Rollback

- Disable delivery first: set `OSTRIS_LEDGER_ENABLED=false` and recreate only `ostris`. Committed economic state is not
  affected: the osTRIS commit does not depend on delivery succeeding.
- Stop the ledger service; do not stop the XRPL validators unless they are misbehaving.
- Revert the images to the previous release set and restore the backup from section 5 only if a database migration must
  be reversed (Flyway migrations are forward-only; do not edit applied migrations).

---

## 12. Transfer of artifacts (SHA-256 after transfer)

`RUN ON: OPERATOR-WORKSTATION` and each target host.

- Transfer each `docker save` archive and the manifest.
- Verify before loading:

```sh
sha256sum -c <RELEASE_DIR>/SHA256SUMS
```

- The image archive **must not** contain: validator private keys, node private keys, the anchoring seed, JWT signing
  private material, database passwords or client secrets. Confirm with the release checker before transfer.
- Load only after the checksum passes.

---

## 13. Stop conditions

Stop and escalate (do not improvise) if any of these occur:

- section 4 finds an existing network identity;
- validators do not converge after the single start in section 7;
- a token request is refused with an unexpected status after the grant in section 8.1 was applied;
- a delivery returns a permanent failure for a valid proof (the outbox row is terminal and needs a reviewed replay path);
- the ledger database shows any row outside the expected tenant;
- disk free falls below the section 10 threshold during activation.

---

## 14. Post-activation checks

`RUN ON: PROD-STIR`

- STIR browser smoke: run the release smoke against the production public URL with a test tenant.
- Audit health: the audit verifier `/security-status` reports `CLEAR`, `openIncidentCount` 0.
- Ledger readiness: `wget -qO- http://localhost:8094/actuator/health/readiness` returns UP (inside the host network).
- XRPL: `python3 xrpl_health.py` reports HEALTHY.

Record every result in the checklist. Do not declare the release live until all four pass.

---

## 15. Known open items (carry forward, do not hide)

- AUD-012 (runtime credential can write governed state): MITIGATED — DETECTED ONLY, OPEN. Not closed by this release.
- AUD-007: OPEN.
- Permanent outbox rows (`FAILED_PERMANENT`) are terminal in the delivery store; there is no reviewed replay path yet.
  A token-endpoint failure that is classified as permanent will not retry automatically after the cause is fixed.
- Genesis, validator independence and retention (D2–D4) are operator decisions, not decided by this document.
- Production has not been started. This release is not deployed to stir.es.
