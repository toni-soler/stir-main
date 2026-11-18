"""Real browser proof of the agreement-fulfilment UI (stir-doc/AGREEMENT_FULFILMENT.md): the
obligation board on an Agreement, a partial declaration made through the rendered form, an
amendment proposed through the UI, the credit capacity on the economic page and the opt-in
history on the profile. Setup and the counterparty's answers use the same public HTTP calls
agreement_fulfilment_e2e.py proves; this script's job is to prove the UI renders and works, in
Spanish, with no JavaScript page errors. Uses STIR_TEST_URL (smoke.BASE) and an isolated tenant.
"""
import os
import pathlib
import uuid
from playwright.sync_api import sync_playwright, expect
from economic_exchange_e2e import PERMISSIONS, activate_economic
from marketplace_browser_smoke import make_login
from multitenant import sql
from smoke import BASE, ROOT, request

ARTIFACTS = pathlib.Path(os.environ.get('STIR_E2E_ARTIFACTS', 'e2e-artifacts'))


def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]; name = 'Fulfilment Browser ' + suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('fulfil-b-{suffix}','{name}','active',false);")
    shell = f'/api/shell/v1/tenants/{tenant}'
    role = request(shell + '/roles', 'POST', {'key': 'fulfil' + suffix, 'name': 'Member', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + role['id'] + '/permissions', 'PUT', PERMISSIONS + ['stir.agreements.fulfil', 'stir.notifications.read'], admin)
    ana_login, pedro_login = make_login(admin, tenant, role['id'], 'ana', suffix), make_login(admin, tenant, role['id'], 'pedro', suffix)
    ana = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], ana_login)))
    pedro = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], pedro_login)))
    base = f'/api/stir/tenants/{tenant}'
    for person, label in ((ana, 'Ana'), (pedro, 'Pedro')):
        request(base + '/participants/me', 'PUT', {'displayName': f'{label} {suffix}'}, person['accessToken'])
    request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': name, 'unitCode': 'CRD', 'unitScale': 0}, ana['accessToken'])
    activate_economic(base, ana); activate_economic(base, pedro)
    listing = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': 'Bike repair ' + suffix, 'description': 'Browser fixture',
                      'category': 'general', 'resourceKind': 'service'}, ana['accessToken'], 201)
    negotiation = request(base + f"/listings/{listing['id']}/offers", 'POST', {'message': 'Deal?', 'proposedAmount': '30',
        'structuredTerms': {'scope': 'Brakes and gears', 'ownerTasks': 'Test-ride after the repair', 'initiatorTasks': 'Bring the bike',
                            'fulfilmentMode': 'ON_SITE', 'fulfilBy': '2030-01-10'}}, pedro['accessToken'], 201)
    agreement = request(base + f"/negotiations/{negotiation['id']}/accept", 'POST',
                        {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version']}, ana['accessToken'])

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 1600}).new_page()
            page.set_default_timeout(30000); page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(BASE)
            page.locator('input[type=email]').fill(ana_login[0]); page.locator('input[type=password]').fill(ana_login[1])
            page.locator('button[type=submit]').click()
            page.locator('.module-card').filter(has=page.get_by_role('heading', name='STIR', exact=True)).click()
            page.locator('.surface > header select').first.select_option(label=name)

            page.goto(BASE + '/stir/agreements/' + agreement['id'])
            board = page.get_by_test_id('obligation-board')
            expect(board.get_by_text('El bien o servicio', exact=True)).to_be_visible()
            expect(board.get_by_text('Tareas de quien publicó', exact=True)).to_be_visible()
            expect(board.get_by_text('El intercambio en la unidad comunitaria', exact=True)).to_be_visible()
            card = page.get_by_test_id('obligation-OBJECT')
            expect(card.get_by_test_id('obligation-status')).to_have_text('Pendiente')
            card.get_by_test_id('declaration-type').select_option('PARTIALLY_PERFORMED')
            card.get_by_test_id('declaration-note').fill('Frenos hechos; falta una pieza para el cambio')
            card.get_by_test_id('declare-submit').click()
            expect(card.get_by_test_id('obligation-status')).to_have_text('Declarada')
            print('PASS: the board shows each obligation, and a partial declaration is made through the form')

            obligation = next(o for o in request(base + f"/agreements/{agreement['id']}/obligations", token=pedro['accessToken'])['obligations'] if o['kind'] == 'OBJECT')
            request(base + f"/agreements/{agreement['id']}/obligations/{obligation['id']}/events", 'POST',
                    {'type': 'PARTIAL_RECEIVED', 'note': 'El cambio aún falla'}, pedro['accessToken'])
            page.reload()
            expect(page.get_by_test_id('obligation-OBJECT').get_by_test_id('obligation-status')).to_have_text('Cumplida en parte')
            expect(page.get_by_test_id('obligation-OBJECT').get_by_text('El cambio aún falla', exact=False)).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / 'stir-fulfilment-partial.png'), full_page=True)
            print('PASS: the counterparty\'s partial receipt shows as "Cumplida en parte" with what is missing')

            page.get_by_test_id('amendment-open').click()
            form = page.get_by_test_id('amendment-form')
            form.get_by_test_id('amend-replace-OWNER_TASKS').check()
            form.get_by_test_id('amend-add').click()
            form.get_by_test_id('amend-new-kind').select_option('OWNER_TASKS')
            form.get_by_test_id('amend-new-date').fill('2030-01-20')
            form.get_by_test_id('amend-new-description').fill('Prueba en ruta cuando llegue la pieza')
            form.get_by_test_id('amend-reason').fill('La pieza tarda una semana más')
            form.get_by_test_id('amend-submit').click()
            expect(page.get_by_test_id('amendment-PROPOSED')).to_be_visible()
            proposed = request(base + f"/agreements/{agreement['id']}/amendments", token=pedro['accessToken'])[-1]
            request(base + f"/agreements/{agreement['id']}/amendments/{proposed['id']}/accept", 'POST', {}, pedro['accessToken'])
            page.reload()
            expect(page.get_by_test_id('amendment-ACCEPTED')).to_be_visible()
            expect(page.get_by_test_id('obligation-OWNER_TASKS').get_by_text('Prueba en ruta cuando llegue la pieza')).to_be_visible()
            page.get_by_text('Obligaciones sustituidas', exact=False).click()
            expect(page.get_by_test_id('obligation-OWNER_TASKS-superseded')).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / 'stir-fulfilment-amended.png'), full_page=True)
            print('PASS: an amendment proposed in the UI, accepted by the other party, replaces only what it named')

            page.goto(BASE + '/stir/economic')
            expect(page.get_by_test_id('available-capacity')).to_contain_text('Capacidad disponible')
            expect(page.get_by_text('fijado al activar la cuenta', exact=False)).to_be_visible()
            print('PASS: the economic page shows the floor\'s origin and the available capacity')

            page.goto(BASE + '/stir/profile')
            history = page.get_by_test_id('fulfilment-history')
            expect(history.get_by_test_id('history-none')).to_have_text('Sin historia todavía.')
            history.get_by_test_id('history-share').click()  # controlled: it changes once STIR answers
            expect(history.get_by_test_id('history-share')).to_be_checked()
            page.screenshot(path=str(ARTIFACTS / 'stir-fulfilment-history.png'), full_page=True)
            print('PASS: the profile shows "no history yet", never a score, and sharing is an explicit choice')

            assert not errors, errors
            print('AGREEMENT FULFILMENT BROWSER E2E: PASS')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
