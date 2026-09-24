# Changelog

## fix: proxy healthcheck always failed in production

`compose.yml`'s `proxy` healthcheck hits `http://localhost:8088/actuator/health/readiness` -
correct for the dev Caddyfile's `:8088` site block, but `Caddyfile.production` only ever defines
`{$STIR_PUBLIC_HOSTNAME}` on 80/443. `compose.production.yml` never overrode it, so Caddy was
unconditionally "unhealthy" in every production deploy regardless of actual health - found
2026-09-24 when a redeploy's `deploy.py` health wait looked stuck for over an hour against an
otherwise fully working stir.es. `compose.production.yml` now checks Caddy's own admin API
instead (`127.0.0.1:2019/config/` - not `localhost`, which failed to resolve inside the Alpine
container in testing), present regardless of which site blocks are configured. Verified the exact
wget command against a real `caddy:2.9-alpine` container.

## One-command update: `scripts/update.py`

Every upstream pin bump so far meant hand-running the same sequence on the target host: `git
pull` this repo and every sibling repo, re-fetch/checkout whichever `vendor/` clone(s) moved to a
new pinned commit (`initialize.py` only ever verified a pin, it never updated a stale one - the
exact "idax-shell: wrong upstream commit" failure hit deploying the previous entry's fixes), then
`deploy.py`. `initialize.py` now self-heals a stale vendor checkout to whatever `upstream.lock.json`
currently pins (discarding only its own previously-applied patch state, never real work - the
reviewed patches get reapplied fresh right after). New `scripts/update.py` chains the whole
sequence: pull stir-main + present siblings (refusing over any uncommitted tracked change) →
`initialize.py` → `deploy.py [--prod]`, deriving the production smoke check's target URL/login
from `.env` instead of requiring them exported by hand.

## Upstream pin bump: fixes found in the first real E2E review of stir.es

Real browser E2E testing against production stir.es (see stir-doc/manual-usuario/) found several
bugs and gated `deploy/extensions.json`'s `ledger` entry behind `"requiredPermission":"LEDGER_READ"`
(hides the module card from users who can't use it - idax-shell's `ExtensionController` now
filters by the caller's effective permissions; an entry without `requiredPermission` stays
visible to everyone, unchanged). `upstream.lock.json` bumped to the commits carrying the fixes:

- `idax-shell` -> `f2c92b1`: the active tenant never reached module extensions (Ledger's Proofs
  rejected every call with "Tenant context is required"); the Users editor could change a user's
  display-only "role" label without ever granting real permissions (the actual grant, a separate
  roleIds assignment, had no UI at all until now); the extension manifest gained permission
  filtering.
- `idax-ledger` -> `ca17714`: Overview/Nodes/Ledgers/Transactions crashed or 500'd with every
  network disabled instead of showing an empty state; the 403 error vocabulary was English-only
  even under the Spanish locale.
- `ostris` -> `5c0d1d2`: opening osTRIS left the whole Shell blank (a second, bundled copy of
  React broke hooks) - now consumes Shell's shared React instance, same as Ledger already did.

Also bumps to `stir-frontend` (separate repo, not upstream-pinned) for the same review's
Marketplace/negotiations contrast and first-publication-flow findings - see its own CHANGELOG.

Verified against the full rebuilt dev stack: `smoke.py` and `multitenant.py` both PASS.

## smoke.py: configurable login email for production

DEPLOYMENT.md's "Initial deploy (production)" step 7 instructs running `python scripts/smoke.py`
against a real production deployment, but the script hardcoded the dev bootstrap login
(`admin@stir.test`) with no way to override it - it could never actually pass against a real
`.env` with a different `STIR_ADMIN_EMAIL`, making that step silently wrong for any real
deployment. Added `STIR_TEST_LOGIN_EMAIL` (defaults to `admin@stir.test`, so the existing dev
flow and README.md's quick start are unaffected). Still needs `STIR_TEST_URL` set to the real
public HTTPS origin in production - `http://localhost:8089` (the dev-only proxy port) is never
published by `compose.production.yml`.

## Secret file permissions fix (real Linux deployment)

On a real Linux host, `scripts/initialize.py` generated `.local/secrets/*` at mode 0600. Docker
Compose (non-Swarm) `secrets:` bind-mounts that exact host file into a container's
`/run/secrets/<name>`, preserving host permissions - and `shell`/`stir`/`ostris`/`ledger` each run
as non-root (`USER 10001` in their Dockerfiles), a different UID than whichever host user ran
`initialize.py`. Result: `cat: /run/secrets/runtime_password: Permission denied` inside those
containers, surfacing as a Postgres `SCRAM-based authentication, but no password was provided`
crash loop - reproducible on Rocky 9, and identically on any real Linux host (this is plain POSIX
file permissions, not SELinux or Rocky-specific; a Windows/macOS Docker Desktop dev clone never
hits it because `initialize.py` skips `chmod` entirely off POSIX). Fixed by generating these
secret files at 0644 instead of 0600 - re-running `initialize.py` re-applies the new mode to
already-generated files without changing their values. Verified the exact permission behavior
(0600 denied, 0644 allowed for a non-owning UID) in a real Linux container.

## MinIO image source fix

`docker.io/minio/minio` now denies anonymous pulls of the pinned `RELEASE.2025-04-08T15-41-24Z`
tag (`pull access denied ... repository does not exist or may require 'docker login'`) - MinIO
moved distribution to `quay.io/minio/minio`, same official image and tag. `compose.yml`'s `minio`
service now pulls from `quay.io/minio/minio:RELEASE.2025-04-08T15-41-24Z` instead. Verified by
pulling the corrected image and bringing `minio`+`postgres` up healthy against it. Affects both
the dev stack and the production overlay, since `compose.production.yml` does not override the
`minio` service.

## Host provisioning documentation

New `HOST_PROVISIONING.md`: the bare-VM prerequisite step before DEPLOYMENT.md's "Initial deploy
(production)" - installing Docker Engine + the Compose plugin, opening the firewall, and (Rocky
Linux 9-specific) the SELinux relabel `compose.production.yml`'s read-only Caddyfile bind mount
needs under Enforcing. Documents Rocky Linux 9 as the primary target with Ubuntu 22.04/24.04
noted wherever the procedure differs. No product/runtime change. DEPLOYMENT.md step 1's dangling
"(see DNS section below)" reference (no such section existed) now points here instead.

## Upstream 0.4 baseline

Pinned the reproducible public stack to IDAX Core Runtime, IDAX Shell, IDAX Ledger and osTRIS 0.4.0. Shell 0.4 now provides the generic manifest-driven extension and session-permission contracts, so both temporary Shell patches were removed after reverse-application equivalence checks against the exact public tag. The reviewed Ledger migration switch and osTRIS migration/public-application-surface patches remain because those capabilities are not part of their 0.4 releases.

## 0.3.0-SNAPSHOT

`compose.yml`: `stir` service gains `OSTRIS_BASE_URL` and now depends on `ostris` being healthy (it calls osTRIS's HTTP API directly at runtime, as the sole gateway - see OSTRIS_INTEGRATION.md in stir-doc). New `scripts/economic_exchange_e2e.py` (HTTP: OFFER and WANTED direction, reconciliation, idempotency, a real credit-floor policy rejection) and `scripts/economic_browser_smoke.py` (two real Playwright browser sessions, each generating and holding its own Ed25519 key) prove the full economic exchange end to end against the real running stack. `stir` schema now migrates through V3 (economic exchange). `vendor/ostris` synced from the separate `github-public/ostris` repository's 0.3 working tree (discovery/provisioning/status endpoints) so the Docker build includes them - `vendor/ostris` remains read-only reproducibility only, never edited directly.

## 0.2.0-SNAPSHOT

New `scripts/marketplace_e2e.py` (HTTP) and `scripts/marketplace_browser_smoke.py` (two real Playwright browser sessions) prove the marketplace flow end to end: publish, discover, propose, counter, accept, Agreement + AgreementSnapshot, third-party/foreign-tenant denial. `docker compose build --no-cache` and empty-volume `up -d` re-verified against the 0.2 backend/frontend; `stir` schema now migrates through V2 (marketplace). No compose/deploy changes were needed - the existing module-migrations volume mount and healthcheck-gated startup already covered it.

## 0.1.0-SNAPSHOT

Initial public marketplace foundation; development preview.
