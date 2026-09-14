"""Local backup of the stack (section 25 of the 0.4 brief; automated + retention for the 0.5
beta - section 14): PostgreSQL (every schema: idax_core, idax_shell, stir, ostris, idax_ledger)
via pg_dump, and the object storage volume (Listing photos, avatars) via a plain tar of the Docker
volume. Writes one timestamped directory under .local/backups/, then prunes old ones down to
--keep (default 14 - a "keep the last N runs" retention is enough for a beta; it is not meant to
be enterprise-grade). Private browser signing keys are never part of this - they never leave the
browser, so there is nothing server-side to back up for them.

Usage: python scripts/backup.py [--keep N]
Cron (daily at 03:15): 15 3 * * * cd /path/to/stir-main && python3 scripts/backup.py >> .local/backups/backup.log 2>&1
"""
import argparse
import datetime
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_PRODUCTION = (ROOT / '.local/production').exists()
COMPOSE = ['docker', 'compose'] + (['-f', 'compose.yml', '-f', 'compose.production.yml'] if IS_PRODUCTION else [])

def run(*args, **kwargs):
    subprocess.run(args, check=True, cwd=ROOT, **kwargs)

def prune(keep):
    backups_dir = ROOT / '.local/backups'
    runs = sorted((p for p in backups_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
    for stale in runs[:-keep] if keep > 0 else []:
        shutil.rmtree(stale)
        print('Pruned old backup:', stale)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', type=int, default=14, help='how many most-recent backup runs to retain (0 = keep all)')
    args = parser.parse_args()

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = ROOT / '.local/backups' / stamp
    out.mkdir(parents=True, exist_ok=True)

    password = (ROOT / '.local/secrets/postgres_password').read_text().strip()
    with (out / 'postgres.sql').open('wb') as f:
        subprocess.run(
            COMPOSE + ['exec', '-T', '-e', f'PGPASSWORD={password}', 'postgres',
             'pg_dump', '-U', 'postgres', '-d', 'idax'],
            check=True, cwd=ROOT, stdout=f)
    print('PostgreSQL dump:', out / 'postgres.sql')

    volume_prefix = 'stir-prod' if IS_PRODUCTION else 'stir-dev'
    run('docker', 'run', '--rm',
        '-v', f'{volume_prefix}_minio_data:/data:ro',
        '-v', f'{out}:/backup',
        'alpine', 'tar', 'czf', '/backup/minio_data.tar.gz', '-C', '/data', '.')
    print('Object storage archive:', out / 'minio_data.tar.gz')

    print(f'Backup complete: {out}')
    prune(args.keep)

if __name__ == '__main__': main()
