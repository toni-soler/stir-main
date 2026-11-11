#!/bin/sh
# Restart test for the IDAX private XRPL network, in the condition that broke it on DEV: nodes stopped at DIFFERENT
# validated ledgers. It records a historical ledger hash, kills one validator, lets the others advance, stops them,
# starts everything with xrpl_start.py (one command, no operator choices) and verifies that validation resumes on one
# chain and that every node still returns the same historical hash. Volumes and node identities are kept.
#   IDAX_LEDGER_RUNTIME_PATH=... XRPLD_IMAGE=... sh deploy/idax-ledger/cold_restart_test.sh <outdir> [victim-node]
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
OUT=${1:?output directory}
VICTIM=${2:-validator-02}
PROJECT=${IDAX_LEDGER_PROJECT:-idax-ledger-dev}
mkdir -p "$OUT"
PORTS=$(python3 -c "import sys;sys.path.insert(0,'$DIR');import xrpl_health;print(' '.join(u.rsplit(':',1)[1] for _,u,_ in xrpl_health.NODES))")
NODES=$(python3 -c "import sys;sys.path.insert(0,'$DIR');import xrpl_health;print(' '.join(n for n,_,_ in xrpl_health.NODES))")
rpc() { curl -s -m 10 -X POST -d "$2" "http://127.0.0.1:$1"; }
hash_at() { rpc "$1" "{\"method\":\"ledger\",\"params\":[{\"ledger_index\":$2}]}" | python3 -c 'import json,sys;r=json.load(sys.stdin)["result"];print(r.get("ledger_hash") or r.get("error"))'; }
FIRST=${PORTS%% *}
VAL=$(rpc "$FIRST" '{"method":"server_info","params":[{}]}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["info"]["validated_ledger"]["seq"])')
HIST=$((VAL - 30))
BEFORE=$(hash_at "$FIRST" "$HIST")
echo "pre validated=$VAL historical=$HIST hash=$BEFORE victim=$VICTIM" | tee "$OUT/meta.txt"
docker kill "$PROJECT-$VICTIM" > "$OUT/stop.log" 2>&1
sleep 12
for node in $NODES; do [ "$node" = "$VICTIM" ] || docker stop -t 60 "$PROJECT-$node" >> "$OUT/stop.log" 2>&1; done
START=$(date +%s)
python3 "$DIR/xrpl_start.py" --force > "$OUT/start.txt" 2>&1 || { cat "$OUT/start.txt"; echo "RESTART: FAIL (not healthy)" | tee -a "$OUT/meta.txt"; exit 1; }
echo "healthy_after_seconds=$(( $(date +%s) - START ))" >> "$OUT/meta.txt"
cat "$OUT/start.txt" >> "$OUT/meta.txt"
for port in $PORTS; do
  H=$(hash_at "$port" "$HIST")
  # A pruned or freshly synced node may not hold this ledger; a different hash is the failure, absence is not.
  case "$H" in
    "$BEFORE") echo "history_port_$port=IDENTICAL" >> "$OUT/meta.txt" ;;
    lgrNotFound) echo "history_port_$port=NOT_HELD" >> "$OUT/meta.txt" ;;
    *) echo "history_port_$port=DIFFERENT($H)" >> "$OUT/meta.txt"; echo "RESTART: FAIL" >> "$OUT/meta.txt"; exit 1 ;;
  esac
done
echo "RESTART: PASS" >> "$OUT/meta.txt"
