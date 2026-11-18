"""Agreement cases HTTP E2E (stir-doc/AGREEMENT_CASES.md), against a running isolated stack.

Ana repairs bicycles; Pedro paid her 30 units in osTRIS and says the repair never happened. The
community has named three reviewers. Through the public HTTP API only, the script checks that:

1. reviewers are named by community authority, never by platform capability, and are public;
2. only the party an obligation is owed to opens a case about it, and only parties and reviewers see it;
3. restricted evidence reaches the reviewers; the other party learns it exists, not what it says;
4. containment pauses the Agreement's amendments and execution, and nothing about a person;
5. the finding about the effect is appealed and decided by a reviewer other than its author;
6. the economic correction is an adjudicated osTRIS REVERSAL with a FINAL_DISPUTE_RESOLUTION basis,
   signed by the reviewers' own keys; the original exchange stays committed and balances return;
7. conduct is a separate decision (a good-faith third party here), and no sanction follows from it;
8. a sanction needs a final attribution of breach, is decided explicitly, and can be appealed and
   overturned; nothing anywhere is sanctioned automatically, and history/credit are untouched.

Isolated development fixtures only: a fresh tenant created with idax_core.tenant_create and the
public Shell HTTP API. Requires STIR_COMPOSE_PROJECT (multitenant.sql). Never run it against a
shared or production stack.
"""
import json
import sys
import uuid
from economic_exchange_e2e import PERMISSIONS, activate_economic, make_user, sign_authorization, b64url
from multitenant import sql
from negotiation_execution_integrity_e2e import call
from smoke import ROOT, request

RESULTS = []
CASES = PERMISSIONS + ['stir.agreements.fulfil', 'stir.notifications.read', 'OSTRIS_RESOLUTION_RECORD']


def check(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  [{detail}]' if detail else ''), flush=True)


def scalar(query):
    out = sql(query)
    return out.splitlines()[-1].strip() if out else ''


def sign_governance(private_key, payload):
    assert payload['signingDomain'] == 'OSTRIS:TX:GOVAUTH:V1', payload['signingDomain']
    return b64url(private_key.sign(b'OSTRIS:TX:GOVAUTH:V1' + b'\x00' + payload['governancePayload'].encode('utf-8')))


class Community:
    def __init__(self):
        login = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})
        self.admin = login['accessToken']
        self.suffix = uuid.uuid4().hex[:8]
        self.tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-cases-{self.suffix}', 'STIR cases {self.suffix}', 'active', false);")
        role = request(f'/api/shell/v1/tenants/{self.tenant}/roles', 'POST', {'key': 'stir_cases_' + self.suffix, 'name': 'STIR cases E2E',
                       'description': 'Isolated development fixture', 'enabled': True}, self.admin, expected=(200, 201))
        request(f'/api/shell/v1/tenants/{self.tenant}/roles/{role["id"]}/permissions', 'PUT', CASES, self.admin)
        names = ('ana', 'pedro', 'noor', 'marta', 'rosa', 'tomas', 'lia')
        people = {name: make_user(self.admin, self.tenant, role['id'], name, self.suffix) for name in names}
        for name, person in people.items(): setattr(self, name, person)
        # Marta is the community's bootstrap authority (tenant owner); nobody else is.
        sql(f"update idax_core.tenant_user set role='owner' where tenant_id='{self.tenant}' and user_id='{self.marta['user']['id']}';")
        self.base = f'/api/stir/tenants/{self.tenant}'
        for name, person in people.items():
            request(self.base + '/participants/me', 'PUT', {'displayName': f'{name.title()} {self.suffix}'}, person['accessToken'])
        request(self.base + '/economic/marketplace/bootstrap', 'POST', {'communityName': 'Cases ' + self.suffix, 'unitCode': 'CRD', 'unitScale': 0}, self.marta['accessToken'])
        self.keys = {name: activate_economic(self.base, people[name]) for name in ('ana', 'pedro', 'rosa', 'tomas', 'lia')}
        self.community = request(self.base + '/economic/marketplace', token=self.marta['accessToken'])['communityId']

    def get(self, path, person):
        return request(self.base + path, token=person['accessToken'])

    def post(self, path, person, body=None, expected=200):
        if expected in (200, 201):
            return request(self.base + path, 'POST', body or {}, person['accessToken'], expected=(200, 201))
        status, _ = call(self.base + path, 'POST', body or {}, person['accessToken'])
        return status

    def agreement(self, title, amount):
        """Ana offers a repair; Pedro accepts and pays; the exchange is committed in osTRIS."""
        listing = request(self.base + '/listings', 'POST', {'direction': 'OFFER', 'title': f'{title} {self.suffix}', 'description': 'Cases fixture',
                          'category': 'general', 'resourceKind': 'service'}, self.ana['accessToken'], 201)
        offer = {'message': 'Deal?', 'quantity': None, 'unitLabel': None, 'proposedAmount': str(amount), 'proposedUnitRef': None, 'terms': None,
                 'structuredTerms': {'scope': 'Brakes and gears', 'ownerTasks': 'Test-ride after the repair', 'initiatorTasks': None,
                                     'fulfilmentMode': 'ON_SITE', 'fulfilBy': '2030-01-10', 'openQuestions': None, 'validUntil': None}}
        negotiation = request(self.base + f"/listings/{listing['id']}/offers", 'POST', offer, self.pedro['accessToken'], 201)
        agreement = request(self.base + f"/negotiations/{negotiation['id']}/accept", 'POST',
                            {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version']}, self.ana['accessToken'])
        trade = self.base + f"/agreements/{agreement['id']}/trade"
        request(trade + '/activate', 'POST', {}, self.pedro['accessToken'])
        for name in ('pedro', 'ana'):
            person, key = getattr(self, name), self.keys[name]
            payload = request(trade + '/signing-payload', token=person['accessToken'])
            request(trade + '/authorizations', 'POST', {'credentialId': key[2], 'signatureBase64url': sign_authorization(key[0], payload['authorizationPayload'])}, person['accessToken'])
        agreement['trade'] = request(trade + '/commit', 'POST', {}, self.ana['accessToken'])
        return agreement

    def board(self, agreement, person):
        return self.get(f"/agreements/{agreement['id']}/obligations", person)

    def balance(self, person):
        return int(self.get('/economic/me', person)['balanceProjection'])


def of(board, kind):
    return next(o for o in board['obligations'] if o['kind'] == kind and o['status'] != 'SUPERSEDED')


def decision(view, kind):
    return next(d for d in view['decisions'] if d['decisionType'] == kind and d['status'] != 'OVERTURNED')


def reviewers(s):
    print('\n== 1. Who reviews')
    check('0. the instance declares agreementCasesVersion 1', request('/api/stir/instance').get('agreementCasesVersion') == 1)
    s.repair = s.agreement('Bike repair', 30)
    check('0b. Pedro paid Ana 30 units in osTRIS', s.balance(s.pedro) == -30 and s.balance(s.ana) == 30)
    obj = of(s.board(s.repair, s.pedro), 'OBJECT')
    request(s.base + f"/agreements/{s.repair['id']}/obligations/{obj['id']}/events", 'POST', {'type': 'PERFORMED'}, s.ana['accessToken'])
    request(s.base + f"/agreements/{s.repair['id']}/obligations/{obj['id']}/events", 'POST', {'type': 'NOT_RECEIVED'}, s.pedro['accessToken'])
    s.obj = obj
    status = s.post(f"/agreements/{s.repair['id']}/cases", s.pedro, {'obligationId': obj['id'], 'claim': 'NON_DELIVERY', 'statement': 'x'}, expected=409)
    check('1a. no case can be opened before the community names its reviewers', status == 409, f'HTTP {status}')
    ids = [s.rosa['user']['id'], s.tomas['user']['id'], s.lia['user']['id']]
    status, _ = call(s.base + '/case-review', 'PUT', {'reviewerUserIds': ids, 'threshold': 2, 'appealWindowHours': 1}, s.admin)
    check('1b. a platform superuser is not community authority', status in (403, 404), f'HTTP {status}')
    status, _ = call(s.base + '/case-review', 'PUT', {'reviewerUserIds': ids, 'threshold': 2, 'appealWindowHours': 1}, s.ana['accessToken'])
    check('1c. neither is an ordinary member', status == 403, f'HTTP {status}')
    view = request(s.base + '/case-review', 'PUT', {'reviewerUserIds': ids, 'threshold': 2, 'appealWindowHours': 1}, s.marta['accessToken'])
    check('1d. the community authority names three reviewers, two signatures for a correction', view['configured'] and len(view['reviewers']) == 3 and view['signatureThreshold'] == 2)
    public = s.get('/case-review', s.noor)
    check('1e. who reviews is public to the community', sorted(r['userId'] for r in public['reviewers']) == sorted(ids) and not public['youReview'])
    rows = scalar(f"select count(*) from ostris.community_resolution_controller c join ostris.community_resolution_authority a on a.id=c.authority_id "
                  f"where a.id='{view['ostrisAuthorityId']}' and a.threshold=2 and a.revocation_sequence is null")
    check('1f. osTRIS holds the same reviewers as the effective resolution authority', rows == '3', rows)


def opening(s):
    print('\n== 2-4. Opening, evidence and containment')
    status = s.post(f"/agreements/{s.repair['id']}/cases", s.ana, {'obligationId': s.obj['id'], 'claim': 'NON_DELIVERY', 'statement': 'x'}, expected=409)
    check('2a. only the party an obligation is owed to can claim it was not fulfilled', status == 409, f'HTTP {status}')
    economic = of(s.board(s.repair, s.pedro), 'ECONOMIC_EXCHANGE')
    status = s.post(f"/agreements/{s.repair['id']}/cases", s.ana, {'obligationId': economic['id'], 'claim': 'NON_DELIVERY', 'statement': 'x'}, expected=409)
    check('2b. the economic exchange is not claimed: osTRIS is its evidence', status == 409, f'HTTP {status}')
    case = s.post(f"/agreements/{s.repair['id']}/cases", s.pedro, {'obligationId': s.obj['id'], 'claim': 'NON_DELIVERY',
                  'statement': 'I left the bike on Monday; it was never repaired.'})
    s.case = case['id']
    check('2c. Pedro opens a non-delivery case; Ana is the respondent', case['status'] == 'OPEN' and case['respondentUserId'] == s.ana['user']['id'] and case['yourRole'] == 'CLAIMANT')
    inbox = s.get('/notifications', s.ana)
    check('2d. Ana is told', 'CASE_OPENED' in [n['type'] for n in (inbox['content'] if isinstance(inbox, dict) else inbox)])
    for label, token in (('a stranger in the community', s.noor['accessToken']), ('a platform superuser', s.admin)):
        status, _ = call(s.base + f'/cases/{s.case}', token=token)
        check(f'2e. {label} does not see the case', status in (403, 404), f'HTTP {status}')

    s.post(f'/cases/{s.case}/evidence', s.ana, {'description': 'Courier receipt with my home address', 'visibility': 'REVIEWERS_ONLY', 'externalReference': 'TRK-4411'})
    s.post(f'/cases/{s.case}/evidence', s.pedro, {'description': 'Photo of the bike, still broken'})
    pedro_view = s.get(f'/cases/{s.case}', s.pedro)
    restricted = next(e for e in pedro_view['evidence'] if e['visibility'] == 'REVIEWERS_ONLY')
    check('3a. Pedro learns that Ana added evidence, not what it says', restricted['description'] is None and restricted['externalReference'] is None)
    rosa_view = s.get(f'/cases/{s.case}', s.rosa)
    check('3b. the reviewers read it', any(e['externalReference'] == 'TRK-4411' for e in rosa_view['evidence']) and rosa_view['yourRole'] == 'REVIEWER')

    taken = s.post(f'/cases/{s.case}/take', s.rosa)
    check('4a. Rosa takes the case', taken['status'] == 'UNDER_REVIEW' and taken['reviewerUserId'] == s.rosa['user']['id'])
    until = '2099-01-01T00:00:00Z'
    status = s.post(f'/cases/{s.case}/containment', s.rosa, {'reason': 'x', 'until': until}, expected=400)
    check('4b. containment is bounded in time', status == 400, f'HTTP {status}')
    import datetime
    until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)).isoformat().replace('+00:00', 'Z')
    contained = s.post(f'/cases/{s.case}/containment', s.rosa, {'reason': 'Hold changes while the delivery is checked', 'until': until})
    check('4c. Rosa pauses the agreement while it is reviewed', any(c['active'] for c in contained['containments']))
    head = s.board(s.repair, s.ana)['headDigest']
    status, body = call(s.base + f"/agreements/{s.repair['id']}/amendments", 'POST', {'baseDigest': head, 'replaces': [], 'reason': 'x',
                        'obligations': [{'kind': 'OWNER_TASKS', 'description': 'Call first', 'fulfilBy': None, 'fulfilmentMode': None}]}, s.ana['accessToken'])
    check('4d. ... an amendment is refused while paused', status == 409 and 'CASE_CONTAINMENT_ACTIVE' in str(body), f'HTTP {status}')
    me = s.get('/economic/me', s.ana)
    check('4e. ... and nothing about Ana changed: balance, floor and capacity are as before',
          int(me['balanceProjection']) == 30 and int(me['availableCapacity']) == 30 - int(me['creditFloor']), json.dumps(me))
    check('4f. the board shows the pause next to the obligations', s.board(s.repair, s.pedro)['containment'] is not None)


def finding_and_appeal(s):
    print('\n== 5. Finding about the effect, and its appeal')
    finding = {'effect': 'ESTABLISHED', 'economicCorrection': 'FULL_REVERSAL', 'performanceRemedy': 'The repair can be agreed again from scratch.',
               'reasoning': 'The courier receipt shows a delivery to an address that is not the one agreed.'}
    status = s.post(f'/cases/{s.case}/findings', s.tomas, finding, expected=409)
    check('5a. only the reviewer who took the case issues its finding', status == 409, f'HTTP {status}')
    view = s.post(f'/cases/{s.case}/findings', s.rosa, finding)
    f = decision(view, 'FINDING')
    check('5b. the finding is issued, with an appeal window', view['status'] == 'FINDING_ISSUED' and f['status'] == 'ISSUED' and f['appealDeadline'])
    basis = scalar(f"select basis_type||':'||status from ostris.resolution_basis where id='{f['ostrisBasisId']}'")
    check('5c. osTRIS records the dispute basis, still PENDING', basis == 'FINAL_DISPUTE_RESOLUTION:PENDING', basis)
    status = s.post(f"/cases/{s.case}/decisions/{f['id']}/finalize", s.tomas, expected=409)
    check('5d. time and one reviewer alone do not make it final', status == 409, f'HTTP {status}')
    status = s.post(f'/cases/{s.case}/attributions', s.tomas, {'subjectRole': 'OBLIGOR', 'assessment': 'BREACH', 'reasoning': 'x'}, expected=409)
    check('5e. conduct is not assessed before the finding about the effect is final', status == 409, f'HTTP {status}')
    view = s.post(f"/cases/{s.case}/decisions/{f['id']}/appeal", s.ana, {'grounds': 'I delivered it to the address Pedro gave me by phone.'})
    appeal = decision(view, 'FINDING')['appeal']
    check('5f. Ana appeals', view['status'] == 'APPEALED' and appeal['status'] == 'OPEN')
    status = s.post(f"/cases/{s.case}/appeals/{appeal['id']}/decide", s.rosa, {'outcome': 'UPHELD', 'reasoning': 'x'}, expected=409)
    check('5g. the author of the finding cannot decide its appeal', status == 409, f'HTTP {status}')
    view = s.post(f"/cases/{s.case}/appeals/{appeal['id']}/decide", s.tomas,
                  {'outcome': 'UPHELD', 'reasoning': 'No message changing the address appears in the negotiation.'})
    check('5h. Tomás upholds it: the case about the effect is resolved', view['status'] == 'RESOLVED' and decision(view, 'FINDING')['status'] == 'FINAL')
    basis = scalar(f"select status from ostris.resolution_basis where id='{f['ostrisBasisId']}'")
    check('5i. ... and the osTRIS basis is FINAL', basis == 'FINAL', basis)
    check('5j. resolving the effect lifts the pause', not any(c['active'] for c in view['containments']) and s.board(s.repair, s.pedro)['containment'] is None)
    check('5k. the obligation keeps its own declared status; the finding is a separate object',
          of(s.board(s.repair, s.pedro), 'OBJECT')['status'] == 'IN_DISAGREEMENT')
    s.finding = f


def correction(s):
    print('\n== 6-7. The economic correction, and conduct')
    view = s.post(f'/cases/{s.case}/attributions', s.lia, {'subjectRole': 'THIRD_PARTY', 'assessment': 'GOOD_FAITH_THIRD_PARTY',
                  'thirdPartyNote': 'The courier', 'reasoning': 'The courier delivered to an old address in good faith.'})
    check('7a. conduct is a separate decision: a good-faith third party', decision(view, 'ATTRIBUTION')['assessment'] == 'GOOD_FAITH_THIRD_PARTY')
    status = s.post(f'/cases/{s.case}/sanctions', s.rosa, {'kind': 'WARNING', 'reasoning': 'x'}, expected=409)
    check('7b. no sanction can follow from it', status == 409, f'HTTP {status}')

    view = s.post(f'/cases/{s.case}/remedies', s.lia)
    remedy = view['remedies'][0]
    proposal = scalar(f"select purpose||':'||status||':'||resolution_basis_type||':'||(references_json->>'reversesTransactionId') "
                      f"from ostris.transaction_proposal where id='{remedy['ostrisTransactionId']}'")
    check('6a. the correction is an adjudicated REVERSAL of exactly that exchange, never an EXCHANGE',
          proposal == f"REVERSAL:PROPOSED:FINAL_DISPUTE_RESOLUTION:{s.repair['trade']['transactionId']}", proposal)
    status = s.post(f"/cases/{s.case}/remedies/{remedy['id']}/signatures", s.ana, {'credentialId': s.keys['ana'][2], 'signatureBase64url': 'x'}, expected=403)
    check('6b. a party never signs a correction about their own agreement', status == 403, f'HTTP {status}')
    payload = s.get(f"/cases/{s.case}/remedies/{remedy['id']}/signing-payload", s.lia)
    body = json.loads(payload['governancePayload'])
    check('6c. the reviewer signs the exact governed text: the original negated, both accounts covered',
          payload['signingDomain'] == 'OSTRIS:TX:GOVAUTH:V1' and body['purpose'] == 'REVERSAL'
          and sorted(e['amount'] for e in body['entries']) == ['-30', '30'] and len(body['coveredAccounts']) == 2
          and body['resolutionBasis'] == {'id': s.finding['ostrisBasisId'], 'type': 'FINAL_DISPUTE_RESOLUTION'})
    view = s.post(f"/cases/{s.case}/remedies/{remedy['id']}/signatures", s.lia, {'credentialId': s.keys['lia'][2], 'signatureBase64url': sign_governance(s.keys['lia'][0], payload)})
    check('6d. one signature of two: nothing moves yet', view['remedies'][0]['status'] == 'AWAITING_AUTHORITY' and s.balance(s.pedro) == -30)
    payload = s.get(f"/cases/{s.case}/remedies/{remedy['id']}/signing-payload", s.tomas)
    view = s.post(f"/cases/{s.case}/remedies/{remedy['id']}/signatures", s.tomas, {'credentialId': s.keys['tomas'][2], 'signatureBase64url': sign_governance(s.keys['tomas'][0], payload)})
    executed = view['remedies'][0]
    check('6e. with the second reviewer signature osTRIS commits the reversal', executed['status'] == 'EXECUTED' and executed['committedSequence'] is not None, json.dumps(executed))
    check('6f. balances are back where they were', s.balance(s.pedro) == 0 and s.balance(s.ana) == 0)
    journal = scalar(f"select string_agg(purpose,',' order by community_sequence) from ostris.journal_transaction where id in "
                     f"('{s.repair['trade']['transactionId']}','{executed['ostrisTransactionId']}')")
    check('6g. the original exchange stays committed; the reversal is a second, linked entry', journal == 'EXCHANGE,REVERSAL', journal)
    board = s.board(s.repair, s.pedro)
    check('6h. the board reports the correction next to the economic obligation',
          board['corrections'][0]['status'] == 'EXECUTED' and of(board, 'ECONOMIC_EXCHANGE')['status'] == 'DONE')
    types = [e['type'] for e in s.get(f'/cases/{s.case}', s.pedro)['events']]
    check('6i. the timeline tells each party every step',
          types[:3] == ['OPENED', 'EVIDENCE_ADDED', 'EVIDENCE_ADDED'] and types[-1] == 'REMEDY_EXECUTED' and 'APPEAL_DECIDED' in types, str(types))


def sanction(s):
    print('\n== 8. A sanction is explicit, appealable and changes nothing else')
    tasks = of(s.board(s.repair, s.pedro), 'OWNER_TASKS')
    request(s.base + f"/agreements/{s.repair['id']}/obligations/{tasks['id']}/events", 'POST', {'type': 'NOT_RECEIVED'}, s.pedro['accessToken'])
    case = s.post(f"/agreements/{s.repair['id']}/cases", s.pedro, {'obligationId': tasks['id'], 'claim': 'NON_DELIVERY', 'statement': 'No test ride, and no answer.'})['id']
    s.post(f'/cases/{case}/take', s.rosa)
    f = decision(s.post(f'/cases/{case}/findings', s.rosa, {'effect': 'ESTABLISHED', 'economicCorrection': 'NONE', 'reasoning': 'Ana confirmed it in the case.'}), 'FINDING')
    s.post(f"/cases/{case}/decisions/{f['id']}/waive", s.ana)
    s.post(f"/cases/{case}/decisions/{f['id']}/waive", s.pedro)
    check('8a. both parties accept the finding; a reviewer confirms it final', s.post(f"/cases/{case}/decisions/{f['id']}/finalize", s.tomas)['status'] == 'RESOLVED')
    a = decision(s.post(f'/cases/{case}/attributions', s.tomas, {'subjectRole': 'OBLIGOR', 'assessment': 'BREACH', 'reasoning': 'Agreed and ignored twice.'}), 'ATTRIBUTION')
    s.post(f"/cases/{case}/decisions/{a['id']}/waive", s.ana)
    s.post(f"/cases/{case}/decisions/{a['id']}/waive", s.pedro)
    s.post(f"/cases/{case}/decisions/{a['id']}/finalize", s.lia)
    automatic = scalar(f"select count(*) from stir.agreement_case_decision d join stir.agreement_case c on c.id=d.case_id "
                       f"where c.tenant_id='{s.tenant}' and d.decision_type='SANCTION'")
    check('8b. a final attribution of breach does not sanction anyone by itself', automatic == '0', automatic)
    history_before = s.get(f"/participants/{s.ana['user']['id']}/fulfilment-history", s.ana)
    view = s.post(f'/cases/{case}/sanctions', s.rosa, {'kind': 'WARNING', 'reasoning': 'A first warning for an acknowledged breach.'})
    w = decision(view, 'SANCTION')
    check('8c. a reviewer decides a warning, explicitly, about Ana', w['sanctionKind'] == 'WARNING' and w['subjectUserId'] == s.ana['user']['id'] and w['status'] == 'ISSUED')
    check('8d. the warning changes no history, balance or credit',
          s.get(f"/participants/{s.ana['user']['id']}/fulfilment-history", s.ana) == history_before and s.balance(s.ana) == 0)
    view = s.post(f"/cases/{case}/decisions/{w['id']}/appeal", s.ana, {'grounds': 'The test ride was impossible: the bike was never returned.'})
    appeal = decision(view, 'SANCTION')['appeal']
    view = s.post(f"/cases/{case}/appeals/{appeal['id']}/decide", s.lia, {'outcome': 'OVERTURNED', 'reasoning': 'Without the bike there was no test ride to do.'})
    check('8e. the sanction is appealed and overturned by another reviewer',
          all(d['status'] == 'OVERTURNED' for d in view['decisions'] if d['decisionType'] == 'SANCTION'))
    check('8f. nothing in osTRIS was touched by the conduct path: one reversal in total, no penalty',
          scalar(f"select count(*) from ostris.journal_transaction where community_id='{s.community}' and purpose in ('PENALTY','RESTITUTION','WRITE_OFF')") == '0')


if __name__ == '__main__':
    community = Community()
    reviewers(community)
    opening(community)
    finding_and_appeal(community)
    correction(community)
    sanction(community)
    failed = [name for name, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed')
    print('AGREEMENT CASES HTTP E2E: ' + ('FAIL' if failed else 'PASS'))
    sys.exit(1 if failed else 0)
