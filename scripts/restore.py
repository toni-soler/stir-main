"""Restore a backup made by backup.py (section 25 of the 0.4 brief). Stops the app services,
restores PostgreSQL from the dump and the object storage volume from the tar archive, then
restarts. Destructive: replaces current data with the backup's. Confirms before proceeding unless
--yes is passed.

Usage: python scripts/restore.py .local/backups/<timestamp> [--yes]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
    if '--yes' not in sys.argv:
        answer = input(f'This REPLACES all current data with the backup at {backup_dir}. Type "yes" to continue: ')
        if answer.strip() != 'yes': raise SystemExit('Aborted.')

    run('docker', 'compose', 'stop', 'stir', 'ostris', 'ledger', 'shell', 'stir-ui', 'ostris-ui', 'ledger-ui', 'proxy')

    password = (ROOT / '.local/secrets/postgres_password').read_text().strip()
    run('docker', 'compose', 'exec', '-T', '-e', f'PGPASSWORD={password}', 'postgres',
        'psql', '-U', 'postgres', '-d', 'idax', '-c',
        'drop schema if exists idax_core, idax_shell, stir, ostris, idax_ledger cascade;')
    with dump.open('rb') as f:
        subprocess.run(['docker', 'compose', 'exec', '-T', '-e', f'PGPASSWORD={password}', 'postgres',
                         'psql', '-U', 'postgres', '-d', 'idax'], check=True, cwd=ROOT, stdin=f)
    print('PostgreSQL restored.')

    run('docker', 'run', '--rm', '-v', 'stir-dev_minio_data:/data', '-v', f'{backup_dir}:/backup',
        'alpine', 'sh', '-c', 'rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar xzf /backup/minio_data.tar.gz -C /data')
    print('Object storage restored.')

    run('docker', 'compose', 'up', '-d')
    # MinIO was left running throughout the raw volume-level restore above, so its in-memory view
    # of the bucket is stale - without this it serves a transient "Resource requested is
    # unreadable, please reduce your request rate" error on the very first reads after restore.
    run('docker', 'compose', 'restart', 'minio')
    print('Restarted. Wait for health checks, then verify.')

if __name__ == '__main__': main()
