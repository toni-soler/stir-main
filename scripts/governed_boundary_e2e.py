"""Governed-state boundary against the real topology. An attacker controlling the ordinary STIR container reaches
the governed verifier directly, bypassing the routing layer. Every forged request must fail and leave the
effective constitution unchanged; the legitimate bootstrap by a tenant administrator must succeed.
Routing is not authorization: the verifier has to reject these by itself. Disposable tenants only.
"""
import base64
import json
import re
import subprocess
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from community_references_e2e import fixtures
from market_integrity_e2e import BOOTSTRAP, digest, rawkey, sign
from multitenant import sql
from smoke import ROOT, request

PROJECT = 'stir-claude-mvp'
COMPOSE = ['docker', 'compose', '-p', PROJECT, '-f', str(ROOT / 'compose.yml'), '-f', str(ROOT / 'compose.release.yml')]
VERIFIER = 'http://governed-verifier:8096'


def compose(*args, input_text=None):
    return subprocess.run(COMPOSE + list(args), input=input_text, text=True, capture_output=True, cwd=ROOT)


def attack(path, body, token=None):
    """A request issued from inside the ordinary runtime container (the compromised-backend model)."""
    args = ['exec', '-T', 'stir', 'wget', '-S', '-q', '-O', '-', '--header=Content-Type: application/json']
    if token:
        args.append(f'--header=Authorization: Bearer {token}')
    args += ['--post-data', json.dumps(body), VERIFIER + path]
    result = compose(*args)
    statuses = re.findall(r'HTTP/1\.1 (\d{3})', result.stderr)
    return int(statuses[-1]) if statuses else 0


def expect_rejected(name, path, body, token=None, expected=None):
    status = attack(path, body, token)
    assert expected is not None, name
    assert status == expected, f'{name}: expected exactly {expected}, got {status}'
    print(f'PASS: {name} -> {status}')


def effective_constitution(tenant):
    return sql(f"select coalesce(string_agg(digest_sha256, ',' order by version), '') from stir.market_constitution where tenant_id='{tenant}';")


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def main():
    tenant, base, ana, pedro, carlos, bea, binding = fixtures()
    community = binding['communityId']
    other_tenant = sql("select tenant_id from idax_core.tenant where code like 'stir-ref-b-%' order by created_at desc limit 1;")
    api = f'/api/stir/tenants/{tenant}/references/governance'
    authority = str(uuid.uuid4())

    keys = [Ed25519PrivateKey.generate() for _ in range(7)]
    guardian = Ed25519PrivateKey.generate()
    credentials = [str(uuid.uuid4()) for _ in keys]
    controllers = [str(uuid.uuid4()) for _ in keys]
    guardian_id = str(uuid.uuid4())
    seats = [dict(ordinal=i + 1, controllerId=controllers[i], credentialId=credentials[i], publicKey=rawkey(keys[i])) for i in range(7)]
    constitution = dict(schema='STIR-MARKET-CONSTITUTION-1', provenanceRequired=True, historyImmutable=True,
                        independenceChecksRequired=True, concentrationChecksRequired=True, minimumObservationFloor=5,
                        minimumParticipantFloor=6, maximumParticipantShareCeiling='0.50', guardianMayGovern=False,
                        constitutionalThreshold=7)
    bootstrap = dict(format='STIR-SEVEN-KEYS-BOOTSTRAP-1', tenantId=tenant, communityId=community, authorityId=authority,
                     seats=seats, guardianCredentialId=guardian_id, guardianPublicKey=rawkey(guardian),
                     constitutionDigest=digest(constitution))
    creation = dict(authorityId=authority, communityId=community,
                    seats=[{**s, 'possessionSignature': sign(keys[i], BOOTSTRAP, bootstrap)} for i, s in enumerate(seats)],
                    guardianCredentialId=guardian_id, guardianPublicKey=rawkey(guardian),
                    guardianPossessionSignature=sign(guardian, BOOTSTRAP, bootstrap))

    assert effective_constitution(tenant) == '', 'fresh tenant must start with no constitution'

    # Legitimate path: routed through Caddy, authorised by the tenant administrator (ana).
    view = request(api + '/bootstrap', 'POST', creation, ana['accessToken'])
    assert view['threshold'] == 7 and len(view['seats']) == 7
    legit = effective_constitution(tenant)
    assert legit != '', 'legitimate bootstrap must persist an effective constitution'
    print('PASS: legitimate 7/7 bootstrap by tenant administrator, persisted through the verifier')

    # Forged caller claims sent straight to the verifier.
    expect_rejected('forged actorId: a plain user asserts the administrator as actor', api + '/bootstrap',
                    {**creation, 'actorId': str(ana['user']['id'])}, pedro['accessToken'], expected=400)
    expect_rejected('valid JWT of a plain tenant user without bootstrap authority', api + '/bootstrap',
                    creation, pedro['accessToken'], expected=403)
    expect_rejected('forged tenant: administrator token used on another tenant path',
                    f'/api/stir/tenants/{other_tenant}/references/governance/bootstrap', creation, ana['accessToken'], expected=403)
    expect_rejected('unsigned token (alg none)', api + '/bootstrap', creation,
                    b64(json.dumps({'alg': 'none'}).encode()) + '.' + b64(json.dumps({'sub': 'ana'}).encode()) + '.', expected=401)
    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    header = b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())
    claims = b64(json.dumps({'sub': 'ana', 'userId': str(ana['user']['id']), 'tenantId': tenant,
                             'roles': ['admin'], 'iss': 'idax-local'}).encode())
    forged_sig = b64(rogue.sign(f'{header}.{claims}'.encode(), padding.PKCS1v15(), hashes.SHA256()))
    expect_rejected('JWT signed with a key the verifier does not trust', api + '/bootstrap', creation,
                    f'{header}.{claims}.{forged_sig}', expected=401)
    expect_rejected('fabricated verification flags (sevenKeysVerified, authorized, final)', api + '/bootstrap',
                    {**creation, 'sevenKeysVerified': True, 'authorized': True, 'final': True}, ana['accessToken'], expected=400)
    expect_rejected('six seats instead of seven', api + '/bootstrap',
                    {**creation, 'seats': creation['seats'][:6]}, ana['accessToken'], expected=400)
    expect_rejected('duplicate seat ordinal', api + '/bootstrap',
                    {**creation, 'seats': creation['seats'][:6] + [creation['seats'][0]]}, ana['accessToken'], expected=409)
    expect_rejected('replay of a completed bootstrap (no replacement path)', api + '/bootstrap',
                    creation, ana['accessToken'], expected=409)

    assert effective_constitution(tenant) == legit, 'a forged request changed the effective constitution'
    print('PASS: no forged request created, replaced or altered effective constitutional state')

    # The ordinary runtime's database identity must not write constitutional state or assume the verifier role.
    attacks = [
        f"update stir.constitutional_seat set public_key = repeat('Z',43) where tenant_id='{tenant}';",
        f"insert into stir.market_constitution values (gen_random_uuid(),'{tenant}','{community}','{authority}',9,'{{}}','{'a' * 64}',now());",
        'set role idax_governed_verifier;',
    ]
    for statement in attacks:
        out = compose('exec', '-T', '-e', 'PGPASSWORD=' + (ROOT / '.local/secrets/runtime_password').read_text().strip(),
                      'postgres', 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-h', 'postgres', '-U', 'idax_backend', '-d', 'idax',
                      input_text=statement)
        assert out.returncode != 0 and 'permission denied' in out.stderr, f'idax_backend was not denied: {statement[:70]} :: {out.stderr[:200]}'
        print(f'PASS: idax_backend denied: {statement[:70]}')

    # Credential isolation: the ordinary runtime container holds no verifier secret and no verifier configuration.
    secrets = compose('exec', '-T', 'stir', 'sh', '-c', 'ls /run/secrets').stdout
    assert 'governed_verifier_password' not in secrets, 'verifier password is mounted into the ordinary runtime'
    env = compose('exec', '-T', 'stir', 'sh', '-c', 'env').stdout.lower()
    assert 'governed' not in env, 'verifier configuration leaked into the ordinary runtime environment'
    print('PASS: ordinary runtime container has no verifier secret and no verifier environment')
    print('GOVERNED BOUNDARY E2E: PASS')


if __name__ == '__main__':
    main()
