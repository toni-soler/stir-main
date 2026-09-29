"""P1-R3-001/P1-R4-001 (THIRD/FOURTH_REVALIDATION_GOVERNED_STATE_AUDIT_PHASE1.md): a minimal,
independent monitor that polls the audit-verifier's own /security-status endpoint over plain HTTP.

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

FAIL-CLOSED CONTRACT (P1-R4-001 - the fourth independent reaudit found the first version of this
probe defaulted an unrecognized/missing "securityState" to "UNKNOWN" and then fell through to a
generic "anything that isn't literally CRITICAL_SECURITY_INCIDENT counts as CLEAR" branch, so a
payload like {"securityState":"UNKNOWN","openIncidentCount":1} silently exited 0/CLEAR despite
asserting an open incident. Fixed by validating the ENTIRE payload strictly before ever returning
CLEAR or CRITICAL, and by never defaulting a missing/unrecognized field to a value that lets
execution reach a success branch):

    exit 0 = CLEAR                          - securityState=="CLEAR" AND openIncidentCount==0,
                                               nothing else in the body is validated beyond that.
    exit 1 = CRITICAL_SECURITY_INCIDENT     - securityState=="CRITICAL_SECURITY_INCIDENT" AND
                                               openIncidentCount is an integer >= 1.
    exit 2 = UNREACHABLE                    - transport-level failure (DNS, connection refused,
                                               timeout) OR a non-2xx HTTP status (the endpoint was
                                               reached but could not or would not serve a status -
                                               a service/endpoint failure, never treated as CLEAR).
    exit 3 = INVALID_SECURITY_STATUS_RESPONSE - the endpoint responded 2xx, but the body is not one
                                               of the exactly two coherent payloads above: missing/
                                               null/non-string/unrecognized securityState (including
                                               a JSON array or object, per P1-R5-001), missing/null/
                                               non-integer/boolean/negative openIncidentCount, a count
                                               inconsistent with the declared state (CLEAR with
                                               count>0, CRITICAL with count<1), malformed JSON, a
                                               JSON value that isn't an object, or an empty body.
                                               This is the fail-closed branch: an unrecognized
                                               response NEVER exits 0, even if some future state
                                               name superficially looks harmless.

exit 2 (UNREACHABLE) and exit 3 (INVALID_SECURITY_STATUS_RESPONSE) are deliberately distinct and
must never be conflated: "the endpoint could not be reached at all" is a different operational
condition from "the endpoint responded but said something this probe does not recognize as
coherent" - conflating them would hide a live but broken/drifted endpoint behind the same signal as
a fully down one.

Fingerprint fields (latestIncidentId/latestIncidentDedupKey/latestIncidentReasonCode/
latestIncidentDetectedAt) are printed for diagnostics when present but are NOT required for
validity - the documented /security-status contract does not declare them mandatory, and this probe
does not invent a new requirement for them (P1-R4-001's remediation order was explicit: do not
require what the contract does not already require).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

EXIT_CLEAR = 0
EXIT_CRITICAL = 1
EXIT_UNREACHABLE = 2
EXIT_INVALID_RESPONSE = 3

VALID_STATES = {"CLEAR", "CRITICAL_SECURITY_INCIDENT"}


class InvalidSecurityStatusResponse(Exception):
    """The response was reached successfully (HTTP 2xx) but its body is not one of the two
    documented, internally coherent security-status payloads. Fail closed - never default to
    CLEAR for anything this probe does not explicitly recognize."""


def parse_security_status(raw_body: bytes) -> dict:
    """Strict validator for the /security-status contract. Returns the parsed payload dict only
    when it is exactly one of the two coherent, documented shapes; raises
    InvalidSecurityStatusResponse for everything else. Never uses a permissive default (no
    `payload.get("securityState", "UNKNOWN")` followed by a generic success branch) - a field
    that is missing, null, or of the wrong type fails closed immediately."""
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidSecurityStatusResponse(f"body is not valid UTF-8: {exc}") from exc
    if not text.strip():
        raise InvalidSecurityStatusResponse("body is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidSecurityStatusResponse(f"body is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise InvalidSecurityStatusResponse(f"body is not a JSON object (got {type(payload).__name__})")

    if "securityState" not in payload:
        raise InvalidSecurityStatusResponse("missing 'securityState' field")
    state = payload["securityState"]
    # P1-R5-001 (FIFTH_REVALIDATION_GOVERNED_STATE_AUDIT_PHASE1.md): `state not in VALID_STATES`
    # performs hash-set membership - a JSON array or object decodes to a Python list/dict, which is
    # unhashable and raises TypeError there, escaping the InvalidSecurityStatusResponse catch in
    # poll_once() entirely. That uncaught TypeError crashed the interpreter, which exits 1 by
    # default - coincidentally EXIT_CRITICAL, so a malformed/adversarial response could look like a
    # real incident instead of failing closed as exit 3. Must check the type BEFORE any membership
    # test against the (hashable-only) VALID_STATES set.
    if not isinstance(state, str):
        raise InvalidSecurityStatusResponse(f"securityState is not a string: {state!r}")
    if state not in VALID_STATES:
        raise InvalidSecurityStatusResponse(f"unrecognized securityState: {state!r}")

    if "openIncidentCount" not in payload:
        raise InvalidSecurityStatusResponse("missing 'openIncidentCount' field")
    count = payload["openIncidentCount"]
    # bool is a subclass of int in Python (isinstance(True, int) is True) - reject it explicitly
    # before the int check, or {"openIncidentCount": true} would silently pass as count=1.
    if isinstance(count, bool) or not isinstance(count, int):
        raise InvalidSecurityStatusResponse(f"openIncidentCount is not an integer: {count!r}")
    if count < 0:
        raise InvalidSecurityStatusResponse(f"openIncidentCount is negative: {count!r}")

    if state == "CLEAR" and count != 0:
        raise InvalidSecurityStatusResponse(f"securityState=CLEAR but openIncidentCount={count} (must be exactly 0)")
    if state == "CRITICAL_SECURITY_INCIDENT" and count < 1:
        raise InvalidSecurityStatusResponse(f"securityState=CRITICAL_SECURITY_INCIDENT but openIncidentCount={count} (must be >=1)")

    return payload


def poll_once(url: str, timeout: float) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw_body = resp.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # Covers DNS failure, connection refused, timeout, AND any non-2xx HTTP status
        # (urllib raises HTTPError, a URLError subclass, for those) - all classified as the
        # endpoint/service having failed to produce a usable response, never as CLEAR.
        print(f"AUDIT-PROBE UNREACHABLE url={url} error={exc}", flush=True)
        return EXIT_UNREACHABLE

    try:
        payload = parse_security_status(raw_body)
    except InvalidSecurityStatusResponse as exc:
        print(f"AUDIT-PROBE INVALID_SECURITY_STATUS_RESPONSE url={url} reason={exc} raw={raw_body[:500]!r}", flush=True)
        return EXIT_INVALID_RESPONSE

    state = payload["securityState"]
    count = payload["openIncidentCount"]
    latest_dedup = payload.get("latestIncidentDedupKey")
    latest_reason = payload.get("latestIncidentReasonCode")
    latest_detected = payload.get("latestIncidentDetectedAt")
    db_time = payload.get("dbTime")
    print(
        f"AUDIT-PROBE state={state} openIncidentCount={count} "
        f"latestIncidentDedupKey={latest_dedup} latestIncidentReasonCode={latest_reason} "
        f"latestIncidentDetectedAt={latest_detected} dbTime={db_time}",
        flush=True,
    )
    return EXIT_CRITICAL if state == "CRITICAL_SECURITY_INCIDENT" else EXIT_CLEAR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="Full URL of the auditor's /security-status endpoint")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-request HTTP timeout in seconds")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between polls when --loop is set")
    parser.add_argument("--loop", action="store_true", help="Poll repeatedly instead of exiting after one check")
    args = parser.parse_args()

    if not args.loop:
        return poll_once(args.url, args.timeout)

    last_code = EXIT_CLEAR
    while True:
        last_code = poll_once(args.url, args.timeout)
        time.sleep(args.interval)
    return last_code  # unreachable, kept for clarity


if __name__ == "__main__":
    sys.exit(main())
