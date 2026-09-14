"""`stir status`: a one-shot operational summary an operator can run without knowing any module's
internals (section 22/36 of the 0.5 brief). Reports per-service container health, disk space
(host + Docker's own reclaimable space), and reachability of Postgres and object storage - the two
things that can be "up" as containers but still unavailable to the app. Read-only; never mutates
anything.

Usage: python scripts/status.py
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_PRODUCTION = (ROOT / '.local/production').exists()
COMPOSE = ['docker', 'compose'] + (['-f', 'compose.yml', '-f', 'compose.production.yml'] if IS_PRODUCTION else [])

def run(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)

def main():
    print(f"STIR status - {'PRODUCTION' if IS_PRODUCTION else 'development'} compose project")
    print('=' * 60)

    ps = run(*COMPOSE, 'ps', '--format', '{{.Name}}\t{{.Status}}')
    if ps.returncode:
        print('Could not reach the Docker daemon or compose project:', ps.stderr.strip())
        return
    rows = [line.split('\t', 1) for line in ps.stdout.strip().splitlines() if line.strip()]
    if not rows:
        print('No containers are running for this compose project.')
    for name, status in rows:
        flag = 'OK ' if 'healthy' in status or ('Up' in status and 'starting' not in status and 'unhealthy' not in status) else '!! '
        print(f'{flag}{name:<28} {status}')

    print()
    print('Disk (host filesystem):')
    total, used, free = shutil.disk_usage(str(ROOT))
    print(f'  {free / 1e9:.1f} GB free of {total / 1e9:.1f} GB total ({used / total * 100:.0f}% used)')

    df = run('docker', 'system', 'df')
    if df.returncode == 0:
        print('Docker disk usage:')
        for line in df.stdout.strip().splitlines():
            print(' ', line)

    print()
    password = None
    secret = ROOT / '.local/secrets/postgres_password'
    if secret.exists():
        password = secret.read_text().strip()
    if password:
        pg = run(*COMPOSE, 'exec', '-T', '-e', f'PGPASSWORD={password}', 'postgres',
                 'pg_isready', '-U', 'postgres', '-d', 'idax')
        print('PostgreSQL: ' + ('OK reachable and accepting connections' if pg.returncode == 0 else 'NOT reachable: ' + pg.stdout.strip() + pg.stderr.strip()))
    else:
        print('PostgreSQL: skipped (no .local/secrets/postgres_password on this host)')

    storage = run(*COMPOSE, 'exec', '-T', 'minio', 'curl', '-fsS', 'http://localhost:9000/minio/health/live')
    print('Object storage: ' + ('OK reachable' if storage.returncode == 0 else 'NOT reachable'))

    backups_dir = ROOT / '.local/backups'
    if backups_dir.exists():
        runs = sorted((p.name for p in backups_dir.iterdir() if p.is_dir()))
        print()
        print(f'Backups: {len(runs)} retained, most recent: {runs[-1] if runs else "none"}')
    else:
        print()
        print('Backups: none taken yet (.local/backups does not exist)')

if __name__ == '__main__': main()
