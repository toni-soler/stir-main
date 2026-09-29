"""Regression matrix for audit_security_status_probe.py's fail-closed contract (P1-R4-001,
FOURTH_REVALIDATION_GOVERNED_STATE_AUDIT_PHASE1.md; P1-R5-001,
FIFTH_REVALIDATION_GOVERNED_STATE_AUDIT_PHASE1.md). Stdlib-only, no external test framework.

Run directly: python scripts/test_audit_security_status_probe.py
"""
import http.server
import json
import pathlib
import subprocess
import sys
import threading
import unittest

import audit_security_status_probe as probe

_SCRIPT_PATH = pathlib.Path(__file__).parent / "audit_security_status_probe.py"


class _CannedHandler(http.server.BaseHTTPRequestHandler):
    """Serves whatever (status_code, body_bytes) the subclass's `response` attribute says, for
    every GET request - one throwaway subclass per test via _serve()."""
    response = (200, b"{}")

    def do_GET(self):
        status, body = type(self).response
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep test output quiet - this is a synthetic local server, not real traffic


def _serve(status: int, body):
    """Starts a one-shot local HTTP server returning (status, body) for any GET. Returns
    (url, stop_callback)."""
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    handler = type("Handler", (_CannedHandler,), {"response": (status, raw)})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{port}/security-status", stop


class SecurityStatusProbeContractTest(unittest.TestCase):
    def _assert_exit(self, status, body, expected_exit):
        url, stop = _serve(status, body)
        try:
            actual = probe.poll_once(url, timeout=2.0)
            self.assertEqual(expected_exit, actual, f"status={status} body={body!r}")
        finally:
            stop()

    # ----- the two coherent, documented payloads -----

    def test_clear_zero_exits_clear(self):
        self._assert_exit(200, json.dumps({"securityState": "CLEAR", "openIncidentCount": 0}), probe.EXIT_CLEAR)

    def test_critical_one_exits_critical(self):
        self._assert_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": 1}), probe.EXIT_CRITICAL)

    def test_critical_two_exits_critical(self):
        self._assert_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": 2}), probe.EXIT_CRITICAL)

    # ----- everything else must fail closed (exit 3), never default to CLEAR -----

    def test_unknown_state_with_incident_count_exits_invalid_not_clear(self):
        # Exactly Codex's own fourth-reaudit reproduction: an unrecognized state must never look
        # like success just because it isn't literally CRITICAL_SECURITY_INCIDENT.
        self._assert_exit(200, json.dumps({"securityState": "UNKNOWN", "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_missing_state_exits_invalid(self):
        self._assert_exit(200, json.dumps({"openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    # ----- P1-R5-001: `state not in VALID_STATES` is hash-set membership, which raises an
    # uncaught TypeError for an unhashable JSON type (list/dict) - escaping the
    # InvalidSecurityStatusResponse catch entirely and crashing with Python's default exit code 1,
    # coincidentally EXIT_CRITICAL, instead of the mandated exit 3. These are the exact
    # reproductions from FIFTH_REVALIDATION_GOVERNED_STATE_AUDIT_PHASE1.md plus the remediation
    # order's required scalar-type variants. -----

    def test_array_state_exits_invalid_not_critical(self):
        self._assert_exit(200, json.dumps({"securityState": [], "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_object_state_exits_invalid_not_critical(self):
        self._assert_exit(200, json.dumps({"securityState": {}, "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_integer_state_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": 1, "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_boolean_state_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": True, "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_null_state_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": None, "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_clear_with_positive_count_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": "CLEAR", "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_critical_with_zero_count_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": 0}), probe.EXIT_INVALID_RESPONSE)

    def test_negative_count_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": -1}), probe.EXIT_INVALID_RESPONSE)

    def test_string_count_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": "1"}), probe.EXIT_INVALID_RESPONSE)

    def test_boolean_count_exits_invalid(self):
        # bool is a subclass of int in Python - this specifically guards against that trap.
        self._assert_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": True}), probe.EXIT_INVALID_RESPONSE)

    def test_null_count_exits_invalid(self):
        self._assert_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": None}), probe.EXIT_INVALID_RESPONSE)

    def test_malformed_json_exits_invalid(self):
        self._assert_exit(200, "{not valid json", probe.EXIT_INVALID_RESPONSE)

    def test_json_array_exits_invalid(self):
        self._assert_exit(200, json.dumps([{"securityState": "CLEAR", "openIncidentCount": 0}]), probe.EXIT_INVALID_RESPONSE)

    def test_empty_body_exits_invalid(self):
        self._assert_exit(200, "", probe.EXIT_INVALID_RESPONSE)

    # ----- transport/endpoint failures are a THIRD, distinct bucket - never CLEAR, never exit 3 -----

    def test_connection_refused_exits_unreachable(self):
        actual = probe.poll_once("http://127.0.0.1:1/security-status", timeout=1.0)
        self.assertEqual(probe.EXIT_UNREACHABLE, actual)

    def test_non_2xx_http_status_exits_unreachable_not_clear(self):
        # A well-formed CLEAR body behind a 503 must still never read as CLEAR - the endpoint
        # itself failed to serve a status, which is the condition that matters here.
        self._assert_exit(503, json.dumps({"securityState": "CLEAR", "openIncidentCount": 0}), probe.EXIT_UNREACHABLE)


class SecurityStatusProbeCliContractTest(unittest.TestCase):
    """P1-R5-001's own reproduction ran the REAL CLI as a subprocess, not an in-process call - and
    that distinction is exactly why the bug was invisible to a purely in-process test: an uncaught
    TypeError from `state not in VALID_STATES` (unhashable list/dict) propagates out of poll_once()
    and, in-process, would simply fail/error the calling test method - it never gets the chance to
    demonstrate what the OS-visible *process exit code* actually was. Only a real subprocess
    invocation shows the exit code a scheduler/cron/systemd-timer would actually observe (Python's
    interpreter exits 1 by default for an uncaught exception, coincidentally EXIT_CRITICAL - the
    exact false-positive this finding is about). These tests drive the packaged script exactly as
    an external monitor would."""

    def _assert_cli_exit(self, status: int, body: str, expected_exit: int):
        url, stop = _serve(status, body)
        try:
            result = subprocess.run(
                [sys.executable, str(_SCRIPT_PATH), "--url", url, "--timeout", "2"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(expected_exit, result.returncode,
                f"status={status} body={body!r} stdout={result.stdout!r} stderr={result.stderr!r}")
            # An uncaught exception before the fix prints a Python traceback to stderr, not the
            # probe's own diagnostic line - assert there is none, not just that the exit code
            # happens to match by coincidence.
            self.assertNotIn("Traceback", result.stderr, f"the CLI must never crash with an uncaught exception: {result.stderr}")
        finally:
            stop()

    def test_array_state_via_real_cli_exits_invalid_not_critical(self):
        self._assert_cli_exit(200, json.dumps({"securityState": [], "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_object_state_via_real_cli_exits_invalid_not_critical(self):
        self._assert_cli_exit(200, json.dumps({"securityState": {}, "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_unknown_state_via_real_cli_exits_invalid(self):
        # The original P1-R4-001 reproduction, re-driven through the real CLI as a sanity check
        # that this file's in-process tests and the real subprocess agree.
        self._assert_cli_exit(200, json.dumps({"securityState": "UNKNOWN", "openIncidentCount": 1}), probe.EXIT_INVALID_RESPONSE)

    def test_clear_via_real_cli_exits_clear(self):
        self._assert_cli_exit(200, json.dumps({"securityState": "CLEAR", "openIncidentCount": 0}), probe.EXIT_CLEAR)

    def test_critical_via_real_cli_exits_critical(self):
        self._assert_cli_exit(200, json.dumps({"securityState": "CRITICAL_SECURITY_INCIDENT", "openIncidentCount": 1}), probe.EXIT_CRITICAL)


if __name__ == "__main__":
    unittest.main()
