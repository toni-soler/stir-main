"""Real browser proof of the Consent/Retention UI (CONSENT_RETENTION.md): a party viewing and
withdrawing their own consent from the profile page, and a publisher viewing/using the retention
panel from the references page - all through the actually rendered UI, no JavaScript page errors.
Setup (tenant, roles, users, definition, a real bilateral Agreement, and a backdated observation
for the anonymization path) reuses the same real HTTP calls consent_retention_e2e.py already
proves end to end; this script's job is only to prove the new UI renders and works.
"""
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql
from marketplace_browser_smoke import make_login
from community_references_e2e import MEMBER


def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]; name = 'ConsentRetention Browser ' + suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('cr-b-{suffix}','{name}','active',false);")
    shell = f'/api/shell/v1/tenants/{tenant}'
    manager_role = request(shell + '/roles', 'POST', {'key': 'manager' + suffix, 'name': 'Manager', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + manager_role['id'] + '/permissions', 'PUT',
        MEMBER + ['stir.references.publish', 'stir.retention.manage', 'stir.consent.manage'], admin)
    member_role = request(shell + '/roles', 'POST', {'key': 'member' + suffix, 'name': 'Member', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + member_role['id'] + '/permissions', 'PUT', MEMBER + ['stir.consent.manage'], admin)
    manager_login = make_login(admin, tenant, manager_role['id'], 'manager', suffix)
    pedro_login = make_login(admin, tenant, member_role['id'], 'pedro', suffix)
    manager = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], manager_login)))
    pedro = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], pedro_login)))
    m, base = manager['accessToken'], f'/api/stir/tenants/{tenant}'
    binding = request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': name, 'unitCode': 'CRB', 'unitScale': 2}, m)
    community = binding['communityId']
    definition = request(base + '/references', 'POST', {'name': 'Wool ' + suffix, 'scope': 'One kilogram of raw wool',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 'kg'}, m)
    definition_id = definition['id']
    listing = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': 'Wool ' + suffix,
        'description': 'Test bundle', 'category': 'home', 'resourceKind': 'physical', 'referenceDefinitionId': definition_id}, m, 201)

    def agree(amount):
        n = request(base + '/listings/' + listing['id'] + '/offers', 'POST', {'message': 'Offer',
            'quantity': '1', 'unitLabel': 'kg', 'proposedAmount': amount, 'proposedUnitRef': definition['unit_ref'],
            'shareReferenceObservation': True}, pedro['accessToken'], 201)
        return request(base + '/negotiations/' + n['id'] + '/accept', 'POST', {'offerId': n['offers'][-1]['id'],
            'expectedVersion': n['version'], 'shareReferenceObservation': True}, m)

    # A real bilateral Agreement, whose consent Pedro will withdraw through the profile UI.
    agreement = agree('30')
    observation = next(o for o in request(base + '/references/' + definition_id + '/observations', token=m)
                        if o['source_id'] == agreement['id'])

    # A second, backdated observation past the 90-day floor, with no market-integrity case - the
    # manager will anonymize this one through the retention panel. Real HTTP cannot backdate
    # observed_at (by design); the same session-level trigger bypass consent_retention_e2e.py uses.
    old_agreement = agree('35')
    old_observation = next(o for o in request(base + '/references/' + definition_id + '/observations', token=m)
                            if o['source_id'] == old_agreement['id'])
    sql(f"set session_replication_role='replica'; "
        f"update stir.reference_observation set observed_at=now()-interval '200 days' where tenant_id='{tenant}' and id='{old_observation['id']}'; "
        f"set session_replication_role='origin';")
    request(base + '/references/retention/policy/' + community, 'POST',
        {'retentionPeriodDays': 90, 'explanation': 'Initial policy for this browser test'}, m)

    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        try:
            def login(page, credentials):
                page.set_default_timeout(30000); page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto('http://localhost:8089')
                page.locator('input[type=email]').fill(credentials[0]); page.locator('input[type=password]').fill(credentials[1])
                page.locator('button[type=submit]').click()
                page.locator('.module-card').filter(has=page.get_by_role('heading', name='STIR', exact=True)).click()
                page.locator('.surface > header select').first.select_option(label=name)

            # --- Pedro: view and withdraw his own consent from the profile page ---
            pedro_page = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 1400}).new_page()
            login(pedro_page, pedro_login)
            pedro_page.goto('http://localhost:8089/stir/profile')
            consent_panel = pedro_page.locator('section').filter(has_text='Mi consentimiento a la evidencia de referencias')
            expect(consent_panel.get_by_text('Wool ' + suffix, exact=False).first).to_be_visible()
            consent_panel.get_by_role('button', name='Retirar', exact=True).first.click()
            expect(consent_panel.get_by_text('Esta observación dejará de usarse', exact=False)).to_be_visible()
            consent_panel.get_by_label('Motivo (opcional)').fill('Ya no quiero compartir esto')
            consent_panel.get_by_role('button', name='Confirmar retirada', exact=True).click()
            expect(consent_panel.get_by_text('Has retirado este consentimiento', exact=False)).to_be_visible()

            # --- Manager: use the retention panel from the references page ---
            manager_page = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1800}).new_page()
            login(manager_page, manager_login)
            manager_page.goto('http://localhost:8089/stir/references')
            manager_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            manager_page.get_by_text('Conservación', exact=True).click()
            retention_panel = manager_page.locator('details').filter(has_text='Conservación').first
            expect(retention_panel.get_by_text('Política actual', exact=False)).to_be_visible()
            due_row = retention_panel.locator('li').filter(has_text=old_observation['id'][:8])
            expect(due_row).to_be_visible()
            due_row.get_by_role('button', name='Anonimizar', exact=True).click()
            expect(due_row).not_to_be_visible()

            assert not errors, errors
            print('PASS BROWSER CONSENT/RETENTION: Pedro viewed and withdrew his own consent with the plain-language '
                  'impact notice shown first; the manager viewed the retention policy and anonymized an eligible '
                  'observation from the rendered due-for-anonymization list - no page errors.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
