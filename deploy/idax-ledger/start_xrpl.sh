#!/bin/sh
# Starts the IDAX private XRPL network and the IDAX Ledger service once, then verifies convergence.
#
# No per-node restarts are performed. Peers are configured by fixed subnet address (compose.xrpl.yml), which made the cold
# start converge automatically: see cold_restart_test.sh and the evidence in deploy/idax-ledger/README.md.
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT=${IDAX_LEDGER_PROJECT:-idax-ledger-dev}
docker compose -p "$PROJECT" -f "$DIR/compose.xrpl.yml" -f "$DIR/compose.ledger.yml" up -d
i=0
until python3 "$DIR/xrpl_health.py" >/dev/null 2>&1 || [ "$i" -ge 36 ]; do i=$((i + 1)); sleep 5; done
python3 "$DIR/xrpl_health.py"
