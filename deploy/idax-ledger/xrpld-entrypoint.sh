#!/bin/sh
# Entrypoint for the IDAX private XRPL nodes: bounded debug log, then xrpld exactly as the image starts it.
#
# Why: xrpld appends to one debug.log forever. On DEV three validators wrote about 118 MB/day each even at
# `log_level warning` (the consensus heartbeat is logged at NFO regardless), which together with the ledger data filled
# the disk on 2026-10-28. xrpld stops itself below 512 MB free; every node then persisted a different last validated
# ledger and the network could not resume on its own (deploy/idax-ledger/README.md, "Disk exhaustion incident").
#
# What: a background loop rotates debug.log once per UTC day, and earlier if it passes XRPLD_LOG_MAX_MB, by renaming the
# file and asking the local node to reopen it (`logrotate` admin command, loopback only). Rotated files are compressed
# and removed after XRPLD_LOG_KEEP_DAYS days, so a node never holds more than that many days of logs.
set -eu

LOG_DIR=${XRPLD_LOG_DIR:-/var/log/xrpld}
KEEP_DAYS=${XRPLD_LOG_KEEP_DAYS:-7}
MAX_MB=${XRPLD_LOG_MAX_MB:-512}
CHECK_SECONDS=${XRPLD_LOG_CHECK_SECONDS:-300}
CONF=/etc/xrpld/xrpld.cfg

rotate() {
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    mv "$LOG_DIR/debug.log" "$LOG_DIR/debug.log.$stamp" || return 0
    if /usr/bin/xrpld --conf "$CONF" logrotate >/dev/null 2>&1; then
        gzip -f "$LOG_DIR/debug.log.$stamp" || true
    else
        # The node did not reopen its log (still starting, or stopping): it keeps writing to the renamed file, so put
        # the name back and try again on the next pass rather than compressing a file that is still being written.
        mv "$LOG_DIR/debug.log.$stamp" "$LOG_DIR/debug.log" || true
    fi
}

rotation_loop() {
    day=$(date -u +%Y%m%d)
    while sleep "$CHECK_SECONDS"; do
        today=$(date -u +%Y%m%d)
        if [ -s "$LOG_DIR/debug.log" ]; then
            size_mb=$(( $(wc -c < "$LOG_DIR/debug.log") / 1048576 ))
            if [ "$today" != "$day" ] || [ "$size_mb" -ge "$MAX_MB" ]; then rotate; fi
        fi
        day=$today
        # -mtime +N matches files older than N+1 whole days, so KEEP_DAYS-1 keeps at most KEEP_DAYS days.
        find "$LOG_DIR" -name 'debug.log.*' -mtime +$((KEEP_DAYS - 1)) -exec rm -f {} + 2>/dev/null || true
    done
}

rotation_loop &
exec /usr/bin/xrpld --conf "$CONF" "$@"
