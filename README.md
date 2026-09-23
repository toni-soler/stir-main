# STIR development instance — 0.5.0-rc1

STIR is Sistema Transparente de Intercambio de Recursos. This repository composes an independent marketplace instance using public IDAX Open Core, IDAX Shell, IDAX Ledger and osTRIS 0.4.0. Application source is Apache-2.0. Core's publicly downloadable binary retains its separate binary license.

## Quick start

Requirements: Git, Python 3.10+, OpenSSL, Docker Compose v2 with Linux containers. For native validation: Java 21/Maven 3.9 and Node 22. Clone the four independent public STIR repositories as siblings inside a clone of `stir-workspace`:

```sh
git clone https://github.com/toni-soler/stir-workspace.git stir
cd stir
git clone https://github.com/toni-soler/stir-doc.git
git clone https://github.com/toni-soler/stir-backend.git
git clone https://github.com/toni-soler/stir-frontend.git
git clone https://github.com/toni-soler/stir-main.git
```

```text
workspace/
  stir-doc/
  stir-backend/
  stir-frontend/
  stir-main/
```

Open `../stir.code-workspace`, then from stir-main:

```sh
python scripts/initialize.py
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Visit http://localhost:8089. Login is `admin@stir.test`; read the generated password from `.local/secrets/login_password` (PowerShell: `Get-Content .local/secrets/login_password`; POSIX: `cat .local/secrets/login_password`). Select STIR Development and open STIR from the module cards. Create, list, filter, edit and close your publications. This creates actual records in this development database.

Initializer pins anonymous public Git inputs to upstream.lock.json, preserves existing secrets and generates 3072-bit RSA keys. No local Maven mirror, private artifact or developer-built JAR is used. Copy .env.example to .env only to change local ports. Every published port binds loopback. No production DNS/domain dependency exists.

## Composition boundaries

PostgreSQL 17 → public Core migrations → module migrations → runtime role provisioning → Shell/STIR/osTRIS/Ledger. nginx serves modules; Caddy exposes one browser origin. STIR maintains only its own listing tables. The public module Dockerfiles reference stale jar versions; deploy/Module.Dockerfile builds the same pinned source and selects the resulting jar without modifying those projects.

IDAX Shell 0.4 provides the manifest-driven extension host, active tenant/user SDK context and effective permissions that STIR previously supplied through temporary patches. Initialization checks each public origin and pinned commit, accepts only the remaining reviewed Ledger/osTRIS patches, and rejects unexpected tracked or untracked source changes. Authentication, membership checks, permissions and saved filters remain Core/Shell responsibilities. STIR supplies its own marketplace UI through the public extension SDK.

STIR 0.5 includes the client-signed osTRIS EXCHANGE lifecycle introduced in 0.3. osTRIS remains the normative authority for authorization, policy evaluation, commit and reconciliation; STIR does not modify economic balances or journal state directly. The public osTRIS 0.4 baseline still receives STIR's reviewed discovery/provisioning/device compatibility patch until those APIs are released upstream. Ledger/XRPL proof delivery remains independently configurable.

Migration jobs alone receive the PostgreSQL bootstrap credential. Application containers receive a distinct idax_backend credential, configured NOSUPERUSER and NOBYPASSRLS, with no database/schema creation privileges. Public module Flyway startup is disabled through small reviewed compatibility patches after the one-shot migrations finish. Production still requires immutable image digests, service-principal provisioning and a security review. The automated runtime proof checks the actual database login and both idax_app/idax_admin tenant contexts; the HTTP suite independently checks two ordinary participants.

## Validation and shutdown

```sh
python scripts/audit-public.py
python scripts/smoke.py
python scripts/multitenant.py
docker compose logs --tail=100 stir
docker compose down
```

Backend Swagger is http://localhost:8096/swagger-ui/index.html; readiness is /actuator/health/readiness. Listing paths include `/api/stir/tenants/{tenantId}/listings` and require a Shell bearer token. Backend validation: `mvn -s .mvn/public-settings.xml clean verify` from stir-backend (Docker required for PostgreSQL test). Frontend: `npm ci`, `npm test`, `npm run i18n:validate`, `npm run build`.

`down` preserves the database. Do not remove volumes unless deliberately discarding local data. No production `stir.es` deployment is performed by these development commands.

## Optional browser verification

With Microsoft Edge installed, create a local Python virtual environment, install `requirements-browser.txt` from public PyPI, and run `python scripts/browser-smoke.py`. It reads the generated local login secret without printing it, opens a headless browser, verifies the module and Listing UI, writes an ignored screenshot under `.local`, and closes its browser. The HTTP/RLS suite requires no browser package.
