"""Deploy a new version of the stack (section 16/17 of the 0.5 brief): build -> migrate
(automatic - the existing core-migrations/module-migrations/runtime-provision dependency chain
runs on every `up -d`) -> recreate services -> health check -> non-destructive smoke -> rollback
to the previous images if anything is unhealthy or the smoke fails. Never touches volumes.

Usage:
  python scripts/deploy.py            # development compose project
  python scripts/deploy.py --prod     # production compose project (.local/production must exist)
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD = '--prod' in sys.argv
if PROD and not (ROOT / '.local/production').exists():
    raise SystemExit('--prod was passed but .local/production does not exist on this host - refusing.')
COMPOSE = ['docker', 'compose'] + (['-f', 'compose.yml', '-f', 'compose.production.yml'] if PROD else [])
# Only the images this repo actually builds from source - not postgres/minio/caddy, which are
# pulled, versioned upstream images and are not part of "our" rollback surface.
BUILT_SERVICES = ['shell', 'stir', 'stir-ui', 'ostris', 'ostris-ui', 'ledger', 'ledger-ui']

def run(*args, check=True):
    return subprocess.run(args, cwd=ROOT, check=check)

def capture(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout.strip()

def image_name(service):
    return capture(*COMPOSE, 'images', '-q', service)

def tag_current_as_previous():
    tags = {}
    for service in BUILT_SERVICES:
        image_id = image_name(service)
        if not image_id: continue
        previous_tag = f'stir-rollback/{service}:previous'
        run('docker', 'tag', image_id, previous_tag, check=False)
        tags[service] = previous_tag
    return tags

def healthy():
    ps = capture(*COMPOSE, 'ps', '--format', '{{.Name}}\t{{.Status}}')
    rows = [line.split('\t', 1) for line in ps.splitlines() if line.strip()]
    if not rows: return False
    return all('starting' not in status and 'unhealthy' not in status and 'Exit' not in status for _, status in rows)

def wait_healthy(timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if healthy(): return True
        time.sleep(5)
    return False

def rollback(previous_tags):
    print('!!! Deploy unhealthy - rolling back to the previous images.')
    for service, tag in previous_tags.items():
        run('docker', 'tag', tag, f'stir-main-{service}' if not PROD else f'stir-prod-{service}', check=False)
    run(*COMPOSE, 'up', '-d', '--no-build')
    if wait_healthy():
        print('Rollback complete - previous version is healthy again.')
    else:
        print('Rollback did not reach healthy either - manual intervention required. See `python scripts/status.py`.')

def main():
    print(f"Deploying to {'PRODUCTION' if PROD else 'development'}...")
    previous_tags = tag_current_as_previous()

    print('Building...')
    run(*COMPOSE, 'build')

    print('Recreating services (migrations run automatically as part of the dependency chain)...')
    run(*COMPOSE, 'up', '-d')

    print('Waiting for health...')
    if not wait_healthy():
        rollback(previous_tags)
        raise SystemExit('Deploy FAILED: services did not reach healthy. Rolled back where possible.')

    print('Healthy. Running a non-destructive smoke check...')
    smoke = subprocess.run([sys.executable, 'scripts/smoke.py'], cwd=ROOT)
    if smoke.returncode != 0:
        rollback(previous_tags)
        raise SystemExit('Deploy FAILED: post-deploy smoke check failed. Rolled back where possible.')

    print('Deploy PASSED: services healthy, smoke check green.')

if __name__ == '__main__': main()
