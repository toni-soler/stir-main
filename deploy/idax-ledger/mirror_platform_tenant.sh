#!/bin/sh
# Mirrors ONE platform tenant row into the IDAX Ledger database, as a referential/routing identity only.
#
# Why: idax_ledger.ledger_proof and ledger_submission reference idax_core.tenant(tenant_id). Without the row, the ledger
# cannot store proofs for that tenant.
#
# What is copied (and nothing else): tenant_id, code, name, status, mfa_required, created_at, updated_at.
#   - NOT copied: users, memberships, roles, Seven Keys / constitutional state, SuperAdmin state, credentials, service
#     principals, grants, secrets. Authentication, membership and community authority stay in the platform database.
#
# Who runs it: the operator, after the platform tenant exists and before the first ledger delivery for that tenant. It is
# idempotent (ON CONFLICT DO UPDATE), so re-running it refreshes the mirror from the platform row.
#
# Disagreement: the platform row always wins. The ledger row is metadata for display and foreign-key integrity only; a
# mismatch in code/name/status is corrected by re-running this script. A tenant missing in the platform but present in the
# ledger is NOT deleted automatically: the ledger keeps its proofs, and the operator decides (see the runbook).
#
# Usage (DEV):
#   PLATFORM_PG=stir-claude-mvp-postgres-1 LEDGER_PG=idax-ledger-dev-postgres TENANT_ID=<uuid> sh mirror_platform_tenant.sh
set -eu
: "${PLATFORM_PG:?container name of the platform PostgreSQL}"
: "${LEDGER_PG:?container name of the ledger PostgreSQL}"
: "${TENANT_ID:?tenant uuid to mirror}"
case "$TENANT_ID" in
  [0-9a-f]*-*-*-*-*) ;;
  *) echo "TENANT_ID must be a uuid" >&2; exit 2 ;;
esac

ROW=$(docker exec "$PLATFORM_PG" psql -X -At -F '|' -U postgres -d idax -c \
  "select tenant_id, code, name, status, mfa_required, created_at, updated_at from idax_core.tenant where tenant_id = '$TENANT_ID'")
[ -n "$ROW" ] || { echo "platform has no tenant $TENANT_ID" >&2; exit 3; }
IFS='|' read -r ID CODE NAME STATUS MFA CREATED UPDATED <<EOF
$ROW
EOF

docker exec -i "$LEDGER_PG" psql -X -v ON_ERROR_STOP=1 -U postgres -d idax <<SQL
insert into idax_core.tenant (tenant_id, code, name, status, mfa_required, created_at, updated_at)
values ('$ID', '$CODE', '$NAME', '$STATUS', $( [ "$MFA" = t ] && echo true || echo false ), '$CREATED', '$UPDATED')
on conflict (tenant_id) do update set code = excluded.code, name = excluded.name, status = excluded.status,
  mfa_required = excluded.mfa_required, updated_at = excluded.updated_at;
SQL
echo "mirrored tenant $ID ($CODE) into the ledger database"
