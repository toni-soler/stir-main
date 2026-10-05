"""Regression tests for the INT-P1-002 harness fix (multitenant.py's compose-project guards).
Stdlib-only, no external test framework - matches test_audit_security_status_probe.py's
convention.

Deliberately requires a REAL stir-dev stack and a REAL isolated stack running simultaneously -
this is not incidental, it is the point. The bug this guards against (a bare `docker compose`
invocation silently resolving against stir-dev because compose.yml hardcodes `name: stir-dev`)
only reproduces when stir-dev actually exists to be silently routed to. A test environment where
stir-dev doesn't exist could pass trivially without proving anything about the actual failure
mode - so the coexistence tests below skip (not pass) when stir-dev isn't reachable, rather than
silently asserting less than they claim to.

Run directly: STIR_COMPOSE_PROJECT=<isolated project name> python scripts/test_multitenant_compose_safety.py
"""
import os
import subprocess
import unittest

import multitenant


class ComposeProjectRequiredTests(unittest.TestCase):
    """No stir-dev or isolated stack needed - pure guard-function behavior."""

    def test_unset_project_raises(self):
        saved = os.environ.pop('STIR_COMPOSE_PROJECT', None)
        try:
            with self.assertRaises(multitenant.ComposeSafetyError):
                multitenant.compose_project()
        finally:
            if saved is not None:
                os.environ['STIR_COMPOSE_PROJECT'] = saved

    def test_stir_dev_explicitly_refused_even_if_requested(self):
        saved = os.environ.get('STIR_COMPOSE_PROJECT')
        os.environ['STIR_COMPOSE_PROJECT'] = 'stir-dev'
        try:
            with self.assertRaises(multitenant.ComposeSafetyError):
                multitenant.compose_project()
        finally:
            if saved is not None:
                os.environ['STIR_COMPOSE_PROJECT'] = saved
            else:
                os.environ.pop('STIR_COMPOSE_PROJECT', None)

    def test_blank_project_raises(self):
        saved = os.environ.get('STIR_COMPOSE_PROJECT')
        os.environ['STIR_COMPOSE_PROJECT'] = '   '
        try:
            with self.assertRaises(multitenant.ComposeSafetyError):
                multitenant.compose_project()
        finally:
            if saved is not None:
                os.environ['STIR_COMPOSE_PROJECT'] = saved
            else:
                os.environ.pop('STIR_COMPOSE_PROJECT', None)


class CoexistenceAndRoutingTests(unittest.TestCase):
    """Test 1 (coexistence): proves stir-dev and an isolated stack running at the same time don't
    interfere, and that commands land on the isolated one. Test 2 (positive no-touch proof): for
    every check below, the assertion is made against a SEPARATE docker inspection than the one
    the guard function itself performs internally - the point is to catch a regression in the
    guard, not to just re-run the guard and trust its own verdict."""

    @classmethod
    def setUpClass(cls):
        cls.project = os.environ.get('STIR_COMPOSE_PROJECT')
        if not cls.project:
            raise unittest.SkipTest(
                'STIR_COMPOSE_PROJECT must be set to a real, running isolated project for this test class')
        check = subprocess.run(
            ['docker', 'ps', '--filter', 'label=com.docker.compose.project=stir-dev', '--format', '{{.Names}}'],
            capture_output=True, text=True)
        cls.stir_dev_containers = [n for n in check.stdout.splitlines() if n.strip()]
        if not cls.stir_dev_containers:
            raise unittest.SkipTest(
                'stir-dev does not appear to be running right now - cannot prove coexistence '
                '(this is a weaker claim than what this test class asserts, so it skips rather '
                'than passing trivially)')

    def test_stir_dev_is_genuinely_running_concurrently(self):
        """Part of the coexistence proof: stir-dev must be a real, live stack, not just a stale
        label somewhere."""
        self.assertGreater(len(self.stir_dev_containers), 0)
        self.assertTrue(
            any('postgres' in n for n in self.stir_dev_containers),
            f'expected a stir-dev postgres container among {self.stir_dev_containers}')

    def test_isolated_project_has_its_own_running_postgres_distinct_from_stir_dev(self):
        """Coexistence proof, second half: the isolated project is ALSO genuinely running, at
        the same time, as a physically different container."""
        isolated = subprocess.run(
            ['docker', 'compose', '-p', self.project, 'ps', '-q', 'postgres'],
            capture_output=True, text=True).stdout.strip()
        self.assertTrue(isolated, f'no running postgres container for project {self.project}')
        stir_dev_pg = subprocess.run(
            ['docker', 'ps', '--filter', 'label=com.docker.compose.project=stir-dev',
             '--filter', 'name=postgres', '--format', '{{.ID}}'],
            capture_output=True, text=True).stdout.strip()
        if stir_dev_pg:
            self.assertNotEqual(
                isolated[:12], stir_dev_pg[:12],
                'the isolated project resolved to the SAME physical container as stir-dev')

    def test_verify_isolated_container_resolves_correctly_per_independent_docker_inspect(self):
        """Positive no-touch proof: call the real guard function, then independently re-verify
        its result with a SEPARATE docker command, rather than trusting the guard's own
        assertion."""
        container_id = multitenant.verify_isolated_container(self.project, 'postgres')
        label = subprocess.run(
            ['docker', 'inspect', container_id, '--format',
             '{{index .Config.Labels "com.docker.compose.project"}}'],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(label, self.project)
        self.assertNotEqual(label, 'stir-dev')

    def test_sql_executes_against_the_isolated_project_end_to_end(self):
        """Exercises the real, full sql() call path (not just the guard function in isolation)
        and confirms it succeeds - meaning verify_isolated_container() fired internally for this
        real invocation and passed against the isolated project, not stir-dev."""
        result = multitenant.sql("select current_database();")
        self.assertEqual(result, 'idax')

    def test_no_container_id_overlap_between_isolated_project_and_stir_dev(self):
        """Broadest positive check: enumerate every container in both projects independently (a
        third docker command, distinct from the two used above) and assert zero overlap."""
        isolated_ids = set(
            x.strip()[:12] for x in
            subprocess.run(['docker', 'compose', '-p', self.project, 'ps', '-q'],
                            capture_output=True, text=True).stdout.splitlines() if x.strip())
        stir_dev_ids = set(
            subprocess.run(
                ['docker', 'ps', '--filter', 'label=com.docker.compose.project=stir-dev', '--format', '{{.ID}}'],
                capture_output=True, text=True).stdout.split())
        self.assertGreater(len(isolated_ids), 0)
        self.assertGreater(len(stir_dev_ids), 0)
        overlap = isolated_ids & stir_dev_ids
        self.assertEqual(overlap, set(),
            f'project {self.project} and stir-dev share container ID(s): {overlap}')


if __name__ == '__main__':
    unittest.main()
