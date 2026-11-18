"""Real browser proof of the agreement-cases UI (stir-doc/AGREEMENT_CASES.md), in Spanish.

Pedro opens a case from the obligation board and adds evidence through the form; Ana's restricted
evidence shows to him as "shared only with the reviewers". Rosa, a reviewer, activates her own
device key in the browser, takes the case, issues a finding that orders a full reversal of the community-unit exchange,
confirms it final once both parties accepted it, reviews exactly what she is about to sign and
signs the adjudicated reversal with her own key. Pedro's board then shows the correction. Setup
and the other people's steps use the public HTTP calls agreement_cases_e2e.py proves. Uses
STIR_TEST_URL (smoke.BASE) and an isolated tenant; no JavaScript page error is tolerated.
"""
import os
import pathlib
import uuid
from playwright.sync_api import sync_playwright, expect
from economic_exchange_e2e import PERMISSIONS, activate_economic, sign_authorization
from marketplace_browser_smoke import make_login
from multitenant import sql
from smoke import BASE, ROOT, request

ARTIFACTS = pathlib.Path(os.environ.get('STIR_E2E_ARTIFACTS', 'e2e-artifacts'))
CASES = PERMISSIONS + ['stir.agreements.fulfil', 'stir.notifications.read', 'OSTRIS_RESOLUTION_RECORD']


def open_stir(page, email, password, community):
    page.goto(BASE)
    page.locator('input[type=email]').fill(email); page.locator('input[type=password]').fill(password)
    page.locator('button[type=submit]').click()
    page.locator('.module-card').filter(has=page.get_by_role('heading', name='STIR', exact=True)).click()
    page.locator('.surface > header select').first.select_option(label=community)


def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]; name = 'Cases Browser ' + suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('cases-b-{suffix}','{name}','active',false);")
    shell = f'/api/shell/v1/tenants/{tenant}'
    role = request(shell + '/roles', 'POST', {'key': 'cases' + suffix, 'name': 'Member', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + role['id'] + '/permissions', 'PUT', CASES, admin)
    logins = {label: make_login(admin, tenant, role['id'], label, suffix) for label in ('ana', 'pedro', 'marta', 'rosa', 'tomas')}
    people = {label: request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], login))) for label, login in logins.items()}
    ana, pedro, marta, rosa, tomas = (people[k] for k in ('ana', 'pedro', 'marta', 'rosa', 'tomas'))
    sql(f"update idax_core.tenant_user set role='owner' where tenant_id='{tenant}' and user_id='{marta['user']['id']}';")
    base = f'/api/stir/tenants/{tenant}'
    for label, person in people.items():
        request(base + '/participants/me', 'PUT', {'displayName': f'{label.title()} {suffix}'}, person['accessToken'])
    request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': name, 'unitCode': 'CRD', 'unitScale': 0}, marta['accessToken'])
    keys = {'ana': activate_economic(base, ana), 'pedro': activate_economic(base, pedro), 'tomas': activate_economic(base, tomas)}
    listing = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': 'Bike repair ' + suffix, 'description': 'Browser fixture',
                      'category': 'general', 'resourceKind': 'service'}, ana['accessToken'], 201)
    negotiation = request(base + f"/listings/{listing['id']}/offers", 'POST', {'message': 'Deal?', 'proposedAmount': '30',
        'structuredTerms': {'scope': 'Brakes and gears', 'fulfilmentMode': 'ON_SITE', 'fulfilBy': '2030-01-10'}}, pedro['accessToken'], 201)
    agreement = request(base + f"/negotiations/{negotiation['id']}/accept", 'POST',
                        {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version']}, ana['accessToken'])
    trade = base + f"/agreements/{agreement['id']}/trade"
    request(trade + '/activate', 'POST', {}, pedro['accessToken'])
    for label in ('pedro', 'ana'):
        payload = request(trade + '/signing-payload', token=people[label]['accessToken'])
        request(trade + '/authorizations', 'POST', {'credentialId': keys[label][2], 'signatureBase64url': sign_authorization(keys[label][0], payload['authorizationPayload'])}, people[label]['accessToken'])
    request(trade + '/commit', 'POST', {}, ana['accessToken'])
    obj = next(o for o in request(base + f"/agreements/{agreement['id']}/obligations", token=pedro['accessToken'])['obligations'] if o['kind'] == 'OBJECT')
    request(base + f"/agreements/{agreement['id']}/obligations/{obj['id']}/events", 'POST', {'type': 'PERFORMED'}, ana['accessToken'])
    request(base + f"/agreements/{agreement['id']}/obligations/{obj['id']}/events", 'POST', {'type': 'NOT_RECEIVED'}, pedro['accessToken'])

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            # Rosa activates her own device key in her browser first: she will sign with it.
            rosa_page = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 1600}).new_page()
            rosa_page.set_default_timeout(30000); rosa_page.on('pageerror', lambda e: errors.append('rosa: ' + str(e)))
            open_stir(rosa_page, *logins['rosa'], name)
            rosa_page.goto(BASE + '/stir/economic')
            rosa_page.get_by_role('button', name='Activar intercambios').click()
            expect(rosa_page.get_by_test_id('available-capacity')).to_be_visible()
            request(base + '/case-review', 'PUT', {'reviewerUserIds': [rosa['user']['id'], tomas['user']['id']], 'threshold': 1, 'appealWindowHours': 24}, marta['accessToken'])
            rosa_page.goto(BASE + '/stir/case-review')
            expect(rosa_page.get_by_test_id('case-reviewers')).to_contain_text('Rosa ' + suffix)
            print('PASS: a reviewer activates her own key in the browser, and the public roster shows who reviews')

            page = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 1600}).new_page()
            page.set_default_timeout(30000); page.on('pageerror', lambda e: errors.append('pedro: ' + str(e)))
            open_stir(page, *logins['pedro'], name)
            page.goto(BASE + '/stir/agreements/' + agreement['id'])
            card = page.get_by_test_id('obligation-OBJECT')
            expect(card.get_by_test_id('obligation-status')).to_have_text('En desacuerdo')
            card.get_by_test_id('case-open-OBJECT').click()
            form = card.get_by_test_id('case-open-form')
            form.get_by_test_id('case-claim').select_option('NON_DELIVERY')
            form.get_by_test_id('case-statement-text').fill('Dejé la bici el lunes y nunca se reparó.')
            form.get_by_test_id('case-statement-submit').click()
            expect(page.get_by_test_id('case-status')).to_have_text('Abierto, esperando revisor')
            evidence = page.get_by_test_id('evidence-form')
            evidence.get_by_test_id('evidence-text').fill('Foto de la bici, sigue rota')
            evidence.get_by_test_id('evidence-submit').click()
            expect(page.get_by_test_id('case-evidence')).to_contain_text('Foto de la bici, sigue rota')
            case_id = page.url.rstrip('/').split('/')[-1]
            request(base + f'/cases/{case_id}/evidence', 'POST', {'description': 'Recibo del mensajero con mi domicilio', 'visibility': 'REVIEWERS_ONLY'}, ana['accessToken'])
            page.reload()
            expect(page.get_by_test_id('evidence-REVIEWERS_ONLY')).to_contain_text('compartida sólo con los revisores')
            expect(page.get_by_text('Recibo del mensajero', exact=False)).to_have_count(0)
            page.screenshot(path=str(ARTIFACTS / 'stir-case-opened.png'), full_page=True)
            print('PASS: Pedro opens a case from the board and adds evidence; Ana\'s restricted evidence stays with the reviewers')

            rosa_page.goto(BASE + '/stir/cases')
            rosa_page.get_by_test_id('case-review-queue').get_by_role('button').first.click()
            expect(rosa_page.get_by_test_id('case-evidence')).to_contain_text('Recibo del mensajero con mi domicilio')
            rosa_page.get_by_test_id('case-take').click()
            expect(rosa_page.get_by_test_id('case-status')).to_have_text('En revisión')
            finding = rosa_page.get_by_test_id('finding')
            finding.get_by_test_id('finding-effect-select').select_option('ESTABLISHED')
            finding.get_by_test_id('finding-correction').select_option('FULL_REVERSAL')
            finding.get_by_test_id('finding-text').fill('El recibo muestra una entrega en una dirección distinta de la acordada.')
            finding.get_by_test_id('finding-submit').click()
            expect(rosa_page.get_by_test_id('decision-FINDING-ISSUED')).to_be_visible()
            decision = request(base + f'/cases/{case_id}', token=pedro['accessToken'])['decisions'][0]
            for person in (ana, pedro):
                request(base + f"/cases/{case_id}/decisions/{decision['id']}/waive", 'POST', {}, person['accessToken'])
            rosa_page.reload()
            rosa_page.get_by_test_id('decision-finalize').click()
            expect(rosa_page.get_by_test_id('case-status')).to_have_text('Resuelto')
            print('PASS: Rosa takes the case, issues a finding in the form, and confirms it final once both parties accepted it')

            rosa_page.get_by_test_id('remedy-prepare').click()
            remedy = rosa_page.get_by_test_id('remedy-AWAITING_AUTHORITY')
            remedy.get_by_test_id('remedy-review').click()
            expect(remedy.get_by_test_id('remedy-summary')).to_contain_text('REVERSAL')
            rosa_page.screenshot(path=str(ARTIFACTS / 'stir-case-remedy-review.png'), full_page=True)
            remedy.get_by_test_id('remedy-sign').click()
            expect(rosa_page.get_by_test_id('remedy-EXECUTED').get_by_test_id('remedy-status')).to_have_text('Registrada en osTRIS')
            rosa_page.screenshot(path=str(ARTIFACTS / 'stir-case-remedy-executed.png'), full_page=True)
            print('PASS: Rosa reviews exactly what she signs and signs the adjudicated reversal with her own key')

            page.goto(BASE + '/stir/agreements/' + agreement['id'])
            expect(page.get_by_test_id('correction-EXECUTED')).to_contain_text('Revertido por una decisión firme de un caso')
            expect(page.get_by_test_id('obligation-case-RESOLVED')).to_be_visible()
            page.goto(BASE + '/stir/cases/' + case_id)
            expect(page.get_by_test_id('case-event-REMEDY_EXECUTED')).to_be_visible()
            expect(page.get_by_text('No es una puntuación', exact=False)).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / 'stir-case-timeline.png'), full_page=True)
            me = request(base + '/economic/me', token=pedro['accessToken'])
            assert me['balanceProjection'] == '0', me
            print('PASS: Pedro\'s board shows the correction and the resolved case; his timeline tells each step; his balance is back to 0')

            assert not errors, errors
            print('AGREEMENT CASES BROWSER E2E: PASS')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
