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
            raise SystemExit(f'{name}: wrong upstream commit; use a clean pinned checkout')
    from prepare_shell import prepare
    prepare(ROOT)
    core=vendor/'idax-core-runtime'
    if subprocess.check_output(['git','-C',str(core),'status','--porcelain']).strip(): raise SystemExit('Unexpected Core vendor modifications')
    secret_dir = ROOT / '.local/secrets'
    secret_dir.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt': secret_dir.chmod(0o700)
    for name in ['postgres_password', 'bootstrap_token', 'login_password', 'runtime_password']:
        p = secret_dir / name
        if not p.exists(): p.write_text(secrets.token_urlsafe(36), encoding='utf-8')
        if os.name != 'nt': p.chmod(0o600)
    openssl = shutil.which('openssl')
    if not openssl and os.name == 'nt':
        candidate = Path(os.environ.get('ProgramFiles', '')) / 'Git/usr/bin/openssl.exe'
        if candidate.exists(): openssl = str(candidate)
    if not openssl: raise SystemExit('OpenSSL is required for local RSA keys')
    private = secret_dir / 'jwt_private_key'
    public = secret_dir / 'jwt_public_key'
    if not private.exists(): run(openssl, 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', str(private))
    if not public.exists(): run(openssl, 'pkey', '-in', str(private), '-pubout', '-out', str(public))
    if os.name != 'nt': private.chmod(0o600); public.chmod(0o644)
    print('Public inputs pinned; secrets preserved/generated. Login: admin@stir.test; password in .local/secrets/login_password')

if __name__ == '__main__': main()
