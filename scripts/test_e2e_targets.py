"""Every E2E script reaches the stack through smoke.BASE (STIR_TEST_URL), never through a fixed address.

A script that hardcodes http://localhost:8089 ignores STIR_TEST_URL: run against an isolated stack,
it sends its browser to whatever answers on the shared port instead. Stdlib-only, no stack needed,
same convention as test_audit_security_status_probe.py.

Run directly: python scripts/test_e2e_targets.py
"""
import pathlib
import re
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
# The shared local dev stack's address. Other fixed addresses (documentation examples, in-container probes,
# env-overridable side services) do not decide which STIR stack a script drives.
FIXED = re.compile(r"https?://(?:localhost|127\.0\.0\.1):8089(?!\d)")
# The only places a fixed local address is legitimate: smoke.BASE's own default (the local dev stack),
# a check that the target is local, and the WebAuthn origin the relying party accepts by default.
ALLOWED = {
    ('smoke.py', "BASE=os.environ.get('STIR_TEST_URL','http://localhost:8089')"),
    ('audit_economic_commit_e2e.py', "os.environ.get('STIR_TEST_URL','http://localhost:8089')"),
    ('webauthn_hardware_custody_e2e.py', "ORIGIN=os.environ.get('STIR_WEBAUTHN_ORIGIN','http://localhost:8089')"),
}


class E2ETargets(unittest.TestCase):
    def test_no_script_hardcodes_the_stack_address(self):
        offenders = []
        for path in sorted(SCRIPTS.glob('*.py')):
            if path.name == pathlib.Path(__file__).name:
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if FIXED.search(line) and not any(path.name == name and snippet in line for name, snippet in ALLOWED):
                    offenders.append(f'{path.name}:{number}: {line.strip()}')
        self.assertEqual(offenders, [], 'use smoke.BASE (STIR_TEST_URL) instead of a fixed address')


if __name__ == '__main__':
    unittest.main()
