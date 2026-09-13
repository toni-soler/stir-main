"""Local backup of the development/pilot stack (section 25 of the 0.4 brief): PostgreSQL (every
schema: idax_core, idax_shell, stir, ostris, idax_ledger) via pg_dump, and the object storage
volume (Listing photos, avatars) via a plain tar of the Docker volume. Writes one timestamped
directory under .local/backups/. Private browser signing keys are never part of this - they never
leave the browser, so there is nothing server-side to back up for them.

Usage: python scripts/backup.py
"""
import datetime
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def run(*args, **kwargs):
    subprocess.run(args, check=True, cwd=ROOT, **kwargs)

def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = ROOT / '.local/backups' / stamp
    out.mkdir(parents=True, exist_ok=True)

    password = (ROOT / '.local/secrets/postgres_password').read_text().strip()
    with (out / 'postgres.sql').open('wb') as f:
        subprocess.run(
            ['docker', 'compose', 'exec', '-T', '-e', f'PGPASSWORD={password}', 'postgres',
             'pg_dump', '-U', 'postgres', '-d', 'idax'],
            check=True, cwd=ROOT, stdout=f)
    print('PostgreSQL dump:', out / 'postgres.sql')

    run('docker', 'run', '--rm',
        '-v', 'stir-dev_minio_data:/data:ro',
        '-v', f'{out}:/backup',
        'alpine', 'tar', 'czf', '/backup/minio_data.tar.gz', '-C', '/data', '.')
    print('Object storage archive:', out / 'minio_data.tar.gz')

    print(f'Backup complete: {out}')

if __name__ == '__main__': main()
