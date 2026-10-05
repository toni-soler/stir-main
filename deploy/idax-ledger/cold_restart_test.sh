#!/bin/sh
# Cold-restart test for the IDAX private XRPL network: records a historical ledger hash, stops the whole project (volumes
# and node identities are kept, nothing is recreated), starts it once with no per-node restarts, waits for bounded
# convergence and verifies that every node still returns the same historical hash.
#   IDAX_LEDGER_RUNTIME_PATH=... XRPLD_IMAGE=... IDAX_LEDGER_REPO_PATH=... IDAX_LEDGER_MIGRATIONS_PATH=... \
#   IDAX_LEDGER_ANCHOR_ACCOUNT=... sh deploy/idax-ledger/cold_restart_test.sh <outdir>
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
OUT=${1:?output directory}
mkdir -p "$OUT"
F="-p idax-ledger-dev -f $DIR/compose.xrpl.yml -f $DIR/compose.ledger.yml"
rpc() { curl -s -m 10 -X POST -d "$2" "http://127.0.0.1:$1"; }
VAL=$(rpc 5006 '{"method":"server_info","params":[{}]}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["info"]["validated_ledger"]["seq"])')
HIST=$((VAL - 30))
BEFORE=$(rpc 5006 "{\"method\":\"ledger\",\"params\":[{\"ledger_index\":$HIST}]}" | python3 -c 'import json,sys;r=json.load(sys.stdin)["result"];print(r.get("ledger_hash") or r["ledger"]["ledger_hash"])')
echo "pre validated=$VAL historical=$HIST hash=$BEFORE" | tee "$OUT/meta.txt"
docker compose $F down > "$OUT/down.log" 2>&1
docker compose $F up -d > "$OUT/up.log" 2>&1
i=0
until python3 "$DIR/xrpl_health.py" > "$OUT/health.txt" 2>&1 || [ "$i" -ge 36 ]; do i=$((i + 1)); sleep 5; done
echo "converged_after_polls=$i" >> "$OUT/meta.txt"
cat "$OUT/health.txt" >> "$OUT/meta.txt"
for port in 5006 5007 5008 5010; do
  H=$(rpc $port "{\"method\":\"ledger\",\"params\":[{\"ledger_index\":$HIST}]}" | python3 -c 'import json,sys;r=json.load(sys.stdin)["result"];print(r.get("ledger_hash") or r["ledger"]["ledger_hash"])')
  [ "$H" = "$BEFORE" ] && echo "history_port_$port=IDENTICAL" >> "$OUT/meta.txt" || { echo "history_port_$port=DIFFERENT" >> "$OUT/meta.txt"; exit 1; }
done
echo "COLD_RESTART: PASS" >> "$OUT/meta.txt"
