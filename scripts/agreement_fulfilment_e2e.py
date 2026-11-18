"""Agreement fulfilment HTTP E2E (stir-doc/AGREEMENT_FULFILMENT.md), against a running isolated stack.

Ana fixes bicycles and Pedro needs his fixed. Their Agreement becomes obligations: the repair,
each side's tasks and the community-unit exchange. The script checks, through the public HTTP API
only:

1. obligations derived once from the snapshot, with who owes what to whom;
2. a partial delivery, recorded as partial with what is missing, and persisted;
3. a one-sided "not received" is a disagreement; only both sides agreeing makes it "not fulfilled";
4. each party declares only its own side; strangers and a non-party platform superuser see nothing;
5. an amendment names what it replaces, keeps the rest, chains to the snapshot and never rewrites it;
6. every economic execution attempt is persisted with a typed outcome: an exposure refusal is not a
   failure of the agreement, and the economic obligation follows osTRIS's commit, not a declaration;
7. credit capacity is reported as balance minus floor;
8. history on profiles: described facts, opt-in, "not shared" distinct from "no history yet".

Isolated development fixtures only: a fresh tenant created with idax_core.tenant_create and the
public Shell HTTP API, as every other E2E here. Requires STIR_COMPOSE_PROJECT (multitenant.sql).
Never run it against a shared or production stack.
"""
import json
import sys
import uuid
from economic_exchange_e2e import PERMISSIONS, activate_economic, make_user, sign_authorization
from multitenant import sql
from negotiation_execution_integrity_e2e import call
from smoke import ROOT, request

RESULTS = []
FULFIL = PERMISSIONS + ['stir.agreements.fulfil', 'stir.notifications.read']


def check(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  [{detail}]' if detail else ''), flush=True)


def runtime_count(tenant, query):
    out = sql(f"begin; set local role idax_app; select set_config('app.tenant_id','{tenant}',true); {query}; rollback;", runtime=True)
    return int(out.splitlines()[-2])


class Repair:
    def __init__(self):
        login = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})
        self.admin = login['accessToken']
        self.suffix = uuid.uuid4().hex[:8]
        self.tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-fulfil-{self.suffix}', 'STIR fulfilment {self.suffix}', 'active', false);")
        role = request(f'/api/shell/v1/tenants/{self.tenant}/roles', 'POST', {'key': 'stir_fulfil_' + self.suffix, 'name': 'STIR fulfilment E2E',
                       'description': 'Isolated development fixture', 'enabled': True}, self.admin, expected=(200, 201))
        request(f'/api/shell/v1/tenants/{self.tenant}/roles/{role["id"]}/permissions', 'PUT', FULFIL, self.admin)
        self.ana, self.pedro, self.noor = (make_user(self.admin, self.tenant, role['id'], name, self.suffix) for name in ('ana', 'pedro', 'noor'))
        self.base = f'/api/stir/tenants/{self.tenant}'
        for person, name in ((self.ana, 'Ana'), (self.pedro, 'Pedro'), (self.noor, 'Noor')):
            request(self.base + '/participants/me', 'PUT', {'displayName': f'{name} {self.suffix}'}, person['accessToken'])
        request(self.base + '/economic/marketplace/bootstrap', 'POST', {'communityName': 'Fulfilment ' + self.suffix, 'unitCode': 'CRD', 'unitScale': 0}, self.ana['accessToken'])
        self.ana_key, self.pedro_key = activate_economic(self.base, self.ana), activate_economic(self.base, self.pedro)

    def get(self, path, person):
        return request(self.base + path, token=person['accessToken'])

    def agreement(self, title, amount, structured=True):
        """Ana publishes an OFFER; Pedro proposes; Ana accepts. Ana provides, Pedro pays."""
        listing = request(self.base + '/listings', 'POST', {'direction': 'OFFER', 'title': f'{title} {self.suffix}', 'description': 'Fulfilment fixture',
                          'category': 'general', 'resourceKind': 'service'}, self.ana['accessToken'], 201)
        offer = {'message': 'Deal?', 'quantity': None, 'unitLabel': None, 'proposedAmount': str(amount), 'proposedUnitRef': None, 'terms': None}
        if structured:
            offer['structuredTerms'] = {'scope': 'Brakes and gears; new parts not included', 'ownerTasks': 'Test-ride after the repair',
                                        'initiatorTasks': 'Bring the bike to the workshop', 'fulfilmentMode': 'ON_SITE', 'fulfilBy': '2030-01-10',
                                        'openQuestions': None, 'validUntil': None}
        negotiation = request(self.base + f"/listings/{listing['id']}/offers", 'POST', offer, self.pedro['accessToken'], 201)
        return request(self.base + f"/negotiations/{negotiation['id']}/accept", 'POST',
                       {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version']}, self.ana['accessToken'])

    def board(self, agreement, person=None):
        return self.get(f"/agreements/{agreement['id']}/obligations", person or self.ana)

    def declare(self, agreement, obligation, person, type_, note=None, expected=200):
        if expected == 200:
            return request(self.base + f"/agreements/{agreement['id']}/obligations/{obligation['id']}/events", 'POST',
                           {'type': type_, 'note': note}, person['accessToken'])
        status, _ = call(self.base + f"/agreements/{agreement['id']}/obligations/{obligation['id']}/events", 'POST', {'type': type_, 'note': note}, person['accessToken'])
        return status


def of(board, kind):
    return next(o for o in board['obligations'] if o['kind'] == kind and o['status'] != 'SUPERSEDED')


def obligations_and_partial(s):
    print('\n== 1-4. Obligations, partial delivery, disagreement and who may declare')
    instance = request('/api/stir/instance')
    check('0. the instance declares agreementFulfilmentVersion 1', instance.get('agreementFulfilmentVersion') == 1, str(instance.get('agreementFulfilmentVersion')))
    agreement = s.agreement('Bike repair', 30)
    s.repair = agreement
    board = s.board(agreement)
    kinds = sorted(o['kind'] for o in board['obligations'])
    check('1a. the Agreement becomes four obligations', kinds == ['ECONOMIC_EXCHANGE', 'INITIATOR_TASKS', 'OBJECT', 'OWNER_TASKS'], str(kinds))
    ana_id, pedro_id = s.ana['user']['id'], s.pedro['user']['id']
    check('1b. who owes what to whom follows the snapshot',
          of(board, 'OBJECT')['obligorUserId'] == ana_id and of(board, 'INITIATOR_TASKS')['obligorUserId'] == pedro_id
          and of(board, 'ECONOMIC_EXCHANGE')['obligorUserId'] == pedro_id and of(board, 'ECONOMIC_EXCHANGE')['beneficiaryUserId'] == ana_id)
    check('1c. each starts pending, with its date and mode', all(o['status'] == 'PENDING' for o in board['obligations'])
          and of(board, 'OBJECT')['fulfilBy'] == '2030-01-10' and of(board, 'OBJECT')['fulfilmentMode'] == 'ON_SITE')
    s.board(agreement, s.pedro)
    check('1d. derived once, however many readers', runtime_count(s.tenant, f"select count(*) from stir.agreement_obligation where agreement_id='{agreement['id']}'") == 4)

    obj = of(board, 'OBJECT')
    check('2a. a partial declaration needs a note saying what is missing', s.declare(agreement, obj, s.ana, 'PARTIALLY_PERFORMED', ' ', expected=400) == 400)
    s.declare(agreement, obj, s.ana, 'PARTIALLY_PERFORMED', 'Brakes done; the gears need a part')
    after = s.declare(agreement, obj, s.pedro, 'PARTIAL_RECEIVED', 'Brakes work; gears still slip')
    check('2b. the partial delivery is shown as partial', of(after, 'OBJECT')['status'] == 'PARTIAL', of(after, 'OBJECT')['status'])
    check('2c. ... and persisted with both declarations',
          of(s.board(agreement, s.pedro), 'OBJECT')['status'] == 'PARTIAL'
          and runtime_count(s.tenant, f"select count(*) from stir.agreement_obligation_event where obligation_id='{obj['id']}'") == 2)
    inbox = s.get('/notifications', s.pedro)
    notes = [n['type'] for n in (inbox['content'] if isinstance(inbox, dict) else inbox)]
    check('2d. the other party is notified', 'OBLIGATION_UPDATED' in notes, str(notes[:5]))

    tasks = of(board, 'OWNER_TASKS')
    status = of(s.declare(agreement, tasks, s.pedro, 'NOT_RECEIVED'), 'OWNER_TASKS')['status']
    check('3a. a one-sided "not received" is a disagreement, not a failure', status == 'IN_DISAGREEMENT', status)
    status = of(s.declare(agreement, tasks, s.ana, 'NOT_PERFORMED', 'No time for the test ride'), 'OWNER_TASKS')['status']
    check('3b. both sides agreeing makes it "not fulfilled"', status == 'FAILED', status)

    check('4a. a party cannot declare the other side', s.declare(agreement, obj, s.ana, 'CONFORMITY_CONFIRMED', expected=400) == 400)
    check('4b. the economic exchange is not declared by hand', s.declare(agreement, of(board, 'ECONOMIC_EXCHANGE'), s.pedro, 'PERFORMED', expected=409) == 409)
    status, _ = call(s.base + f"/agreements/{agreement['id']}/obligations", token=s.noor['accessToken'])
    check('4c. a stranger in the same community sees nothing', status == 404, f'HTTP {status}')
    status, _ = call(s.base + f"/agreements/{agreement['id']}/obligations/{obj['id']}/events", 'POST', {'type': 'PERFORMED'}, s.admin)
    check('4d. a platform superuser who is not a party cannot declare', status in (403, 404), f'HTTP {status}')


def amendments(s):
    print('\n== 5. Amendments')
    agreement = s.repair
    snapshot_digest = s.get(f"/agreements/{agreement['id']}", s.ana)['snapshot']['digestSha256']
    board = s.board(agreement, s.pedro)
    pedro_tasks = of(board, 'INITIATOR_TASKS')
    proposal = {'baseDigest': board['headDigest'], 'replaces': [pedro_tasks['id']], 'reason': 'Ana will collect the bike instead',
                'obligations': [{'kind': 'OWNER_TASKS', 'description': 'Collect the bike from Pedro', 'fulfilBy': '2030-01-08', 'fulfilmentMode': 'PICKUP'}]}
    proposed = request(s.base + f"/agreements/{agreement['id']}/amendments", 'POST', proposal, s.pedro['accessToken'], 201)
    status, _ = call(s.base + f"/agreements/{agreement['id']}/amendments/{proposed['id']}/accept", 'POST', {}, s.pedro['accessToken'])
    check('5a. the proposer cannot accept their own amendment', status == 409, f'HTTP {status}')
    accepted = request(s.base + f"/agreements/{agreement['id']}/amendments/{proposed['id']}/accept", 'POST', {}, s.ana['accessToken'])
    check('5b. the other party accepts it; it chains to the snapshot',
          accepted['status'] == 'ACCEPTED' and accepted['content']['previousDigest'] == snapshot_digest and accepted['content']['sequence'] == 1)
    board = s.board(agreement)
    replaced = next(o for o in board['obligations'] if o['id'] == pedro_tasks['id'])
    new = [o for o in board['obligations'] if o['origin'] == 'AMENDMENT']
    check('5c. it replaced what it named and kept the rest',
          replaced['status'] == 'SUPERSEDED' and len(new) == 1 and new[0]['obligorUserId'] == s.ana['user']['id'] and new[0]['fulfilBy'] == '2030-01-08'
          and of(board, 'OBJECT')['status'] == 'PARTIAL' and len(board['obligations']) == 5)
    check('5d. the accepted agreement snapshot is never rewritten',
          s.get(f"/agreements/{agreement['id']}", s.ana)['snapshot']['digestSha256'] == snapshot_digest and board['headDigest'] == accepted['digestSha256'])
    status, _ = call(s.base + f"/agreements/{agreement['id']}/amendments", 'POST', proposal, s.pedro['accessToken'])
    check('5e. an amendment on a stale base is refused', status == 409, f'HTTP {status}')
    status, _ = call(s.base + f"/agreements/{agreement['id']}/amendments", 'POST',
                     {'baseDigest': board['headDigest'], 'replaces': [of(board, 'ECONOMIC_EXCHANGE')['id']], 'obligations': [], 'reason': 'Cheaper'}, s.pedro['accessToken'])
    check('5f. the economic exchange cannot be amended here', status == 409, f'HTTP {status}')


def execution(s):
    print('\n== 6-7. Typed execution outcomes and credit')
    agreement = s.repair
    base = s.base + f"/agreements/{agreement['id']}/trade"
    request(base + '/activate', 'POST', {}, s.ana['accessToken'])
    request(base + '/commit', 'POST', {}, s.ana['accessToken'], expected=409)
    for person, key in ((s.pedro, s.pedro_key), (s.ana, s.ana_key)):
        payload = request(base + '/signing-payload', token=person['accessToken'])
        request(base + '/authorizations', 'POST', {'credentialId': key[2], 'signatureBase64url': sign_authorization(key[0], payload['authorizationPayload'])}, person['accessToken'])
    request(base + '/commit', 'POST', {}, s.ana['accessToken'])
    outcomes = [a['outcome'] for a in request(base + '/attempts', token=s.pedro['accessToken'])]
    check('6a. each attempt is recorded with its own typed outcome', outcomes == ['AUTHORISATION_INCOMPLETE', 'COMMITTED'], str(outcomes))
    board = s.board(agreement)
    check('6b. the economic obligation is done because osTRIS committed it', of(board, 'ECONOMIC_EXCHANGE')['status'] == 'DONE'
          and board['lastExecutionAttempt']['outcome'] == 'COMMITTED')

    big = s.agreement('Full rebuild', 500000, structured=False)
    base = s.base + f"/agreements/{big['id']}/trade"
    request(base + '/activate', 'POST', {}, s.pedro['accessToken'])
    for person, key in ((s.pedro, s.pedro_key), (s.ana, s.ana_key)):
        payload = request(base + '/signing-payload', token=person['accessToken'])
        request(base + '/authorizations', 'POST', {'credentialId': key[2], 'signatureBase64url': sign_authorization(key[0], payload['authorizationPayload'])}, person['accessToken'])
    status, _ = call(base + '/commit', 'POST', {}, s.ana['accessToken'])
    attempts = request(base + '/attempts', token=s.ana['accessToken'])
    check('6c. a commit beyond the payer\'s credit floor is an exposure refusal',
          status == 422 and attempts[-1]['outcome'] == 'EXPOSURE_REFUSAL' and attempts[-1]['ostrisCode'] == 'CREDIT_FLOOR_EXCEEDED', f'HTTP {status}; {attempts[-1:]}')
    board = s.board(big)
    check('6d. ... persisted and reported separately: the agreement is not failed, the exchange stays pending',
          of(board, 'ECONOMIC_EXCHANGE')['status'] == 'PENDING' and board['lastExecutionAttempt']['outcome'] == 'EXPOSURE_REFUSAL'
          and of(board, 'OBJECT')['status'] == 'PENDING'
          and runtime_count(s.tenant, f"select count(*) from stir.economic_execution_attempt where agreement_id='{big['id']}' and outcome='EXPOSURE_REFUSAL'") == 1)
    me = s.get('/economic/me', s.pedro)
    check('7a. available capacity is balance minus floor, never negative',
          int(me['availableCapacity']) == max(0, int(me['balanceProjection']) - int(me['creditFloor'])), json.dumps(me))


def history(s):
    print('\n== 8. Contextual history, no score')
    ana_id = s.ana['user']['id']
    check('8a. not shared by default, to others', s.get(f'/participants/{ana_id}/fulfilment-history', s.noor)['status'] == 'NOT_SHARED')
    small = s.agreement('Puncture', 5, structured=False)
    obj = of(s.board(small), 'OBJECT')
    s.declare(small, obj, s.ana, 'PERFORMED')
    check('8b. a claim alone is not history', s.get(f'/participants/{ana_id}/fulfilment-history', s.ana)['status'] == 'NO_HISTORY_YET')
    s.declare(small, obj, s.pedro, 'CONFORMITY_CONFIRMED')
    own = s.get(f'/participants/{ana_id}/fulfilment-history', s.ana)
    check('8c. the person sees their own described facts', own['status'] == 'HISTORY' and own['entries'] == [{'kind': 'OBJECT', 'fulfilmentMode': None, 'count': 1}], json.dumps(own))
    check('8d. still not shown to others until shared', s.get(f'/participants/{ana_id}/fulfilment-history', s.noor)['status'] == 'NOT_SHARED')
    request(s.base + '/participants/me/fulfilment-history-sharing', 'PUT', {'share': True}, s.ana['accessToken'])
    shared = s.get(f'/participants/{ana_id}/fulfilment-history', s.noor)
    check('8e. once shared, others see the same facts and nothing more', shared['entries'] == own['entries'] and set(shared) == {'userId', 'status', 'shared', 'entries'})
    check('8f. a newcomer is "no history yet", never low', s.get(f"/participants/{s.noor['user']['id']}/fulfilment-history", s.noor)['status'] == 'NO_HISTORY_YET')
    check('8g. the profile says whether it is shared', s.get('/participants/me', s.ana)['shareFulfilmentHistory'] is True)


if __name__ == '__main__':
    repair = Repair()
    obligations_and_partial(repair)
    amendments(repair)
    execution(repair)
    history(repair)
    failed = [name for name, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed')
    print('AGREEMENT FULFILMENT HTTP E2E: ' + ('FAIL' if failed else 'PASS'))
    sys.exit(1 if failed else 0)
