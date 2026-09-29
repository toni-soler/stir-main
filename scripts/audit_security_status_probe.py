"""P1-R3-001 (THIRD_REVALIDATION_GOVERNED_STATE_AUDIT_PHASE1.md): a minimal, independent monitor
that polls the audit-verifier's own /security-status endpoint over plain HTTP.

Deliberately does NOT:
  - use idax_app or any IDAX Core credential;
  - need any constitutional/Seven Keys secret;
  - govern, acknowledge, resolve, or otherwise modify a security_incident row;
  - depend on any CRITICAL log line ever having been printed by the auditor process it polls -
    /security-status queries stir_audit.security_incident directly on every request, so this probe
    observes the durable, canonical state even if the auditor crashed before logging anything.

This is a demonstration that a process entirely separate from the one that created an incident can
observe it by polling - not a paging/notification system. No email, Slack, or webhook here; see
VALIDATION_GOVERNED_STATE_AUDIT_MVP.md's P1-R3-001 section for the documented (not yet implemented)
future outbox/at-least-once-delivery pattern this would feed into.

Usage:
    python scripts/audit_security_status_probe.py --url http://127.0.0.1:9090/security-status
    python scripts/audit_security_status_probe.py --url ... --interval 5 --loop

Exit code: 0 when the probe successfully reached the endpoint and the state is CLEAR; 1 when it
reached the endpoint and the state is CRITICAL_SECURITY_INCIDENT (so this script is usable directly
as a cron/systemd-timer check without extra wrapping); 2 when the endpoint could not be reached at
all (a genuinely different operational condition - see the module docstring above and the
THIRD_REVALIDATION finding: "monitor cannot reach security endpoint" must be treated and alerted on
separately from "reached it and it says CLEAR").
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def poll_once(url: str, timeout: float) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(f"AUDIT-PROBE UNREACHABLE url={url} error={exc}", flush=True)
        return 2

    state = body.get("securityState", "UNKNOWN")
    count = body.get("openIncidentCount", "?")
    latest = body.get("latestIncidentDedupKey")
    detected = body.get("latestIncidentDetectedAt")
    db_time = body.get("dbTime")
    print(
        f"AUDIT-PROBE state={state} openIncidentCount={count} "
        f"latestIncidentDedupKey={latest} latestIncidentDetectedAt={detected} dbTime={db_time}",
        flush=True,
    )
    return 1 if state == "CRITICAL_SECURITY_INCIDENT" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Full URL of the auditor's /security-status endpoint")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-request HTTP timeout in seconds")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between polls when --loop is set")
    parser.add_argument("--loop", action="store_true", help="Poll repeatedly instead of exiting after one check")
    args = parser.parse_args()

    if not args.loop:
        return poll_once(args.url, args.timeout)

    last_code = 0
    while True:
        last_code = poll_once(args.url, args.timeout)
        time.sleep(args.interval)
    return last_code  # unreachable, kept for clarity


if __name__ == "__main__":
    sys.exit(main())
