#!/bin/sh
# Starts the IDAX private XRPL network and the IDAX Ledger service in the right order, and recovers peering.
#
# Observed on the DEV network (2026-10-05): when every node is restarted at the same moment, the validators keep their
# validated ledgers but do not re-establish peering; restarting each disconnected node individually does. This script
# encodes that procedure: bring the validators and API node up, restart any node that has fewer than two peers, wait
# until the validators propose and follow the validated ledger, and only then start the ledger service.
#
#   IDAX_LEDGER_RUNTIME_PATH=... XRPLD_IMAGE=... IDAX_LEDGER_REPO_PATH=... IDAX_LEDGER_MIGRATIONS_PATH=... \
#   IDAX_LEDGER_ANCHOR_ACCOUNT=... sh deploy/idax-ledger/start_xrpl.sh
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT=${IDAX_LEDGER_PROJECT:-idax-ledger-dev}
COMPOSE="docker compose -p $PROJECT -f $DIR/compose.xrpl.yml -f $DIR/compose.ledger.yml"
RPC_PORTS="5006 5007 5008 5010"
NODES="validator-01 validator-02 validator-03 api-01"

peers_of() {
  curl -s -m 5 -X POST -d '{"method":"server_info","params":[{}]}' "http://127.0.0.1:$1" |
    python3 -c 'import json,sys;i=json.load(sys.stdin)["result"]["info"];print(i.get("peers",0), i.get("server_state",""))' 2>/dev/null || echo "0 unreachable"
}

$COMPOSE up -d ledger-postgres validator-01 validator-02 validator-03 api-01

i=0
while [ "$i" -lt 12 ]; do
  i=$((i + 1))
  sleep 10
  all_ok=1
  n=0
  for port in $RPC_PORTS; do
    n=$((n + 1))
    node=$(echo $NODES | cut -d' ' -f$n)
    set -- $(peers_of "$port")
    if [ "$1" -lt 2 ] && [ "$node" != "api-01" ] || { [ "$node" = "api-01" ] && [ "$1" -lt 1 ]; }; then
      all_ok=0
      echo "restarting $node (peers=$1 state=$2)"
      docker restart "$PROJECT-$node" >/dev/null
      sleep 25
    fi
  done
  [ "$all_ok" = 1 ] && break
done

python3 "$DIR/xrpl_health.py"
$COMPOSE up -d ledger-migrations ledger-provision ledger
echo "waiting for the ledger service readiness"
j=0
until [ "$(curl -s -m 5 http://127.0.0.1:8094/actuator/health/readiness | grep -c UP)" = 1 ] || [ "$j" -ge 30 ]; do
  j=$((j + 1)); sleep 5
done
curl -s -m 5 http://127.0.0.1:8094/actuator/health/readiness
echo
