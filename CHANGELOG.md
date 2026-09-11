# Changelog

## 0.2.0-SNAPSHOT

New `scripts/marketplace_e2e.py` (HTTP) and `scripts/marketplace_browser_smoke.py` (two real Playwright browser sessions) prove the marketplace flow end to end: publish, discover, propose, counter, accept, Agreement + AgreementSnapshot, third-party/foreign-tenant denial. `docker compose build --no-cache` and empty-volume `up -d` re-verified against the 0.2 backend/frontend; `stir` schema now migrates through V2 (marketplace). No compose/deploy changes were needed - the existing module-migrations volume mount and healthcheck-gated startup already covered it.

## 0.1.0-SNAPSHOT

Initial public marketplace foundation; development preview.
