# Community catalog composition example

This opt-in development composition mounts the second presentation from `stir-frontend/examples/community-catalog`. It does not change STIR's default manifest or production proxy. Run it only against a local development database.

Build `stir-frontend` with `npm ci && npm run build`, then from `stir-main`:

```sh
docker compose -f compose.yml -f examples/community-catalog/compose.override.yml up -d --build
python scripts/community_catalog_browser_e2e.py
docker compose -f compose.yml -f examples/community-catalog/compose.override.yml down
```

The browser script needs Playwright and Microsoft Edge as described in `requirements-browser.txt`. It creates a fresh test tenant and two ordinary participants, uploads a synthetic PNG, sends one real offer from the community extension, and checks the resulting negotiation and STIR gallery. It never writes to the ordinary STIR tenant. Compose `down` preserves database volumes.

For an upgrade rehearsal, first run the browser script against the older pinned frontend revision with `STIR_CATALOG_EXPECT_PHOTOS=0` and `STIR_CATALOG_UPGRADE_MARKER` pointing to an ignored file under `.local/`. This optional marker contains a temporary test password; keep it local and remove it when done. Change only the frontend build source to the newer pinned revision, rebuild/recreate `stir-ui`, and run `scripts/community_catalog_upgrade_e2e.py` with the same marker path. It verifies the original listing, authenticated photo and negotiation without writing new business data. Use isolated Git worktrees to keep both frontend revisions available while another agent works in the normal checkout.
