"""Initialize public pinned sources and independent local secrets; never publish."""
from pathlib import Path
import json
import os
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
def run(*args):
    subprocess.run(args, check=True, cwd=ROOT)

def main():
    vendor = ROOT / 'vendor'
    vendor.mkdir(exist_ok=True)
    for name, spec in json.loads((ROOT / 'upstream.lock.json').read_text()).items():
        target = vendor / name
        if not target.exists():
            run('git', '-c', 'credential.helper=', 'clone', '--no-checkout', spec['url'], str(target))
            run('git', '-C', str(target), 'checkout', '--detach', spec['commit'])
        actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
        origin = subprocess.check_output(['git', '-C', str(target), 'remote', 'get-url', 'origin'], text=True).strip()
        if origin != spec['url']: raise SystemExit(f'{name}: unexpected origin')
        if actual != spec['commit']:
            # A previous run pinned this vendor clone to an older commit (or the reviewed
            # patches below left it with uncommitted changes) - upstream.lock.json has since
            # moved on, most often because this repo itself was just `git pull`ed. Self-heal to
            # the newly pinned commit rather than making every operator hand-run the equivalent
            # fetch+checkout after every pin bump. The only uncommitted state a vendor clone ever
            # has is our own reviewed patches (prepare_shell.py, called below) - never real
            # work - so discarding it here is always safe; prepare_shell.py reapplies it fresh
            # against whatever commit we land on.
            run('git', '-C', str(target), 'checkout', '--force', actual)
            run('git', '-C', str(target), 'clean', '-fd')
            run('git', '-C', str(target), 'fetch', 'origin', spec['commit'])
            run('git', '-C', str(target), 'checkout', '--detach', spec['commit'])
            actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != spec['commit']:
            raise SystemExit(f'{name}: wrong upstream commit even after re-fetching; the pinned commit may not exist on {spec["url"]}')
    from prepare_shell import prepare
    prepare(ROOT)
    core=vendor/'idax-core-runtime'
    if subprocess.check_output(['git','-C',str(core),'status','--porcelain']).strip(): raise SystemExit('Unexpected Core vendor modifications')
    secret_dir = ROOT / '.local/secrets'
    secret_dir.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt': secret_dir.chmod(0o700)
    for name in ['postgres_password', 'bootstrap_token', 'login_password', 'runtime_password', 'storage_access_key', 'storage_secret_key']:
        p = secret_dir / name
        if not p.exists(): p.write_text(secrets.token_urlsafe(36), encoding='utf-8')
        # 0644, not 0600: Docker Compose (non-Swarm) `secrets:` bind-mounts this exact host file
        # into /run/secrets/<name>, preserving host permissions - and every service that reads
        # one (shell, stir, ostris, ledger) runs as non-root (USER 10001) per its Dockerfile, a
        # different UID/GID than whichever host user ran this script (root here, but the
        # "non-root operator" flow HOST_PROVISIONING.md recommends works too, since this no
        # longer depends on group/UID matching). Protection is still owner(root)-write-only;
        # the realistic threat model is other host users, not the containers reading it.
        if os.name != 'nt': p.chmod(0o644)
    openssl = shutil.which('openssl')
    if not openssl and os.name == 'nt':
        candidate = Path(os.environ.get('ProgramFiles', '')) / 'Git/usr/bin/openssl.exe'
        if candidate.exists(): openssl = str(candidate)
    if not openssl: raise SystemExit('OpenSSL is required for local RSA keys')
    private = secret_dir / 'jwt_private_key'
    public = secret_dir / 'jwt_public_key'
    if not private.exists(): run(openssl, 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', str(private))
    if not public.exists(): run(openssl, 'pkey', '-in', str(private), '-pubout', '-out', str(public))
    if os.name != 'nt': private.chmod(0o644); public.chmod(0o644)
    print('Public inputs pinned; secrets preserved/generated. Login: admin@stir.test; password in .local/secrets/login_password')

if __name__ == '__main__': main()
