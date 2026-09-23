# Changelog

## Upstream 0.4 baseline

Pinned the reproducible public stack to IDAX Core Runtime, IDAX Shell, IDAX Ledger and osTRIS 0.4.0. Shell 0.4 now provides the generic manifest-driven extension and session-permission contracts, so both temporary Shell patches were removed after reverse-application equivalence checks against the exact public tag. The reviewed Ledger migration switch and osTRIS migration/public-application-surface patches remain because those capabilities are not part of their 0.4 releases.

## 0.3.0-SNAPSHOT

`compose.yml`: `stir` service gains `OSTRIS_BASE_URL` and now depends on `ostris` being healthy (it calls osTRIS's HTTP API directly at runtime, as the sole gateway - see OSTRIS_INTEGRATION.md in stir-doc). New `scripts/economic_exchange_e2e.py` (HTTP: OFFER and WANTED direction, reconciliation, idempotency, a real credit-floor policy rejection) and `scripts/economic_browser_smoke.py` (two real Playwright browser sessions, each generating and holding its own Ed25519 key) prove the full economic exchange end to end against the real running stack. `stir` schema now migrates through V3 (economic exchange). `vendor/ostris` synced from the separate `github-public/ostris` repository's 0.3 working tree (discovery/provisioning/status endpoints) so the Docker build includes them - `vendor/ostris` remains read-only reproducibility only, never edited directly.

## 0.2.0-SNAPSHOT

New `scripts/marketplace_e2e.py` (HTTP) and `scripts/marketplace_browser_smoke.py` (two real Playwright browser sessions) prove the marketplace flow end to end: publish, discover, propose, counter, accept, Agreement + AgreementSnapshot, third-party/foreign-tenant denial. `docker compose build --no-cache` and empty-volume `up -d` re-verified against the 0.2 backend/frontend; `stir` schema now migrates through V2 (marketplace). No compose/deploy changes were needed - the existing module-migrations volume mount and healthcheck-gated startup already covered it.

## 0.1.0-SNAPSHOT

Initial public marketplace foundation; development preview.
