#!/bin/sh
# Starts the IDAX private XRPL network and then the IDAX Ledger service, and verifies that validation is progressing.
#
# The nodes are started by xrpl_start.py, most advanced node first, because a simultaneous start of nodes that stopped
# at different validated ledgers does not resume validation (README.md, "Disk exhaustion incident"). Run this after a
# host reboot as well: Docker does not restart these containers by itself (compose.xrpl.yml, restart policy).
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT=${IDAX_LEDGER_PROJECT:-idax-ledger-dev}
python3 "$DIR/xrpl_start.py"
docker compose -p "$PROJECT" -f "$DIR/compose.xrpl.yml" -f "$DIR/compose.ledger.yml" up -d
python3 "$DIR/xrpl_health.py"
