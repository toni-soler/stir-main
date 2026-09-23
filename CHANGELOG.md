# Changelog

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
