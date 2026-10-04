#!/bin/sh
# Flyway upgrade gate on DISPOSABLE PostgreSQL containers. Never touches the validated stack or stir-dev.
#
#   sh scripts/flyway_matrix.sh <empty|v16|v19> <outdir>
#
# Same migration chain and Flyway version as deploy/compose.yml (core-migrations, then module-migrations).
# empty: a fresh database is migrated to the latest version.
# v16 / v19: the database is migrated to that version only, then realistic rows are loaded from the validated
#   stack for every stir/idax_core table whose column list is identical at that version and now (tables changed
#   by later migrations are listed as skipped, not guessed). Then the database is migrated to the latest version.
# Before and after the final migration the script records row counts, per-table content hashes, grants, role
# attributes, role memberships and audit-table counts, and checks that no ordinary role can assume the verifier.
set -eu

STAGE=$1
OUT=$2
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BACKEND=${STIR_BACKEND_DIR:-$ROOT/../stir-backend}
SOURCE=${STIR_SOURCE_PG:-stir-claude-mvp-postgres-1}
CORE_IMAGE=${STIR_CORE_IMAGE:-stir-claude-mvp-core-migrations}
FLYWAY_IMAGE=flyway/flyway:11.12.0-alpine
PG_IMAGE=postgres:17-alpine

case "$STAGE" in
  empty) TARGET="" ;;
  v16) TARGET="-target=16" ;;
  v19) TARGET="-target=19" ;;
  *) echo "unknown stage: $STAGE" >&2; exit 2 ;;
esac

RUN="fmx-$STAGE-$(date -u +%H%M%S)"
NET="$RUN-net"
mkdir -p "$OUT"
chmod 700 "$OUT"
SECRET="$OUT/.pw"
head -c 24 /dev/urandom | base64 | tr -d '/+=\n' > "$SECRET"
chmod 600 "$SECRET"

cleanup() {
  docker rm -f "$RUN" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -f "$SECRET"
}
trap cleanup EXIT INT TERM

echo "stage=$STAGE target=${TARGET:-latest} run=$RUN" > "$OUT/meta.txt"
echo "stir_backend_commit=$(git -C "$BACKEND" rev-parse HEAD)" >> "$OUT/meta.txt"
echo "started=$(date -u +%FT%TZ)" >> "$OUT/meta.txt"

docker network create "$NET" >/dev/null
docker run -d --name "$RUN" --network "$NET" --network-alias postgres \
  -e POSTGRES_DB=idax -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD_FILE=/run/secrets/pg \
  -v "$SECRET:/run/secrets/pg:ro" "$PG_IMAGE" >/dev/null
i=0
until docker exec "$RUN" pg_isready -U postgres -d idax >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -lt 90 ] || { echo "postgres did not start" >&2; exit 1; }
  sleep 1
done

psql_run() { docker exec -i "$RUN" psql -X -At -v ON_ERROR_STOP=1 -U postgres -d idax "$@"; }

# 1. idax_core, exactly as core-migrations does it
docker run --rm --network "$NET" -v "$SECRET:/run/secrets/postgres_password:ro" --entrypoint /bin/sh "$CORE_IMAGE" -ec \
  'exec flyway -url=jdbc:postgresql://postgres:5432/idax -user=postgres -password="$(cat /run/secrets/postgres_password)" -schemas=idax_core -table=flyway_schema_history_idax_core migrate' \
  > "$OUT/migrate-core.log" 2>&1

# 2. module schemas, same order and history tables as migrate-modules.sh; $TARGET applies to stir only when staged
migrate_module() {
  schema=$1; path=$2; history=$3; extra=$4
  docker run --rm --network "$NET" -v "$SECRET:/run/secrets/postgres_password:ro" -v "$path:/migrations/$schema:ro" \
    --entrypoint /bin/sh "$FLYWAY_IMAGE" -ec \
    "exec flyway -url=jdbc:postgresql://postgres:5432/idax -user=postgres -password=\"\$(cat /run/secrets/postgres_password)\" -schemas=$schema -table=$history -locations=filesystem:/migrations/$schema $extra migrate" \
    > "$OUT/migrate-$schema${extra:+-staged}.log" 2>&1
}
migrate_module idax_shell "$ROOT/vendor/idax-shell/src/main/resources/db/migration-idax-shell" flyway_schema_history_idax_shell ""
migrate_module stir "$BACKEND/src/main/resources/db/migration-stir" flyway_schema_history "$TARGET"
migrate_module idax_ledger "$ROOT/vendor/idax-ledger/backend/src/main/resources/db/migration-idax-ledger" flyway_schema_history ""
migrate_module ostris "$ROOT/vendor/ostris/backend/src/main/resources/db/migration-ostris" flyway_schema_history ""

# Test-only login for the ordinary runtime role, so the negative "cannot assume the verifier role" check runs as it would.
ORDINARY_PW=$(cat "$SECRET")
psql_run -c "ALTER ROLE idax_backend LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '$ORDINARY_PW'" >/dev/null

snapshot() {
  name=$1
  psql_run -c "select 'rows '||table_schema||'.'||table_name||' '||(xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text
    from information_schema.tables where table_schema in ('stir','idax_core','stir_audit') and table_type='BASE TABLE' and table_name not like 'flyway%' order by 1" > "$OUT/$name-rows.txt"
  psql_run -c "select format('select %L || md5(coalesce(string_agg(t::text, chr(10) order by t::text), %L)) from %I.%I t;', 'content '||table_schema||'.'||table_name, '', table_schema, table_name)
    from information_schema.tables where table_schema in ('stir','idax_core') and table_type='BASE TABLE' and table_name not like 'flyway%' order by 1" > "$OUT/$name-content.sql"
  psql_run < "$OUT/$name-content.sql" > "$OUT/$name-content.txt"
  psql_run -c "select 'grant '||grantee||' '||table_schema||'.'||table_name||' '||privilege_type from information_schema.role_table_grants
    where table_schema in ('stir','idax_core','stir_audit') and (grantee like 'idax%' or grantee like 'stir%') order by 1" > "$OUT/$name-grants.txt"
  psql_run -c "select 'role '||rolname||' login='||rolcanlogin||' inherit='||rolinherit||' bypassrls='||rolbypassrls||' super='||rolsuper||' createrole='||rolcreaterole
    from pg_roles where rolname like 'idax%' or rolname like 'stir%' order by 1" > "$OUT/$name-roles.txt"
  psql_run -c "select 'member '||m.rolname||' of '||g.rolname from pg_auth_members a join pg_roles m on m.oid=a.member join pg_roles g on g.oid=a.roleid order by 1" > "$OUT/$name-members.txt"
  psql_run -c "select 'audit '||case when to_regclass('stir_audit.mutation_event') is null then 'absent' else
    (xpath('/row/c/text()', query_to_xml('select count(*) as c from stir_audit.mutation_event', false, true, '')))[1]::text end" > "$OUT/$name-audit.txt"
  psql_run -c "select 'schema_history '||coalesce(max(version),'none') from stir.flyway_schema_history where success" > "$OUT/$name-stir-version.txt" 2>/dev/null || echo "schema_history none" > "$OUT/$name-stir-version.txt"
}

# 3. staged realistic rows copied from the validated stack
if [ "$STAGE" != empty ]; then
  describe() { psql_run -c "select table_schema||'.'||table_name||'|'||string_agg(column_name||':'||data_type, ',' order by ordinal_position)
    from information_schema.columns where table_schema in ('stir','idax_core') and table_name not like 'flyway%' group by table_schema, table_name order by 1"; }
  describe > "$OUT/staged-target-columns.txt"
  docker exec "$SOURCE" psql -X -At -U postgres -d idax -c "select table_schema||'.'||table_name||'|'||string_agg(column_name||':'||data_type, ',' order by ordinal_position)
    from information_schema.columns where table_schema in ('stir','idax_core') and table_name not like 'flyway%' group by table_schema, table_name order by 1" > "$OUT/staged-source-columns.txt"
  sort "$OUT/staged-target-columns.txt" > "$OUT/t.sorted"; sort "$OUT/staged-source-columns.txt" > "$OUT/s.sorted"
  comm -12 "$OUT/t.sorted" "$OUT/s.sorted" > "$OUT/staged-identical.txt"
  comm -13 "$OUT/t.sorted" "$OUT/s.sorted" > "$OUT/staged-skipped.txt"
  cut -d'|' -f1 "$OUT/staged-identical.txt" > "$OUT/staged-tables.txt"
  args=""
  while read -r table; do args="$args -t $table"; done < "$OUT/staged-tables.txt"
  # --disable-triggers: the seeded rows are copies of an already-audited history; the audit chain is verified on the stack, not here
  docker exec "$SOURCE" pg_dump -U postgres -d idax --data-only --disable-triggers --no-owner $args > "$OUT/staged-data.sql"
  docker exec -i "$RUN" psql -X -v ON_ERROR_STOP=1 -q -U postgres -d idax < "$OUT/staged-data.sql" > "$OUT/staged-load.log" 2>&1
fi

snapshot before
echo "before_snapshot=$(date -u +%FT%TZ)" >> "$OUT/meta.txt"

# 4. migrate to the latest version (no target), in the same order as the stack
migrate_module idax_shell "$ROOT/vendor/idax-shell/src/main/resources/db/migration-idax-shell" flyway_schema_history_idax_shell ""
migrate_module stir "$BACKEND/src/main/resources/db/migration-stir" flyway_schema_history ""
migrate_module idax_ledger "$ROOT/vendor/idax-ledger/backend/src/main/resources/db/migration-idax-ledger" flyway_schema_history ""
migrate_module ostris "$ROOT/vendor/ostris/backend/src/main/resources/db/migration-ostris" flyway_schema_history ""
echo "after_migration=$(date -u +%FT%TZ)" >> "$OUT/meta.txt"

snapshot after

# 5. evaluation
FAIL=0
{
  echo "stage=$STAGE"
  echo "latest_stir_version=$(cat "$OUT/after-stir-version.txt")"
  # no table may lose rows (rows lines are: rows <schema.table> <count>)
  lost=$(awk 'NR==FNR{b[$2]=$3;next} {if(($2 in b) && $3+0 < b[$2]+0) print "LOST "$2" "b[$2]" -> "$3}' "$OUT/before-rows.txt" "$OUT/after-rows.txt")
  if [ -n "$lost" ]; then echo "$lost"; echo "ROW_LOSS=FAIL"; FAIL=1; else echo "ROW_LOSS=NONE"; fi
  changed=$(diff <(sed 's/ [0-9]*$//' "$OUT/before-content.txt" | sort) <(sed 's/ [0-9]*$//' "$OUT/after-content.txt" | sort) | grep '^[<>]' | wc -l || true)
  echo "CONTENT_LINES_DIFFERING=$changed (expected only for tables whose structure changed after the staged version)"
  diff "$OUT/before-grants.txt" "$OUT/after-grants.txt" > "$OUT/grants.diff" || true
  echo "GRANT_DIFF_LINES=$(wc -l < "$OUT/grants.diff")"
  echo "audit before: $(cat "$OUT/before-audit.txt")  after: $(cat "$OUT/after-audit.txt")"
} > "$OUT/evaluation.txt"

# verifier role isolation, as the ordinary runtime login
assume=$(docker run --rm --network "$NET" -e PGPASSWORD="$ORDINARY_PW" "$PG_IMAGE" psql -X -h postgres -U idax_backend -d idax -c 'set role idax_governed_verifier' 2>&1 || true)
case "$assume" in
  *"permission denied"*) echo "ORDINARY_ROLE_ASSUME_VERIFIER=DENIED" >> "$OUT/evaluation.txt" ;;
  *) echo "ORDINARY_ROLE_ASSUME_VERIFIER=NOT_DENIED" >> "$OUT/evaluation.txt"; FAIL=1 ;;
esac
verifier_member=$(psql_run -c "select count(*) from pg_auth_members a join pg_roles m on m.oid=a.member join pg_roles g on g.oid=a.roleid where g.rolname='idax_governed_verifier'")
echo "ANY_MEMBER_OF_VERIFIER=$verifier_member" >> "$OUT/evaluation.txt"
[ "$verifier_member" = "0" ] || FAIL=1
grep -q "^role idax_governed_verifier login=f inherit=f bypassrls=f" "$OUT/after-roles.txt" || { echo "VERIFIER_ROLE_ATTRIBUTES=UNEXPECTED" >> "$OUT/evaluation.txt"; FAIL=1; }

rm -f "$OUT/t.sorted" "$OUT/s.sorted" "$OUT/staged-data.sql"
echo "finished=$(date -u +%FT%TZ)" >> "$OUT/meta.txt"
echo "result=$([ "$FAIL" -eq 0 ] && echo PASS || echo FAIL)" >> "$OUT/evaluation.txt"
cat "$OUT/evaluation.txt"
[ "$FAIL" -eq 0 ]
