"""Negotiation/execution integrity E2E - STIR's own HTTP APIs only, no distribution involved.

Covers three generic STIR problems found by an external re-audit (stir-doc/NEGOTIATION_EXECUTION_INTEGRITY.md):

1. Economic direction at accept: a listing owner could flip OFFER<->WANTED after the counterparty
   had seen an offer, and accept() derived payer/payee from the *current* listing direction even
   though offerId and negotiation.version were unchanged.
2. Agreement <-> trade <-> osTRIS correlation: with the local stir.trade row altered (SQL fault
   injection - requires write access to STIR's own database, NOT a break of osTRIS signatures),
   STIR committed and reported as Agreement A's execution an osTRIS transaction authorized for
   Agreement B.
3. Concurrency: simultaneous opens/counters surfaced raw unique-constraint violations as 5xx
   instead of a deterministic conflict.

Each check prints FIXED or REPRODUCED. Exit code 1 if anything is REPRODUCED, so the same script is
the pre-fix reproduction and the post-fix regression. Isolated development fixtures only: needs
STIR_COMPOSE_PROJECT (never stir-dev, see multitenant.py) and STIR_TEST_URL. Never run against a
production deployment.
"""
import concurrent.futures
import json
import secrets
import sys
import uuid
from economic_exchange_e2e import PERMISSIONS, activate_economic, sign_authorization
from multitenant import sql
from smoke import ROOT, request

RESULTS = []


def outcome(name, fixed, detail=''):
    RESULTS.append((name, fixed))
    print(('FIXED       ' if fixed else 'REPRODUCED  ') + name + (f'  [{detail}]' if detail else ''), flush=True)


def call(path, method='GET', body=None, token=None):
    """(status, body) without raising - several checks need the status itself."""
    try:
        return 200, request(path, method, body, token, expected=(200, 201))
    except AssertionError as error:
        text = str(error)
        status = int(text.split('got ')[1].split(';')[0]) if 'got ' in text else 0
        return status, text


def user(admin, tenant, role_id, label, suffix):
    email, password = f'{label}-{suffix}@stir.test', secrets.token_urlsafe(32)
    base = f'/api/shell/v1/tenants/{tenant}'
    request(base + '/users', 'POST', {'email': email, 'displayName': label, 'authProvider': 'local', 'subject': email,
                                      'password': password, 'role': 'member', 'enabled': True}, admin, expected=(200, 201))
    first = request('/api/shell/v1/auth/login', 'POST', {'email': email, 'password': password})
    request(base + '/roles/users/' + first['user']['id'], 'PUT', {'roleIds': [role_id]}, admin, 204)
    return request('/api/shell/v1/auth/login', 'POST', {'email': email, 'password': password})


def parties(agreement):
    """(payer, payee) as frozen into the Agreement snapshot - the authoritative record."""
    frozen = json.loads(agreement['snapshot']['canonicalJson'])
    return frozen['payerUserId'], frozen['payeeUserId']


def scalar(statement):
    return sql(statement).splitlines()[-1].strip()


class Market:
    def __init__(self):
        admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
                        'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
        self.suffix = uuid.uuid4().hex[:8]
        self.tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-nei-{self.suffix}', 'STIR integrity {self.suffix}', 'active', false);")
        role = request(f'/api/shell/v1/tenants/{self.tenant}/roles', 'POST', {'key': 'stir_nei_' + self.suffix, 'name': 'STIR integrity E2E',
                       'description': 'Isolated development fixture', 'enabled': True}, admin, expected=(200, 201))
        request(f'/api/shell/v1/tenants/{self.tenant}/roles/{role["id"]}/permissions', 'PUT', PERMISSIONS, admin)
        self.ana, self.pedro = (user(admin, self.tenant, role['id'], name, self.suffix) for name in ('ana', 'pedro'))
        self.base = f'/api/stir/tenants/{self.tenant}'
        for person, name in ((self.ana, 'Ana'), (self.pedro, 'Pedro')):
            request(self.base + '/participants/me', 'PUT', {'displayName': f'{name} {self.suffix}'}, person['accessToken'])
        request(self.base + '/economic/marketplace/bootstrap', 'POST', {'communityName': 'Integrity ' + self.suffix, 'unitCode': 'CRD', 'unitScale': 0},
                self.ana['accessToken'])
        self.ana_key, self.pedro_key = activate_economic(self.base, self.ana), activate_economic(self.base, self.pedro)

    def token(self, person):
        return person['accessToken']

    def listing(self, direction, title):
        return request(self.base + '/listings', 'POST', {'direction': direction, 'title': f'{title} {self.suffix}', 'description': 'Integrity fixture',
                                                          'category': 'general', 'resourceKind': 'service'}, self.ana['accessToken'], 201)

    def open(self, listing, amount, person=None):
        return request(self.base + f"/listings/{listing['id']}/offers", 'POST', offer(amount), (person or self.pedro)['accessToken'], 201)

    def counter(self, negotiation, amount, person=None):
        return request(self.base + f"/negotiations/{negotiation['id']}/offers", 'POST', {**offer(amount), 'expectedVersion': negotiation['version']},
                       (person or self.ana)['accessToken'])

    def accept_body(self, negotiation, **extra):
        return {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version'], **extra}

    def agreement(self, amount):
        """Pedro buys Ana's OFFER at `amount`; returns the accepted Agreement."""
        negotiation = self.open(self.listing('OFFER', 'Basket'), amount)
        return request(self.base + f"/negotiations/{negotiation['id']}/accept", 'POST', self.accept_body(negotiation), self.ana['accessToken'])

    def sign(self, agreement_id, person, key):
        private_key, _, credential_id = key
        payload = request(self.base + f'/agreements/{agreement_id}/trade/signing-payload', token=person['accessToken'])
        request(self.base + f'/agreements/{agreement_id}/trade/authorizations', 'POST',
                {'credentialId': credential_id, 'signatureBase64url': sign_authorization(private_key, payload['authorizationPayload'])}, person['accessToken'])


def offer(amount):
    return {'message': f'{amount} credits', 'quantity': None, 'unitLabel': None, 'proposedAmount': str(amount), 'proposedUnitRef': None, 'terms': None}


def direction_flip(m):
    print('\n== 1. Economic direction cannot change under an unchanged offer/version')
    listing = m.listing('WANTED', 'Need a ladder')          # Ana wants it: Ana pays, Pedro provides
    negotiation = m.open(listing, 30)
    countered = m.counter(negotiation, 30)                  # Ana's counter, for Pedro to accept
    ana_pays = {'payer': m.ana['user']['id'], 'payee': m.pedro['user']['id']}
    detail = request(m.base + f"/negotiations/{countered['id']}", token=m.pedro['accessToken'])
    outcome('1a. the negotiation tells the acceptor who pays and who receives',
            detail.get('payerUserId') == ana_pays['payer'] and detail.get('payeeUserId') == ana_pays['payee'], f"payer={detail.get('payerUserId')}")
    current = request(m.base + f"/listings/{listing['id']}", token=m.ana['accessToken'])
    status, _ = call(m.base + f"/listings/{listing['id']}", 'PUT', {key: current[key] for key in ('title', 'description', 'category', 'resourceKind', 'version')}
                     | {'direction': 'OFFER'}, m.ana['accessToken'])
    outcome('1b. the owner cannot flip OFFER/WANTED while a negotiation is open on the listing', status == 409, f'HTTP {status}')
    if status != 409:
        # Pre-fix behaviour: the flip went through. Does accept now silently reverse who pays?
        status, body = call(m.base + f"/negotiations/{countered['id']}/accept", 'POST', m.accept_body(countered), m.pedro['accessToken'])
        reversed_parties = status == 200 and parties(body)[0] == m.pedro['user']['id']
        outcome('1c. accept never reverses the previewed payer/payee', not reversed_parties,
                'payer became the acceptor with unchanged offerId/version' if reversed_parties else f'HTTP {status}')
        return
    # A direction already changed before this fix (simulated here by a direct SQL write, as no API path remains)
    # must not reinterpret the open negotiation either.
    sql(f"update stir.listing set direction='OFFER' where id='{listing['id']}';")
    status, _ = call(m.base + f"/negotiations/{countered['id']}/accept", 'POST', m.accept_body(countered), m.pedro['accessToken'])
    outcome('1c. accept refuses when the listing direction differs from the one the negotiation started under (legacy data)', status == 409, f'HTTP {status}')
    sql(f"update stir.listing set direction='WANTED' where id='{listing['id']}';")
    wrong = m.accept_body(countered, expectedPayerUserId=m.pedro['user']['id'], expectedPayeeUserId=m.ana['user']['id'])
    status, _ = call(m.base + f"/negotiations/{countered['id']}/accept", 'POST', wrong, m.pedro['accessToken'])
    outcome('1d. accept bound to a previewed payer/payee refuses a different one', status == 409, f'HTTP {status}')
    status, body = call(m.base + f"/negotiations/{countered['id']}/accept", 'POST',
                        m.accept_body(countered, expectedPayerUserId=ana_pays['payer'], expectedPayeeUserId=ana_pays['payee']), m.pedro['accessToken'])
    outcome('1e. accept with the previewed payer/payee freezes exactly those parties',
            status == 200 and parties(body) == (ana_pays['payer'], ana_pays['payee']), f'HTTP {status}')


def trade_substitution(m, swap_digest):
    label = 'transaction id and digest' if swap_digest else 'transaction id only'
    print(f'\n== 2. Agreement A cannot be executed with B\'s osTRIS transaction ({label} swapped in stir.trade by SQL)')
    a, b = m.agreement(25), m.agreement(25)                 # same parties, same amount, same unit
    for agreement in (a, b):
        request(m.base + f"/agreements/{agreement['id']}/trade/activate", 'POST', {}, m.ana['accessToken'])
    m.sign(b['id'], m.pedro, m.pedro_key)
    m.sign(b['id'], m.ana, m.ana_key)
    tx_b, digest_b = (scalar(f"select {column} from stir.trade where agreement_id='{b['id']}';") for column in ('transaction_id', 'contractual_metadata_digest'))
    # Fault injection: requires write access to STIR's database. osTRIS signatures are untouched.
    setting = f", contractual_metadata_digest='{digest_b}'" if swap_digest else ''
    sql(f"begin; update stir.trade set transaction_id='{uuid.uuid4()}' where agreement_id='{b['id']}';"
        f" update stir.trade set transaction_id='{tx_b}'{setting} where agreement_id='{a['id']}'; commit;")
    status, _ = call(m.base + f"/agreements/{a['id']}/trade/signing-payload", token=m.pedro['accessToken'])
    outcome(f'2a ({label}). signing-payload for A refuses B\'s transaction', status == 409, f'HTTP {status}')
    status, body = call(m.base + f"/agreements/{a['id']}/trade/commit", 'POST', {}, m.ana['accessToken'])
    committed = status == 200 and body.get('executionState') == 'COMMITTED'
    outcome(f'2b ({label}). commit for A refuses B\'s transaction', not committed,
            'A reported COMMITTED with B\'s transaction' if committed else f'HTTP {status}')
    status, body = call(m.base + f"/agreements/{a['id']}/trade/sync", 'POST', {}, m.ana['accessToken'])
    synced = status == 200 and body.get('executionState') == 'COMMITTED'
    outcome(f'2c ({label}). sync for A never reports B\'s transaction as A\'s execution', not synced and not committed, f'HTTP {status}')
    phase = scalar(f"select economic_phase from stir.agreement where id='{a['id']}';")
    outcome(f'2d ({label}). Agreement A is not marked COMMITTED', phase != 'COMMITTED', phase)


def race(calls):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(lambda c: call(*c), calls))


def concurrency(m):
    print('\n== 3. Simultaneous requests end in one success and deterministic conflicts')
    listing = m.listing('OFFER', 'Popular')
    results = race([(m.base + f"/listings/{listing['id']}/offers", 'POST', offer(10 + i), m.pedro['accessToken']) for i in range(6)])
    statuses = [status for status, _ in results]
    rows = scalar(f"select count(*) from stir.negotiation where listing_id='{listing['id']}';")
    outcome('3a. simultaneous opens: one negotiation, the rest 409, no 5xx',
            sorted(statuses).count(200) == 1 and all(s in (200, 409) for s in statuses) and rows == '1', f'{sorted(statuses)} negotiations={rows}')
    opened = next(body for status, body in results if status == 200)
    negotiation = request(m.base + f"/negotiations/{opened['id']}", token=m.pedro['accessToken'])
    statuses = [status for status, _ in race([(m.base + f"/negotiations/{negotiation['id']}/offers", 'POST', {**offer(20 + i), 'expectedVersion': negotiation['version']},
                      m.ana['accessToken']) for i in range(6)])]
    offers = scalar(f"select count(*) || '/' || count(*) filter (where status='PROPOSED') from stir.offer where negotiation_id='{negotiation['id']}';")
    outcome('3b. simultaneous counteroffers: one new offer, the rest 409, no 5xx, no partial offer',
            sorted(statuses).count(200) == 1 and all(s in (200, 409) for s in statuses) and offers == '2/1', f'{sorted(statuses)} offers/proposed={offers}')
    negotiation = request(m.base + f"/negotiations/{negotiation['id']}", token=m.pedro['accessToken'])
    statuses = [status for status, _ in race([(m.base + f"/negotiations/{negotiation['id']}/accept", 'POST', m.accept_body(negotiation),
                                                m.pedro['accessToken']) for _ in range(6)])]
    agreements = scalar(f"select count(*) from stir.agreement where negotiation_id='{negotiation['id']}';")
    outcome('3c. simultaneous accepts: one Agreement, the rest 409, no 5xx',
            sorted(statuses).count(200) == 1 and all(s in (200, 409) for s in statuses) and agreements == '1', f'{sorted(statuses)} agreements={agreements}')


def main():
    m = Market()
    print(f'STIR negotiation/execution integrity E2E - tenant {m.tenant}')
    direction_flip(m)
    trade_substitution(m, swap_digest=True)
    trade_substitution(m, swap_digest=False)
    concurrency(m)
    reproduced = [name for name, fixed in RESULTS if not fixed]
    print(f'\n{len(RESULTS) - len(reproduced)}/{len(RESULTS)} FIXED, {len(reproduced)} REPRODUCED')
    sys.exit(1 if reproduced else 0)


if __name__ == '__main__':
    main()
