"""One-command update: pull the latest STIR + sibling repos, re-pin public vendor sources, and
redeploy - the sequence an operator otherwise has to remember and run by hand after every fix
lands upstream (see CHANGELOG.md's "Upstream pin bump" entries for why this exists).

Runs, in order:
  1. `git pull --ff-only` on this repo (stir-main) and every sibling repo present next to it
     (stir-frontend, stir-backend, stir-doc - whichever actually exist as directories).
  2. `python scripts/initialize.py` - re-pins vendor/ to upstream.lock.json's commits (self-heals
     a stale vendor checkout to a newly-bumped pin) and reapplies the reviewed patches.
  3. `python scripts/deploy.py` (or `--prod`) - build, recreate, health check, smoke check,
     automatic rollback to the previous images if either check fails.

Refuses to run with uncommitted local changes in any repo it would pull (never discards work).
Never touches volumes; never runs backup/restore. Safe to re-run - every step is idempotent.

Usage:
  python scripts/update.py            # development compose project
  python scripts/update.py --prod     # production compose project (.local/production must exist)
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD = '--prod' in sys.argv
SIBLINGS = ['stir-frontend', 'stir-backend', 'stir-doc']

def run(*args, cwd=ROOT, env=None):
    subprocess.run(args, cwd=cwd, check=True, env=env)

def capture(*args, cwd=ROOT):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

def pull(repo_dir):
    name = repo_dir.name
    # Untracked files (e.g. stir-doc's manual-usuario/ review output) never conflict with a
    # fast-forward pull - only tracked modifications/staged changes ("??" excluded) can.
    status = capture('git', '-C', str(repo_dir), 'status', '--porcelain')
    dirty = '\n'.join(line for line in status.splitlines() if not line.startswith('??'))
    if dirty:
        raise SystemExit(f'{name} has uncommitted local changes - refusing to pull over them:\n{dirty}\n'
                          f'Commit, stash, or discard them yourself first (this script never does either).')
    branch = capture('git', '-C', str(repo_dir), 'branch', '--show-current')
    if branch != 'main':
        raise SystemExit(f'{name} is on branch "{branch}", not "main" - refusing to guess. Check it out yourself first.')
    print(f'--- git pull: {name} ---')
    run('git', '-C', str(repo_dir), 'pull', '--ff-only', 'origin', 'main')

def read_env(key):
    env_file = ROOT / '.env'
    if not env_file.exists(): return None
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k, _, v = line.partition('=')
        if k.strip() == key: return v.strip()
    return None

def main():
    if PROD and not (ROOT / '.local/production').exists():
        raise SystemExit('--prod was passed but .local/production does not exist on this host - refusing.')

    pull(ROOT)
    for name in SIBLINGS:
        sibling = ROOT.parent / name
        if sibling.is_dir():
            pull(sibling)

    print('--- initialize.py: re-pinning public vendor sources ---')
    run(sys.executable, 'scripts/initialize.py')

    print(f"--- deploy.py{' --prod' if PROD else ''}: build, recreate, health check, smoke check ---")
    deploy_env = dict(os.environ)
    if PROD:
        # smoke.py (invoked by deploy.py) defaults to the dev-only proxy port and the dev
        # bootstrap login - neither exists in production. Derive the real ones from .env so the
        # operator does not have to export them by hand before every run.
        base_url = read_env('STIR_PUBLIC_BASE_URL')
        admin_email = read_env('STIR_ADMIN_EMAIL')
        if not base_url or not admin_email:
            raise SystemExit('--prod needs STIR_PUBLIC_BASE_URL and STIR_ADMIN_EMAIL set in .env for the post-deploy smoke check.')
        deploy_env['STIR_TEST_URL'] = base_url
        deploy_env['STIR_TEST_LOGIN_EMAIL'] = admin_email
    run(sys.executable, 'scripts/deploy.py', *(['--prod'] if PROD else []), env=deploy_env)

    print('Update complete.')

if __name__ == '__main__': main()
