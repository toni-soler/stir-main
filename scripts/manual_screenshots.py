"""Screenshots for the STIR user manual, in Spanish and in English, taken from the real UI.

For each language it builds an isolated synthetic community (made-up people, never real accounts):
Ana offers a bike repair and Pedro agrees it; the community-unit exchange is committed; Ana declares
the repair done, Pedro says he did not receive it and opens a case that Rosa, a reviewer, takes and
rules on. Another listing has an offer waiting for Ana. Then it photographs the pages the manual
shows and writes them as JPEG into stir-frontend/src/manual/img/<language>/. Run it on an isolated
stack (STIR_TEST_URL, like the other E2E scripts) after any MVP that changes these screens:

    STIR_MANUAL_IMG=../stir-frontend/src/manual/img python scripts/manual_screenshots.py
"""
import os
import pathlib
import uuid

from playwright.sync_api import expect, sync_playwright

from agreement_cases_browser import CASES, open_stir
from economic_exchange_e2e import activate_economic, sign_authorization
from marketplace_browser_smoke import make_login
from multitenant import sql
from smoke import BASE, ROOT, request

OUT = pathlib.Path(os.environ.get('STIR_MANUAL_IMG', ROOT.parent / 'stir-frontend' / 'src' / 'manual' / 'img'))
VIEWPORT = {'width': 1280, 'height': 860}

TEXT = {
    'es': {
        'locale': 'es-ES', 'names': {'ana': 'Ana Ribas', 'pedro': 'Pedro Soler', 'marta': 'Marta Vidal', 'rosa': 'Rosa Pons', 'tomas': 'Tomás Gil'},
        'community': 'Huerta del Barrio',
        'repair': ('Reparación de bicicletas', 'Frenos, cambios y pinchazos. Traes la bici al taller del barrio.', 'Ajuste de frenos y cambios'),
        'bread': ('Pan de masa madre', 'Hogazas de 1 kg horneadas el sábado por la mañana.'),
        'tomatoes': ('Busco tomates para conserva', 'Unos 20 kg de tomate maduro para hacer conserva en agosto.'),
        'offer': 'Me quedo dos hogazas cada sábado de este mes.',
        'statement': 'Dejé la bici el lunes y me la devolvieron sin reparar.',
        'evidence': 'Foto de la bici con el freno suelto, tomada al recogerla.',
        'finding': 'Las dos partes coinciden en que la bici se devolvió sin el ajuste acordado.',
        'marker': 'Mis publicaciones',
    },
    'en': {
        'locale': 'en-GB', 'names': {'ana': 'Ana Ribas', 'pedro': 'Peter Soler', 'marta': 'Martha Vidal', 'rosa': 'Rose Pons', 'tomas': 'Tom Gil'},
        'community': 'Neighbourhood Garden',
        'repair': ('Bicycle repairs', 'Brakes, gears and punctures. Bring your bike to the neighbourhood workshop.', 'Brake and gear adjustment'),
        'bread': ('Sourdough bread', '1 kg loaves baked on Saturday morning.'),
        'tomatoes': ('Looking for tomatoes to preserve', 'About 20 kg of ripe tomatoes to make preserves in August.'),
        'offer': 'I would take two loaves every Saturday this month.',
        'statement': 'I left the bike on Monday and got it back unrepaired.',
        'evidence': 'Photo of the bike with the loose brake, taken when I collected it.',
        'finding': 'Both parties agree the bike was returned without the agreed adjustment.',
        'marker': 'My listings',
    },
}


def community(language):
    """An isolated community with an agreement, a committed exchange, a case and an open negotiation."""
    text = TEXT[language]
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]
    name = f"{text['community']} {suffix[:4]}"
    tenant = sql(f"select tenant_id from idax_core.tenant_create('manual-{language}-{suffix}','{name}','active',false);")
    shell = f'/api/shell/v1/tenants/{tenant}'
    role = request(shell + '/roles', 'POST', {'key': 'manual' + suffix, 'name': 'Member', 'description': 'Manual screenshots', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + role['id'] + '/permissions', 'PUT', CASES, admin)
    logins = {label: make_login(admin, tenant, role['id'], label, suffix) for label in text['names']}
    people = {label: request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], login))) for label, login in logins.items()}
    ana, pedro, marta, rosa, tomas = (people[k] for k in ('ana', 'pedro', 'marta', 'rosa', 'tomas'))
    sql(f"update idax_core.tenant_user set role='owner' where tenant_id='{tenant}' and user_id='{marta['user']['id']}';")
    base = f'/api/stir/tenants/{tenant}'
    for label, person in people.items():
        request(base + '/participants/me', 'PUT', {'displayName': text['names'][label]}, person['accessToken'])
    request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': name, 'unitCode': 'CRD', 'unitScale': 0}, marta['accessToken'])
    keys = {label: activate_economic(base, people[label]) for label in ('ana', 'pedro', 'rosa', 'tomas')}
    request(base + '/case-review', 'PUT', {'reviewerUserIds': [rosa['user']['id'], tomas['user']['id']], 'threshold': 1, 'appealWindowHours': 72}, marta['accessToken'])

    title, description, scope = text['repair']
    repair = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': title, 'description': description, 'category': 'general', 'resourceKind': 'service'}, ana['accessToken'], 201)
    bread = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': text['bread'][0], 'description': text['bread'][1], 'category': 'general', 'resourceKind': 'physical'}, ana['accessToken'], 201)
    request(base + '/listings', 'POST', {'direction': 'WANTED', 'title': text['tomatoes'][0], 'description': text['tomatoes'][1], 'category': 'general', 'resourceKind': 'physical'}, pedro['accessToken'], 201)
    negotiation = request(base + f"/listings/{repair['id']}/offers", 'POST', {'message': scope, 'proposedAmount': '30',
        'structuredTerms': {'scope': scope, 'fulfilmentMode': 'ON_SITE', 'fulfilBy': '2030-01-10'}}, pedro['accessToken'], 201)
    agreement = request(base + f"/negotiations/{negotiation['id']}/accept", 'POST', {'offerId': negotiation['offers'][-1]['id'], 'expectedVersion': negotiation['version']}, ana['accessToken'])
    trade = base + f"/agreements/{agreement['id']}/trade"
    request(trade + '/activate', 'POST', {}, pedro['accessToken'])
    for label in ('pedro', 'ana'):
        payload = request(trade + '/signing-payload', token=people[label]['accessToken'])
        request(trade + '/authorizations', 'POST', {'credentialId': keys[label][2], 'signatureBase64url': sign_authorization(keys[label][0], payload['authorizationPayload'])}, people[label]['accessToken'])
    request(trade + '/commit', 'POST', {}, ana['accessToken'])
    obligations = request(base + f"/agreements/{agreement['id']}/obligations", token=pedro['accessToken'])['obligations']
    obj = next(o for o in obligations if o['kind'] == 'OBJECT')
    request(base + f"/agreements/{agreement['id']}/obligations/{obj['id']}/events", 'POST', {'type': 'PERFORMED'}, ana['accessToken'])
    request(base + f"/agreements/{agreement['id']}/obligations/{obj['id']}/events", 'POST', {'type': 'NOT_RECEIVED'}, pedro['accessToken'])
    case = request(base + f"/agreements/{agreement['id']}/cases", 'POST', {'obligationId': obj['id'], 'claim': 'NON_CONFORMITY', 'statement': text['statement']}, pedro['accessToken'], (200, 201))
    request(base + f"/cases/{case['id']}/evidence", 'POST', {'description': text['evidence'], 'visibility': 'PARTIES_AND_REVIEWERS'}, pedro['accessToken'], (200, 201))
    request(base + f"/cases/{case['id']}/take", 'POST', {}, rosa['accessToken'], (200, 201))
    request(base + f"/cases/{case['id']}/findings", 'POST', {'effect': 'ESTABLISHED', 'economicCorrection': 'FULL_REVERSAL', 'reasoning': text['finding']}, rosa['accessToken'], (200, 201))
    open_offer = request(base + f"/listings/{bread['id']}/offers", 'POST', {'message': text['offer'], 'proposedAmount': '8', 'quantity': '2', 'unitLabel': 'kg',
        'structuredTerms': {'scope': text['offer'], 'fulfilmentMode': 'PICKUP', 'fulfilBy': '2030-02-01'}}, pedro['accessToken'], 201)
    return {'name': name, 'logins': logins, 'agreement': agreement['id'], 'case': case['id'], 'bread': bread['id'], 'negotiation': open_offer['id']}


def shoot(page, path, focus=None):
    """A JPEG of STIR itself (not the platform around it), from the part the manual talks about."""
    page.wait_for_load_state('load')
    page.wait_for_timeout(1500)
    target = (focus if focus is not None else page.locator('.stir')).first
    target.scroll_into_view_if_needed()
    page.evaluate('y => window.scrollBy(0, y)', target.bounding_box()['y'] - (40 if focus is not None else 0))
    page.wait_for_timeout(300)
    box = page.locator('.stir').first.bounding_box()
    top = max(0, box['y'])
    page.screenshot(path=str(path), type='jpeg', quality=80, clip={'x': box['x'], 'y': top, 'width': box['width'], 'height': 760 - top})


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for language, text in TEXT.items():
                data = community(language)
                folder = OUT / language
                folder.mkdir(parents=True, exist_ok=True)

                def person(label):
                    page = browser.new_context(locale=text['locale'], viewport=VIEWPORT).new_page()
                    page.set_default_timeout(30000)
                    page.on('pageerror', lambda e: errors.append(f'{language}/{label}: {e}'))
                    open_stir(page, *data['logins'][label], data['name'])
                    expect(page.get_by_text(text['marker'], exact=True).first).to_be_visible()
                    return page

                pedro, ana = person('pedro'), person('ana')
                pedro.goto(BASE + '/stir/home'); shoot(pedro, folder / 'stir-home.jpg')
                pedro.goto(BASE + '/stir'); shoot(pedro, folder / 'stir-marketplace.jpg', pedro.locator('.stir-filters'))
                pedro.goto(BASE + '/stir/new'); shoot(pedro, folder / 'stir-create.jpg')
                pedro.goto(BASE + '/stir/listing/' + data['bread']); shoot(pedro, folder / 'stir-listing.jpg')
                ana.goto(BASE + '/stir/negotiations/' + data['negotiation']); shoot(ana, folder / 'stir-negotiation.jpg')
                pedro.goto(BASE + '/stir/economic'); shoot(pedro, folder / 'stir-economic.jpg')
                pedro.goto(BASE + '/stir/agreements/' + data['agreement']); shoot(pedro, folder / 'stir-agreement.jpg', pedro.get_by_test_id('obligation-board'))
                pedro.goto(BASE + '/stir/cases/' + data['case']); shoot(pedro, folder / 'stir-case.jpg')
                pedro.goto(BASE + '/stir/profile'); shoot(pedro, folder / 'stir-profile.jpg')
                print(f'PASS: {language} screenshots written to {folder}')
        finally:
            browser.close()
    assert not errors, errors
    print('STIR MANUAL SCREENSHOTS: PASS')


if __name__ == '__main__':
    main()
