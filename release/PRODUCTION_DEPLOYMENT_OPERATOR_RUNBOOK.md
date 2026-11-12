# STIR production deployment — operator runbook (IDAX Ledger / private XRPL)

Status: **NOT READY - see section 16.** Decided and verified on DEV on 2026-11-12: five validators (quorum four), pruned
validators with one full-history node that IDAX Ledger reads, seven-day logs, and a start command that resumes
validation after uneven stops. Still open before production: a second copy of the full history, the start procedure
when validators run on separate hosts, and the fact that this runbook has never been rehearsed end to end. Nothing in
this document has been run against production - the author did not and must not run these commands; every production
command below is for the operator to run on the host named in its `RUN ON:` label.

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

Ledger enablement is per tenant, explicit, never automatic (section 8.3). A tenant with no grant stays denied — that is
the correct, expected state for a tenant that was never meant to be ledger-enabled, not an incident.

---

## 2. Deployment-time checkpoints (resolved into checks/formulas, not open decisions)

Each former open decision is now a checkpoint with a deterministic answer. The only inputs left are environment facts
(how many hosts exist, what they measure) — never an architectural choice made up on deployment day.

**CP1 - Topology profile.** The network has FIVE validators and one non-validating full-history node (decided
2026-11-12). xrpld needs 80% of the trusted validators: five give a quorum of four, so one validator can fail or be
rebuilt while anchoring continues; three would give a quorum of three and no tolerance.
```
CHECK: how many hosts, each under independent operational control, are available to run validators?
  1 host   -> Profile A (all six nodes on one host). One host failure stops anchoring; one validator process
              failing does not. Acceptable ONLY for the first production activation.
  >= 5     -> Profile B (one validator per host, history node on its own host or with one validator).
  2 to 4   -> STOP and decide placement explicitly: no host may run two or more validators unless the operator
              accepts that losing that host stops anchoring (two of five down = no quorum).
```
Verified on DEV: one of five stopped, the validated ledger kept advancing; two stopped, it halted; both started again
with a plain `docker start`, it resumed with no other action.

Negative UNL: a validator that stays down for long is disabled by the network itself and its validations stop
counting until the network re-enables it at a later flag ledger (every 256 ledgers, about 15 minutes each). During that
window the tolerance is already spent: stopping a second validator halts validation. Check
`validators` -> `NegativeUNL` is empty before any planned validator stop.

**CP2 — Genesis / network identity.** Always create a **new** private network; never reuse a DEV network id. Resolved
by the mandatory check in section 4 — there is nothing left to decide, only to run and read the result.

**CP3 — Validator independence.** Not a decision: a record. Before section 6, fill in this table (hostname/operator per
validator) in the checklist. If any two rows share an operator or host, that is CP1's single-host case, not a silent
Profile B.

**CP4 — Ledger retention.**
```
CHECK (before activation, with the per-node figures of section 10; re-check after 24h of real load):
  measured_daily_growth_bytes = sum over the nodes on this disk of (ledger data per day + log budget / KEEP_DAYS)
  retention_days_possible = (disk_free_bytes * 0.5) / (measured_daily_growth_bytes)
  IF retention_days_possible < 30:
      STOP. Either add disk before activation, or get explicit operator sign-off to accept a shorter
      guaranteed-retention window than 30 days. Do not silently prune history to make the number look fine.
  ELSE:
      keep ledger_history=full (the DEV default). No pruning configuration is defined by this runbook.
```

**CP5 — Anchoring account funding.** Measured live on the DEV network (`server_state`): `reserve_base` = 10 XRP,
`reserve_inc` = 2 XRP, `base_fee` = 10 drops (0.00001 XRP) — this private network kept XRPL's standard mainnet-like
reserve/fee economics, nothing reduced.
```
fund the anchoring account with: 10 XRP (base reserve, never spent) + margin.
1 XRP of margin covers roughly 100,000 anchor transactions at base_fee each - more than enough for any
realistic production anchor volume between funding top-ups. Re-check server_state after Profile B's
network is up in case production fee/reserve settings were deliberately set differently from DEV.
```

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
  IDAX_LEDGER_PROJECT=<XRPL_PROJECT> python3 <REPO>/deploy/idax-ledger/xrpl_start.py
```

3. Wait for convergence with the read-only report (no restart):

```sh
python3 <REPO>/deploy/idax-ledger/xrpl_health.py
```

Expected: validators `proposing`, at least 2 peers each, API node `full`, all validated ledger hashes identical.

`xrpl_health.py` passes only if the validated ledger advances on every node and all nodes agree on its hash. Node
state alone (`proposing`, peers) is not evidence of a working network.

**Start and restart: always `start_xrpl.sh`, never a bare `docker compose up`.** Nodes that stopped at different
validated ledgers and are started together build from the lowest one; the nodes holding a later validated ledger log
"Not validating incompatible with valid ledger" and validation does not resume (DEV, 2026-11-11, twice).
`xrpl_start.py` (called by `start_xrpl.sh`) reads each stopped node's last persisted ledger and starts the most
advanced node first. Verified on DEV on 2026-11-12 with one validator killed and the rest stopped later: validation
resumed in about 140 seconds, twice, with identical history on all six nodes.

- After a host reboot run `start_xrpl.sh`. The containers use `restart: on-failure:5`; whether Docker starts them at
  boot was not tested, and `start_xrpl.sh` is safe either way (it does nothing when the network is healthy and
  performs the ordered start when it is not).
- A single node that stopped while the others kept running needs only `docker start`.
- A node whose store is damaged (seen once after a hard kill of a full-history node: "Missing node", exit 139 in a
  loop) is repaired by moving its `data/db` directory aside and starting it; it syncs from its peers. For a pruned
  validator that costs minutes. For the history node it costs the only full copy - see section 16.
- `xrpl_start.py` reads local files and drives local Docker. With validators on separate hosts (Profile B) the same
  order has to be applied across hosts; that procedure is not written yet (section 16).

**Changing a running network from three to five validators** (done on DEV; not needed for a new production network):
regenerate the configuration, keep the existing validators on full history, start them and the history node first,
start the new validators one at a time, then convert the old validators to pruning one at a time by moving their
store aside. A validator does not start with `online_delete` on a store created without it.

---

## 8. Platform, IDAX Ledger and STIR start

### 8.1 Platform service principal and grant (canonical mechanism only)

`RUN ON: PROD-STIR`

Create the principal and the grant **only** through the canonical platform functions
(`service_principal_create`, `service_principal_create_grant`) inside one transaction that sets the tenant context.
Never insert into `service_principal*` tables directly.

**Ledger enablement is explicit per tenant — there is no wildcard, all-tenants, or "surrogate" grant.** A separate,
narrow grant exists for every tenant that may deliver proofs: `ostris-ledger-delivery` + that tenant's own id + audience
`idax-ledger` + exactly `LEDGER_PROOF_CREATE`, `LEDGER_READ`, `LEDGER_PROOF_VERIFY` (or the reduced set already proven
sufficient). A grant scoped to one tenant (e.g. a dedicated routing/test tenant) never authorizes delivery for any other
tenant's commits — confirmed live on DEV: the same client credentials against a different, real, existing tenant with no
grant of its own were rejected with the identical uniform 401 as an invalid credential. See section 8.4 for the full
per-tenant enable/disable procedure; this subsection is only the mechanism the grant itself uses.

Forbidden in any grant: admin, tenant-wide wildcards, an all-tenants/global grant, governance capabilities, role
management, SuperAdmin, and write capabilities unrelated to proof delivery and verification.

The credential secret is generated on the host, stored under `<RUNTIME>/secrets/ostris_ledger_client_secret` (mode 600),
and only its BCrypt hash is stored in the platform database. Never record the value. The SAME credential secret is reused
across every tenant's grant (it authenticates the delivery worker itself, not a specific tenant) — only the grant row
differs per tenant.

### 8.2 Ledger tenant mirror (referential only)

`RUN ON: PROD-STIR`, script `deploy/idax-ledger/mirror_platform_tenant.sh`.

The mirror copies only: `tenant_id`, `code`, `name`, `status`, `mfa_required`, timestamps. It does not copy users,
memberships, roles, Seven Keys state, SuperAdmin state, secrets or credentials. The mirror is routing information, not an
authorization source. If the platform row and mirror disagree, the platform row wins; the mirror is never deleted
automatically.

### 8.3 Tenant ledger enablement lifecycle

Ledger activation is per tenant and explicit. Never provision every tenant automatically — a tenant that has not been
through this procedure must stay denied, and that denial is the correct, expected outcome, not a bug to clean up.

#### ENABLE LEDGER FOR TENANT `<tenant>`

`RUN ON: PROD-STIR` for all steps.

1. **Verify the tenant exists in the platform and is the real originating tenant you intend** (not a routing/test
   surrogate for it):
   ```sh
   # superuser session required
   curl -s -H "Authorization: Bearer <SUPERUSER_TOKEN>" https://<stir-host>/api/shell/v1/platform/tenants \
     | python3 -c "import json,sys;[print(t['id'],t['code'],t['name']) for t in json.load(sys.stdin)]"
   ```
   Confirm `<tenant>`'s id and code match what you expect before continuing.
2. **Create the minimal tenant mirror in IDAX Ledger** (referential only — never users, memberships, roles, Seven Keys,
   SuperAdmin state, secrets or credentials):
   ```sh
   PLATFORM_PG=<platform_postgres_container> LEDGER_PG=<ledger_postgres_container> TENANT_ID=<tenant-uuid> \
     sh deploy/idax-ledger/mirror_platform_tenant.sh
   ```
3. **Create the tenant-scoped grant** through the canonical function only, inside one transaction that sets the tenant
   context — never an ad-hoc `INSERT`:
   ```sql
   BEGIN;
   SELECT set_config('app.tenant_id', '<tenant-uuid>', true);
   SELECT idax_core.service_principal_create_grant(
     gen_random_uuid(), '<ostris-ledger-delivery-service-principal-id>', '<tenant-uuid>'::uuid,
     'idax-ledger', ARRAY['LEDGER_PROOF_CREATE','LEDGER_READ','LEDGER_PROOF_VERIFY'], '<operator-identity>');
   COMMIT;
   ```
4. **Verify the grant** exists and carries exactly the intended permissions, nothing wider:
   ```sql
   SELECT tenant_id, audience, permissions, enabled FROM idax_core.service_principal_grant
     WHERE service_principal_id = '<...>' AND tenant_id = '<tenant-uuid>';
   ```
5. **Run one safe proof test** end to end: a single controlled, real, signed transaction through the product path for
   this tenant (same shape as `scripts/ledger_integration_e2e.py`), and confirm it reaches `ANCHORED` with a validated
   XRPL transaction (see section 9's acceptance criteria). On DEV, this exact sequence (mirror → grant → proof) was
   proven for the platform's own `stir` tenant: a direct token request against a **different**, real, existing tenant
   with no grant of its own was rejected with the same uniform 401 as any other invalid credential — the isolation is
   structural, not just untested.
6. **Record provisioning evidence** in the checklist: tenant id/code, grant id, timestamp, operator identity, the proof
   transaction's identifiers (agreement id, osTRIS transaction id, outbox id, ledger proof id, XRPL transaction hash,
   validated ledger index).

#### DISABLE LEDGER FOR TENANT `<tenant>`

`RUN ON: PROD-STIR`

Disabling future delivery must never erase history. Do not delete or alter any existing row in `ostris.journal_transaction`,
`ostris.protocol_proof_outbox`, `idax_ledger.ledger_proof`, `idax_ledger.ledger_submission`, or the audit tables. Prior
anchors, proof records, audit evidence and osTRIS journal state are all permanent regardless of this action.

1. Revoke the grant (never delete the row — this preserves the authorization history itself):
   ```sql
   BEGIN;
   SELECT set_config('app.tenant_id', '<tenant-uuid>', true);
   UPDATE idax_core.service_principal_grant SET revoked_at = now(), enabled = false
     WHERE service_principal_id = '<...>' AND tenant_id = '<tenant-uuid>' AND revoked_at IS NULL;
   COMMIT;
   ```
2. Confirm new delivery attempts for this tenant are rejected the same way an unprovisioned tenant is (uniform 401 — see
   the DEV proof above).
3. Any outbox row already `PENDING`/`FAILED_RETRYABLE` for this tenant at the moment of revocation will fail on its next
   attempt (a now-missing grant, classified `FAILED_PERMANENT` like any other missing-grant case) and stays there,
   correctly, until the tenant is re-enabled or an operator makes an explicit decision about it — never auto-cleaned.
4. The ledger tenant mirror is left in place (it is routing/display metadata with no bearing on authorization; deleting
   it would only break foreign-key integrity for the tenant's existing proof rows).

A tenant re-enabled later (steps above, ENABLE again) simply gets a new grant row; its prior history, including any rows
that failed while disabled, is unaffected and can go through the reviewed replay path (section "Failed outbox rows,
tenant provisioning changes" below) if the operator decides those specific rows should now be retried.

### 8.4 Start order

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

Enable delivery only after the service token path is confirmed AND at least one tenant has been through the section 8.3
ENABLE procedure. `RUN ON: PROD-STIR`.

1. Confirm the service-token endpoint answers the ostris principal with a valid token **for an enabled tenant** (a
   token request must succeed; do not paste the token anywhere), and separately confirm it is refused for a tenant
   that has no grant yet (same uniform 401 either way — see section 8.3 step 5).
2. Set `OSTRIS_LEDGER_ENABLED=true` in the override and recreate **only** the `ostris` service. This flag turns the
   delivery worker on globally; it does not by itself authorize any tenant — that is the grant from section 8.3.
3. Run one controlled, real, signed transaction through the product path (the same flow as
   `scripts/ledger_integration_e2e.py`, run against a production-approved, already-ENABLEd tenant — a production test
   tenant if one exists, not a real business tenant unless the operator explicitly approves it).
4. Verify on the database (read-only queries):
   - the `ostris.protocol_proof_outbox` row for the commit is `ANCHORED` (the success state in `ProtocolProofOutboxObservability`);
   - the proof digest matches the journal commit;
   - the XRPL transaction is validated (`tesSUCCESS`, validated ledger index recorded).
5. Record the STIR agreement id, osTRIS transaction id, outbox id, XRPL transaction hash and validated ledger index.

**Acceptance:** all five records present; the anchored memo carries only the digest reference.

---

## 10. Retention and capacity (measured on DEV; re-measure on production)

The network closes about 17 ledgers a minute whether or not anything is anchored, and every ledger, even an empty one,
rewrites the recent-ledger-hashes object: about 9 KB per ledger per node. Growth follows time, not STIR traffic, and
cannot be configured away. It is bounded by pruning.

| Node | History kept | Measured on DEV | Growth |
|---|---|---|---|
| Validator (x5) | last 50,000 ledgers (`online_delete`, about two days) | 0.45 GB at 50,000 ledgers | none after the store rotates; budget 1 GB (the store holds up to twice the window) |
| History node (x1) | full (`ledger_history=full`) | 5.2 GB in 23.5 days | about 0.22 GB a day, about 81 GB a year |
| Logs, per validator | seven days, compressed | 0.12 GB a day uncompressed, ratio about 3.7:1 | budget 0.5 GB |
| Logs, history node | seven days, compressed | 0.012 GB a day uncompressed | budget 0.1 GB |

- Disk per validator host: 2 GB for the node, plus headroom. Disk for the history node: `0.22 GB x days + 0.1 GB`,
  2x headroom: about 165 GB for one year.
- Profile A (all six nodes on one disk): about 0.22 GB a day in total, about 175 GB for one year with headroom.
  Before pruning the same network grew about 0.9 GB a day and filled a 95 GB disk in 23 days.
- IDAX Ledger must read the history node: verifying an old proof fetches the old transaction and ledger. Checked on
  DEV: the October anchor transaction is found on the history node and returns `txnNotFound` on a pruned validator.
  `compose.ledger.yml` sets the first (primary) node to `api-01`.

xrpld stops itself when less than 512 MB is free ("Out of transaction DB space"); nodes then stop at different validated
ledgers. Alert thresholds to configure before activation: disk free below 20% (page), below 10% (act); validated ledger
not advancing for 60 seconds (`xrpl_health.py` exit status); ledger outbox `FAILED_RETRYABLE` growing for more than
15 minutes; `NegativeUNL` not empty.

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
- a delivery returns a permanent failure for a proof you believe should have succeeded (diagnose the real cause first —
  wrong/missing tenant grant is the most common one; use section 15's reviewed replay only once the cause is actually
  fixed, never as a first response);
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

## 15. Failed outbox rows and tenant provisioning changes

`FAILED_PERMANENT` outbox rows are terminal and never retried automatically — by design, so a configuration/credential
problem does not spin forever (see the non-negotiable retry classification above). The reviewed replay command
(`POST /api/ostris/admin/ledger-outbox/{id}/replay`, permission `OSTRIS_LEDGER_OUTBOX_REPLAY`, human operator only —
never a service principal) is the only way back, and it is **narrow**: it replays exactly the one row identified,
records an immutable audit entry of the original failure first, and requeues it through the normal delivery pipeline.
Proven live on DEV: three historical rows that failed before their tenant (`stir`) was ENABLEd were left untouched
until the tenant went through section 8.3, then replayed successfully to `ANCHORED` with full history preserved.

Do not grant a tenant, or replay a row, solely to make a stuck row go away. If a tenant is deliberately not
ledger-enabled, its `FAILED_PERMANENT` rows are the **expected, correct** outcome — leave them. Only replay a row once
its own tenant has gone through the real ENABLE procedure for a real reason, not as cleanup.

## 16. Known open items (carry forward, do not hide)

- AUD-012 (runtime credential can write governed state): MITIGATED — DETECTED ONLY, OPEN. Not closed by this release.
- AUD-007: OPEN.
- **Single full-history copy (blocking).** Only the history node can verify old proofs, and IDAX Ledger submits
  through it. If it is down, anchoring and verification stop even though the validators continue; if its store is
  damaged, the history older than the validators' window is gone. Decided 2026-11-12: a second full-history node
  (not scheduled backups). Not implemented yet: compose service, configuration, IDAX Ledger second node URL and a
  failover test are still to be written and verified on DEV before production.
- **Ordered start across hosts (blocking for Profile B).** Section 7.
- **Never rehearsed.** No one has executed this runbook from the first step to the last on a clean host.
- **Host reboot.** A boot-time unit that runs `start_xrpl.sh` is not written or tested.
- Genesis, validator independence and retention are now deployment-time checkpoints (section 2, CP1–CP5), not open
  architecture decisions — but their actual answers (host count, measured growth, measured fee/reserve) are still
  environment inputs this document cannot supply in advance.
- Production has not been started. This release is not deployed to stir.es.
