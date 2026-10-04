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
from market_integrity_e2e import BOOTSTRAP, DOMAIN, GUARDIAN, digest, rawkey, sign
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
    return status


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def constitutional_mutation_matrix(tenant, api, community, authority, token, keys, guardian, guardian_id, credentials, constitution):
    """Constitutional WRITE boundary after bootstrap: exact-threshold, forged-evidence, stale, revoked and replay
    cases are attacked directly against the verifier; then one legitimate 7-of-7 mutation must succeed exactly once."""
    def propose(after, fields):
        return request(api + '/' + community + '/proposals', 'POST', dict(proposalId=str(uuid.uuid4()), actionType='AMEND_CONSTITUTION',
            after=after, affectedFields=fields, reason='Explicit community decision', evidenceRefs=['assembly-record-1']), token)

    def sig(p, seat, key, domain=DOMAIN, payload=None, credential=None):
        signed = payload if payload is not None else json.loads(p['payloadJson'])
        return dict(seatOrdinal=seat, credentialId=credential or credentials[seat - 1], signatureBase64url=sign(key, domain, signed))

    def sign_path(p):
        return api + '/proposals/' + p['id'] + '/signatures'

    def activate_path(p):
        return api + '/proposals/' + p['id'] + '/activate'

    empty = dict(guardianSignature=None, newKeyPossessionSignature=None)
    before = effective_constitution(tenant)
    changed = {**constitution, 'independenceChecksRequired': False}
    p = propose(changed, ['independenceChecksRequired'])
    payload = json.loads(p['payloadJson'])
    for seat in range(1, 7):
        request(sign_path(p), 'POST', sig(p, seat, keys[seat - 1]), token)

    expect_rejected('exact 6-of-7 valid signatures cannot activate', activate_path(p), empty, token, expected=409)
    expect_rejected('duplicate signature by an already-signed seat', sign_path(p), sig(p, 1, keys[0]), token, expected=409)
    expect_rejected('wrong community: seat 7 signs a payload naming another community', sign_path(p),
                    sig(p, 7, keys[6], payload={**payload, 'communityId': str(uuid.uuid4())}), token, expected=400)
    expect_rejected('wrong domain: seat 7 signs under the Guardian domain', sign_path(p),
                    sig(p, 7, keys[6], domain=GUARDIAN), token, expected=400)
    expect_rejected('modified payload: seat 7 signs a tampered reason', sign_path(p),
                    sig(p, 7, keys[6], payload={**payload, 'reason': 'tampered'}), token, expected=400)
    expect_rejected('Guardian counted as constitutional seat 7', sign_path(p),
                    dict(seatOrdinal=7, credentialId=guardian_id, signatureBase64url=sign(guardian, DOMAIN, payload)), token, expected=409)
    expect_rejected('unknown credential presented for seat 7', sign_path(p),
                    sig(p, 7, keys[6], credential=str(uuid.uuid4())), token, expected=409)
    assert effective_constitution(tenant) == before, 'a rejected constitutional attack changed effective state'

    events_before = len(request(api + '/' + community + '/events', token=token))
    request(sign_path(p), 'POST', sig(p, 7, keys[6]), token)
    request(activate_path(p), 'POST', empty, token)
    after = effective_constitution(tenant)
    assert before == '' or after.startswith(before + ','), 'legitimate mutation must append exactly one version'
    assert len(after.split(',')) == (len(before.split(',')) if before else 0) + 1, 'effective constitution must change exactly once'
    assert request(api + '/' + community, token=token)['constitutionVersion'] == 2
    print('PASS: legitimate 7-of-7 constitutional mutation after bootstrap, effective state changed exactly once')

    expect_rejected('replay of the executed mutation cannot change state again', activate_path(p), empty, token, expected=409)
    expect_rejected('replay of a signature for the executed proposal', sign_path(p), sig(p, 7, keys[6]), token, expected=409)
    assert effective_constitution(tenant) == after, 'replay changed effective constitutional state'

    events_after = request(api + '/' + community + '/events', token=token)
    assert len(events_after) > events_before, 'the legitimate mutation must leave audit evidence'
    assert request(api + '/' + community + '/audit', token=token)['valid'] is True
    print('PASS: legitimate mutation produced governance events and the audit chain verifies')

    # Stale sequence and a suspended (revoked) credential.
    sequence = request(api + '/' + community, token=token)['nextSequence']
    declared = now_iso()
    suspension = dict(format='STIR-KEY-SUSPENSION-1', tenantId=tenant, communityId=community,
                      authorityId=authority, seat=4, credentialId=credentials[3],
                      reasonCode='KEY_COMPROMISED', evidenceRefs=['incident-1'], sequence=sequence - 1, declaredAt=declared)
    expect_rejected('stale sequence on a Guardian suspension', api + '/' + community + '/emergency-suspensions',
                    dict(seatOrdinal=4, credentialId=credentials[3], expectedSequence=sequence - 1, declaredAt=declared,
                         reasonCode='KEY_COMPROMISED', evidenceRefs=['incident-1'],
                         guardianSignature=sign(guardian, GUARDIAN, suspension)), token, expected=409)
    suspension['sequence'] = sequence
    request(api + '/' + community + '/emergency-suspensions', 'POST', dict(seatOrdinal=4, credentialId=credentials[3],
            expectedSequence=sequence, declaredAt=declared, reasonCode='KEY_COMPROMISED', evidenceRefs=['incident-1'],
            guardianSignature=sign(guardian, GUARDIAN, suspension)), token)
    later = propose({**changed, 'concentrationChecksRequired': False}, ['concentrationChecksRequired'])
    expect_rejected('suspended credential of seat 4 cannot sign', sign_path(later), sig(later, 4, keys[3]), token, expected=409)
    print('COMPLETE: constitutional mutation matrix executed')


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

    constitutional_mutation_matrix(tenant, api, community, authority, ana['accessToken'], keys, guardian, guardian_id,
                                   credentials, constitution)

    # Credential isolation: the ordinary runtime container holds no verifier secret and no verifier configuration.
    secrets = compose('exec', '-T', 'stir', 'sh', '-c', 'ls /run/secrets').stdout
    assert 'governed_verifier_password' not in secrets, 'verifier password is mounted into the ordinary runtime'
    env = compose('exec', '-T', 'stir', 'sh', '-c', 'env').stdout.lower()
    assert 'governed' not in env, 'verifier configuration leaked into the ordinary runtime environment'
    print('PASS: ordinary runtime container has no verifier secret and no verifier environment')
    print('GOVERNED BOUNDARY E2E: PASS')


if __name__ == '__main__':
    main()
