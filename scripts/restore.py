"""Restore a backup made by backup.py (section 25 of the 0.4 brief). Stops the app services,
restores PostgreSQL from the dump and the object storage volume from the tar archive, then
restarts. Destructive: replaces current data with the backup's. Confirms before proceeding.

In production (.local/production marker present) --yes is NOT enough on its own - the operator
must additionally type the exact compose project name, so a copy-pasted "--yes" from a dev
runbook can never silently wipe live data (section 35 of the 0.5 brief: production must never be
torn down by an unattended/scripted "yes").

Usage: python scripts/restore.py .local/backups/<timestamp> [--yes]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_PRODUCTION = (ROOT / '.local/production').exists()
COMPOSE = ['docker', 'compose'] + (['-f', 'compose.yml', '-f', 'compose.production.yml'] if IS_PRODUCTION else [])
VOLUME_PREFIX = 'stir-prod' if IS_PRODUCTION else 'stir-dev'

def run(*args, **kwargs):
    subprocess.run(args, check=True, cwd=ROOT, **kwargs)

def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python scripts/restore.py <backup-directory> [--yes]')
    backup_dir = Path(sys.argv[1])
    if not backup_dir.is_absolute(): backup_dir = ROOT / backup_dir
    dump = backup_dir / 'postgres.sql'
    archive = backup_dir / 'minio_data.tar.gz'
    if not dump.exists() or not archive.exists():
        raise SystemExit(f'{backup_dir} is missing postgres.sql or minio_data.tar.gz')

    if IS_PRODUCTION:
        print('*** THIS IS THE PRODUCTION DEPLOYMENT (.local/production marker present). ***')
        answer = input(f'This REPLACES ALL LIVE PRODUCTION DATA with the backup at {backup_dir}.\n'
                        f'Type the compose project name (stir-prod) to continue: ')
        if answer.strip() != 'stir-prod': raise SystemExit('Aborted.')
    elif '--yes' not in sys.argv:
        answer = input(f'This REPLACES all current data with the backup at {backup_dir}. Type "yes" to continue: ')
        if answer.strip() != 'yes': raise SystemExit('Aborted.')

    run(*COMPOSE, 'stop', 'stir', 'ostris', 'ledger', 'shell', 'stir-ui', 'ostris-ui', 'ledger-ui', 'proxy')

    password = (ROOT / '.local/secrets/postgres_password').read_text().strip()
    run(*COMPOSE, 'exec', '-T', '-e', f'PGPASSWORD={password}', 'postgres',
        'psql', '-U', 'postgres', '-d', 'idax', '-c',
        'drop schema if exists idax_core, idax_shell, stir, ostris, idax_ledger cascade;')
    with dump.open('rb') as f:
        subprocess.run(COMPOSE + ['exec', '-T', '-e', f'PGPASSWORD={password}', 'postgres',
                         'psql', '-U', 'postgres', '-d', 'idax'], check=True, cwd=ROOT, stdin=f)
    print('PostgreSQL restored.')

    run('docker', 'run', '--rm', '-v', f'{VOLUME_PREFIX}_minio_data:/data', '-v', f'{backup_dir}:/backup',
        'alpine', 'sh', '-c', 'rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar xzf /backup/minio_data.tar.gz -C /data')
    print('Object storage restored.')

    run(*COMPOSE, 'up', '-d')
    # MinIO was left running throughout the raw volume-level restore above, so its in-memory view
    # of the bucket is stale - without this it serves a transient "Resource requested is
    # unreadable, please reduce your request rate" error on the very first reads after restore.
    run(*COMPOSE, 'restart', 'minio')
    print('Restarted. Wait for health checks, then verify.')

if __name__ == '__main__': main()
