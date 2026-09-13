"""ACTUALLY EXECUTES a real local backup/restore round trip (section 25/34 of the 0.4 brief):
create a marker Listing with a real uploaded photo -> backup.py -> destroy BOTH the Postgres and
object-storage Docker volumes outright -> bring the stack back up empty -> verify the marker data
is genuinely gone -> restore.py -> verify the marker Listing, its title, and its photo's exact
bytes are all back. Private browser signing keys are never involved: they never leave the browser,
so there is nothing of theirs to lose or restore here.

This is destructive to the local dev stack's current data by design - it is meant to prove the
backup/restore mechanism actually works, not just that the scripts exist. Only run it against the
isolated local/dev stir-main compose stack, never anything else.

Usage: python scripts/backup_restore_e2e.py
"""
import hashlib
import subprocess
import sys
import time
import requests
from pathlib import Path
from smoke import ROOT, request
from multitenant import sql

PHOTO = ROOT / '.local/test-assets/test-photo.jpg'
PHOTO_SHA256 = hashlib.sha256(PHOTO.read_bytes()).hexdigest()

def run(*args, **kwargs):
    subprocess.run(args, check=True, cwd=ROOT, **kwargs)

def docker_compose(*args, **kwargs):
    run('docker', 'compose', *args, **kwargs)

def create_marker():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    import uuid, secrets
    suffix = uuid.uuid4().hex[:8]
    tenant_name = 'STIR Backup Restore E2E ' + suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-backup-restore-{suffix}', '{tenant_name}', 'active', false);")
    role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'stir_backup_restore_' + suffix, 'name': 'backup restore e2e', 'description': 'Isolated development fixture', 'enabled': True}, admin, expected=(200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/' + role['id'] + '/permissions', 'PUT', ['stir.listings.read', 'stir.listings.create', 'stir.listings.update'], admin)
    email = f'marker-{suffix}@stir.test'
    password = secrets.token_urlsafe(24)
    request(f'/api/shell/v1/tenants/{tenant}/users', 'POST', {'email': email, 'displayName': 'Marker', 'authProvider': 'local', 'subject': email, 'password': password, 'role': 'member', 'enabled': True}, admin, expected=(200, 201))
    session = request('/api/shell/v1/auth/login', 'POST', {'email': email, 'password': password})
    request(f'/api/shell/v1/tenants/{tenant}/roles/users/' + session['user']['id'], 'PUT', {'roleIds': [role['id']]}, admin, 204)
    token = session['accessToken']

    title = 'Backup restore marker ' + suffix
    base = f'http://localhost:8089/api/stir/tenants/{tenant}'
    headers = {'Authorization': 'Bearer ' + token}
    listing = requests.post(base + '/listings', json={
        'direction': 'OFFER', 'title': title, 'description': 'Proves the backup/restore round trip',
        'category': 'general', 'resourceKind': 'physical'}, headers=headers, timeout=30)
    assert listing.status_code == 201, listing.text
    listing_id = listing.json()['id']

    with PHOTO.open('rb') as f:
        upload = requests.post(base + f'/listings/{listing_id}/photos', files={'file': ('test-photo.jpg', f, 'image/jpeg')}, headers=headers, timeout=30)
    assert upload.status_code == 201, upload.text
    attachment_id = upload.json()['id']

    return {'tenant': tenant, 'tenant_name': tenant_name, 'listing_id': listing_id, 'title': title,
            'attachment_id': attachment_id, 'email': email, 'password': password}

def marker_present_in_db(marker):
    listing_count = sql(f"select count(*) from stir.listing where id = '{marker['listing_id']}' and title = '{marker['title']}';")
    attachment_count = sql(f"select count(*) from stir.attachment where id = '{marker['attachment_id']}' and status = 'ACTIVE';")
    return int(listing_count) == 1 and int(attachment_count) == 1

def wait_healthy(service, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(['docker', 'compose', 'ps', service, '--format', '{{.Status}}'], check=True, cwd=ROOT, capture_output=True, text=True).stdout
        if 'healthy' in out:
            return
        time.sleep(5)
    raise SystemExit(f'{service} did not become healthy within {timeout}s: {out!r}')

def main():
    print('--- Step 1: creating marker data (Listing + real uploaded photo) ---')
    marker = create_marker()
    assert marker_present_in_db(marker), 'marker not found in database right after creation'
    print(f"Marker created: tenant={marker['tenant']} listing={marker['listing_id']} title={marker['title']!r}")

    print('--- Step 2: backup.py ---')
    backups_before = set((ROOT / '.local/backups').glob('*')) if (ROOT / '.local/backups').exists() else set()
    run(sys.executable, str(ROOT / 'scripts/backup.py'))
    backups_after = set((ROOT / '.local/backups').glob('*'))
    new_backups = backups_after - backups_before
    assert len(new_backups) == 1, f'expected exactly one new backup directory, got {new_backups}'
    backup_dir = new_backups.pop()
    dump = backup_dir / 'postgres.sql'
    archive = backup_dir / 'minio_data.tar.gz'
    assert dump.exists() and dump.stat().st_size > 0, 'postgres.sql missing or empty'
    assert archive.exists() and archive.stat().st_size > 0, 'minio_data.tar.gz missing or empty'
    assert marker['title'] in dump.read_text(encoding='utf-8', errors='ignore'), "the marker Listing's title must actually be inside the SQL dump"
    print(f'Backup at {backup_dir}: postgres.sql={dump.stat().st_size}B minio_data.tar.gz={archive.stat().st_size}B')
    print("Confirmed the marker title is present in the raw SQL dump.")

    print('--- Step 3: destroying BOTH volumes outright ---')
    docker_compose('down')
    run('docker', 'volume', 'rm', 'stir-dev_postgres_data', 'stir-dev_minio_data')
    print('Volumes removed:', subprocess.run(['docker', 'volume', 'ls'], cwd=ROOT, capture_output=True, text=True).stdout.count('stir-dev'), 'stir-dev volumes remain (expect 0)')

    print('--- Step 4: bringing the stack back up EMPTY ---')
    docker_compose('up', '-d')
    wait_healthy('postgres')
    wait_healthy('stir', timeout=300)
    try:
        gone = sql(f"select count(*) from idax_core.tenant where tenant_id = '{marker['tenant']}';")
        assert int(gone) == 0, 'marker tenant must be GONE after destroying the volume and starting fresh'
        print('Confirmed: marker tenant no longer exists in the freshly-migrated, empty database.')
    except AssertionError:
        raise
    except Exception as e:
        raise SystemExit(f'Could not confirm data was actually destroyed: {e}')

    print('--- Step 5: restore.py ---')
    run(sys.executable, str(ROOT / 'scripts/restore.py'), str(backup_dir), '--yes')
    docker_compose('up', '-d')
    wait_healthy('postgres')
    wait_healthy('minio')
    wait_healthy('stir', timeout=300)

    print('--- Step 6: verifying the marker Listing, title and photo are all back ---')
    assert marker_present_in_db(marker), 'marker Listing/attachment row missing after restore'
    print('Confirmed: marker Listing row and attachment row are back in Postgres.')

    session = request('/api/shell/v1/auth/login', 'POST', {'email': marker['email'], 'password': marker['password']})
    token = session['accessToken']
    base = f"http://localhost:8089/api/stir/tenants/{marker['tenant']}"
    listing = requests.get(base + f"/listings/{marker['listing_id']}", headers={'Authorization': 'Bearer ' + token}, timeout=30)
    assert listing.status_code == 200 and listing.json()['title'] == marker['title'], 'restored Listing must be reachable through the real API with its original title'
    # A short retry: MinIO can serve a transient "unreadable, reduce your request rate" error for
    # the first few reads immediately after restore.py's own minio restart settles in.
    photo = None
    for attempt in range(6):
        photo = requests.get(base + f"/attachments/{marker['attachment_id']}/content", headers={'Authorization': 'Bearer ' + token}, timeout=30)
        if photo.status_code == 200: break
        time.sleep(5)
    assert photo.status_code == 200, f'restored photo must be readable from object storage, got {photo.status_code}: {photo.text[:300]}'
    restored_sha256 = hashlib.sha256(photo.content).hexdigest()
    assert restored_sha256 == PHOTO_SHA256, f'restored photo bytes must be BIT-IDENTICAL to the original upload (expected {PHOTO_SHA256}, got {restored_sha256})'
    print('Confirmed: restored Listing reachable via the real API, and its photo bytes are bit-identical to the original upload.')

    print('PASS: backup.py produced a real dump+archive containing the marker data; destroying both '
          "Docker volumes genuinely erased it; restore.py brought back the exact same Listing, its "
          'title, and its photo (verified by SHA-256) through the real running application.')
    print('STIR 0.4 PUBLIC PILOT MVP (backup/restore): PASS')

if __name__ == '__main__': main()
