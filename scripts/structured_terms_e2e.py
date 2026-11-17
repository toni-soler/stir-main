"""Structured terms and needs E2E - STIR's own HTTP APIs only (stir-doc/STRUCTURED_TERMS_AND_NEEDS.md).

1. Listings carry comparable details; a need states by when and what can or cannot change.
2. The counterpart note reaches only the owner and the people negotiating, never search results or a
   listing revision (and therefore never reference evidence).
3. An edit without details (a client that does not know them) keeps them.
4. Offers carry structured terms frozen into the Agreement snapshot (schemaVersion 4).
5. An offer that leaves questions open is accepted only by acknowledging them; an expired offer is
   not accepted at all, and cannot be made already expired.

Isolated development fixtures only: needs STIR_COMPOSE_PROJECT (never stir-dev, see multitenant.py)
and STIR_TEST_URL. Exit code 1 on any failure.
"""
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from economic_exchange_e2e import PERMISSIONS
from multitenant import sql
from negotiation_execution_integrity_e2e import call, user
from smoke import ROOT, request

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  [{detail}]' if detail else ''), flush=True)


def instant(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


class Square:
    def __init__(self):
        admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
                        'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
        self.suffix = uuid.uuid4().hex[:8]
        self.tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-terms-{self.suffix}', 'STIR terms {self.suffix}', 'active', false);")
        role = request(f'/api/shell/v1/tenants/{self.tenant}/roles', 'POST', {'key': 'stir_terms_' + self.suffix, 'name': 'STIR terms E2E',
                       'description': 'Isolated development fixture', 'enabled': True}, admin, expected=(200, 201))
        request(f'/api/shell/v1/tenants/{self.tenant}/roles/{role["id"]}/permissions', 'PUT', PERMISSIONS, admin)
        self.vecina, self.marcos, self.noor = (user(admin, self.tenant, role['id'], name, self.suffix) for name in ('vecina', 'marcos', 'noor'))
        self.base = f'/api/stir/tenants/{self.tenant}'
        for person, name in ((self.vecina, 'Vecina'), (self.marcos, 'Marcos'), (self.noor, 'Noor')):
            request(self.base + '/participants/me', 'PUT', {'displayName': f'{name} {self.suffix}'}, person['accessToken'])

    def listing(self, direction, title, details=None, person=None, expected=201):
        body = {'direction': direction, 'title': f'{title} {self.suffix}', 'description': 'Terms fixture', 'category': 'general', 'resourceKind': 'service'}
        if details is not None:
            body['details'] = details
        return call(self.base + '/listings', 'POST', body, (person or self.vecina)['accessToken']) if expected != 201 else \
            request(self.base + '/listings', 'POST', body, (person or self.vecina)['accessToken'], 201)


NEED = {'itemCondition': None, 'fulfilmentMode': 'ON_SITE', 'includedItems': None, 'availableUntil': None, 'availability': 'AVAILABLE',
        'neededBy': '2030-01-15', 'flexibleOn': ['AMOUNT', 'TIMING'], 'firmOn': ['PLACE'], 'counterpartNote': 'Third floor, no lift; ring twice'}


def terms(open_questions=None, valid_until=None):
    return {'message': 'I can visit and adjust it', 'quantity': None, 'unitLabel': None, 'proposedAmount': '8', 'proposedUnitRef': None, 'terms': None,
            'structuredTerms': {'scope': 'Diagnosis and adjustment; a new part is not included', 'ownerTasks': 'Clear access to the window',
                                'initiatorTasks': 'Bring tools; say what the part would cost', 'fulfilmentMode': 'ON_SITE', 'fulfilBy': '2030-01-10',
                                'openQuestions': open_questions, 'validUntil': valid_until}}


def details_and_privacy(s):
    print('\n== 1-3. Comparable details, needs and the counterpart note')
    need = s.listing('WANTED', 'Blind repair', NEED)
    read = lambda person: request(s.base + f"/listings/{need['id']}", token=person['accessToken'])
    mine = read(s.vecina)
    check('1a. a need keeps its date, its flexible and its firm dimensions',
          mine['neededBy'] == '2030-01-15' and mine['flexibleOn'] == ['TIMING', 'AMOUNT'] and mine['firmOn'] == ['PLACE'] and mine['fulfilmentMode'] == 'ON_SITE',
          json.dumps({k: mine[k] for k in ('neededBy', 'flexibleOn', 'firmOn')}))
    status, _ = s.listing('OFFER', 'Not a need', {**NEED, 'counterpartNote': None, 'flexibleOn': [], 'firmOn': []}, expected=400)
    check('1b. only a need has a date by which it is needed', status == 400, f'HTTP {status}')
    status, _ = s.listing('WANTED', 'Both', {**NEED, 'firmOn': ['TIMING']}, expected=400)
    check('1c. a dimension cannot be both flexible and firm', status == 400, f'HTTP {status}')
    status, _ = s.listing('OFFER', 'Unknown', {'itemCondition': 'MINT'}, expected=400)
    check('1d. an unknown condition is refused', status == 400, f'HTTP {status}')

    check('2a. the owner reads the counterpart note', mine['counterpartNote'] == NEED['counterpartNote'])
    stranger = read(s.marcos)
    check('2b. someone not negotiating only learns that there is one', stranger['counterpartNote'] is None and stranger['hasCounterpartNote'] is True)
    page = request(s.base + '/listings?q=' + s.suffix, token=s.vecina['accessToken'])
    check('2c. search results never carry it, not even to the owner',
          page['content'] and all(row['counterpartNote'] is None for row in page['content']), f"{len(page['content'])} rows")
    request(s.base + f"/listings/{need['id']}/offers", 'POST', terms(), s.marcos['accessToken'], 201)
    check('2d. once Marcos negotiates, he reads it', read(s.marcos)['counterpartNote'] == NEED['counterpartNote'])
    check('2e. Noor, still not negotiating, does not', read(s.noor)['counterpartNote'] is None)
    revision = sql(f"select canonical_json from stir.listing_revision where listing_id='{need['id']}' order by revision_number desc limit 1;").splitlines()[-1]
    check('2f. the listing revision freezes the comparable details but not the note',
          '"fulfilmentMode":"ON_SITE"' in revision and 'ring twice' not in revision and 'STIR-LISTING-REVISION-JCS-2' in revision)

    current = read(s.vecina)
    legacy = {key: current[key] for key in ('direction', 'title', 'description', 'category', 'resourceKind', 'version')} | {'title': current['title'] + ' (edited)'}
    request(s.base + f"/listings/{need['id']}", 'PUT', legacy, s.vecina['accessToken'])
    after = read(s.vecina)
    check('3. an edit that does not send details keeps them', after['counterpartNote'] == NEED['counterpartNote'] and after['firmOn'] == ['PLACE'])


def structured_offers(s):
    print('\n== 4-5. Structured terms, open questions and validity')
    instance = request('/api/stir/instance')
    check('4a. the instance declares snapshot schema 4', instance['externalContractSchemaVersion'] == 4, str(instance['externalContractSchemaVersion']))
    need = s.listing('WANTED', 'Blind', NEED)
    negotiation = request(s.base + f"/listings/{need['id']}/offers", 'POST', terms('Which part is needed is decided at the visit'), s.marcos['accessToken'], 201)
    body = {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version']}
    status, _ = call(s.base + f"/negotiations/{negotiation['id']}/accept", 'POST', body, s.vecina['accessToken'])
    check('5a. an offer with open questions is not accepted without acknowledging them', status == 409, f'HTTP {status}')
    agreement = request(s.base + f"/negotiations/{negotiation['id']}/accept", 'POST', body | {'acknowledgeOpenQuestions': True}, s.vecina['accessToken'])
    frozen = json.loads(agreement['snapshot']['canonicalJson'])
    check('4b. the snapshot freezes scope, both sides\' tasks and timing (schemaVersion 4)',
          agreement['snapshot']['schemaVersion'] == 4 and frozen['scope'].startswith('Diagnosis') and frozen['ownerTasks'] == 'Clear access to the window'
          and frozen['initiatorTasks'].startswith('Bring tools') and frozen['fulfilBy'] == '2030-01-10' and frozen['fulfilmentMode'] == 'ON_SITE')
    check('5b. ... and that it was accepted with the questions still open',
          frozen['openQuestions'].startswith('Which part') and frozen['openQuestionsAcknowledged'] is True)

    other = s.listing('OFFER', 'Ladder')
    negotiation = request(s.base + f"/listings/{other['id']}/offers", 'POST', terms(valid_until=instant(3)), s.marcos['accessToken'], 201)
    time.sleep(4)
    status, _ = call(s.base + f"/negotiations/{negotiation['id']}/accept", 'POST',
                     {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version']}, s.vecina['accessToken'])
    check('5c. an expired offer cannot be accepted', status == 409, f'HTTP {status}')
    countered = request(s.base + f"/negotiations/{negotiation['id']}/offers", 'POST', terms() | {'expectedVersion': negotiation['version']}, s.vecina['accessToken'])
    check('5d. ... but the conversation goes on with a new proposal', len(countered['offers']) == 2 and countered['offers'][0]['validUntil'])
    third = s.listing('OFFER', 'Table')
    status, _ = call(s.base + f"/listings/{third['id']}/offers", 'POST', terms(valid_until=instant(-60)), s.marcos['accessToken'])
    check('5e. an offer cannot be made already expired', status == 400, f'HTTP {status}')
    plain = request(s.base + f"/listings/{third['id']}/offers", 'POST', {'message': 'Interested', 'proposedAmount': '3'}, s.marcos['accessToken'], 201)
    agreement = request(s.base + f"/negotiations/{plain['id']}/accept", 'POST',
                        {'offerId': plain['offers'][-1]['id'], 'expectedVersion': plain['version']}, s.vecina['accessToken'])
    frozen = json.loads(agreement['snapshot']['canonicalJson'])
    check('4c. an offer without structured terms is accepted as before', frozen['scope'] is None and frozen['openQuestionsAcknowledged'] is False)


if __name__ == '__main__':
    square = Square()
    details_and_privacy(square)
    structured_offers(square)
    failed = [name for name, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed')
    sys.exit(1 if failed else 0)
