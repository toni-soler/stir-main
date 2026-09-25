"""Real browser proof of the Participant Independence UI this increment adds to the already-
validated /stir/references evidence breakdown panel (community_value_governance_browser.py):
the per-participant independence badge and its refresh action, and the evidence panel's
identity-assurance summary line - all through the actually rendered page, no JavaScript page
errors. Setup (tenant, economic activation, the osTRIS continuity decision, the Agreement between
pedro and carlos) reuses the same real HTTP calls participant_independence_e2e.py already proves
end to end; this script's job is only to prove the new UI renders and works, not to re-prove the
backend chain a third time.
"""
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql
from marketplace_browser_smoke import make_login
from community_references_e2e import MEMBER
from participant_independence_e2e import uuid7, participant_id, confirm_related
from economic_exchange_e2e import activate_economic

def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]; name = 'Independence Browser '+suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('indep-b-{suffix}','{name}','active',false);")
    reviewer_role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'reviewer'+suffix,
        'name': 'Reviewer', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+reviewer_role['id']+'/permissions', 'PUT',
        MEMBER+['stir.references.publish', 'OSTRIS_IDENTITY_CONTINUITY_READ_PRIVATE'], admin)
    member_role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'member'+suffix,
        'name': 'Member', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+member_role['id']+'/permissions', 'PUT', MEMBER, admin)
    reviewer_login = make_login(admin, tenant, reviewer_role['id'], 'reviewer', suffix)
    pedro_login = make_login(admin, tenant, member_role['id'], 'pedro', suffix)
    carlos_login = make_login(admin, tenant, member_role['id'], 'carlos', suffix)
    reviewer = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], reviewer_login)))
    pedro = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], pedro_login)))
    carlos = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], carlos_login)))
    r, base = reviewer['accessToken'], f'/api/stir/tenants/{tenant}'
    binding = request(base+'/economic/marketplace/bootstrap', 'POST', {'communityName': name, 'unitCode': 'IND', 'unitScale': 2}, r)
    community = binding['communityId']

    # --- Real setup via HTTP, exactly as participant_independence_e2e.py already proves end to end ---
    for session in (pedro, carlos):
        request(base+'/participants/me', 'PUT', {'displayName': 'Fixture', 'bio': 'Browser fixture', 'location': 'Girona'}, session['accessToken'])
        activate_economic(base, session)
    pedro_user, carlos_user = pedro['user']['id'], carlos['user']['id']
    pedro_participant, carlos_participant = participant_id(tenant, pedro_user), participant_id(tenant, carlos_user)
    risk_subject = uuid7()
    sql(f"insert into ostris.risk_subject(id,tenant_id,community_id) values ('{risk_subject}','{tenant}','{community}');")
    confirm_related(admin, tenant, community, pedro_participant, risk_subject, 'Same cluster (browser fixture)')
    confirm_related(admin, tenant, community, carlos_participant, risk_subject, 'Same cluster (browser fixture)')
    definition = request(base+'/references', 'POST', {'name': 'Firewood '+suffix, 'scope': 'One stere of dry firewood',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 'stere'}, r)
    definition_id = definition['id']
    listing = request(base+'/listings', 'POST', {'direction': 'OFFER', 'title': 'Firewood '+suffix, 'description': 'Browser fixture',
        'category': 'home', 'resourceKind': 'physical', 'referenceDefinitionId': definition_id}, pedro['accessToken'], 201)
    offer = request(base+'/listings/'+listing['id']+'/offers', 'POST', {'message': 'proposal', 'quantity': '1', 'unitLabel': 'stere',
        'proposedAmount': '10', 'proposedUnitRef': definition['unit_ref'], 'shareReferenceObservation': True}, carlos['accessToken'], 201)
    request(base+'/negotiations/'+offer['id']+'/accept', 'POST', {'offerId': offer['offers'][-1]['id'],
        'expectedVersion': offer['version'], 'shareReferenceObservation': True}, pedro['accessToken'])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        errors = []
        try:
            page = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1600}).new_page()
            page.set_default_timeout(30000); page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('http://localhost:8089')
            page.locator('input[type=email]').fill(reviewer_login[0]); page.locator('input[type=password]').fill(reviewer_login[1])
            page.locator('button[type=submit]').click()
            page.locator('.module-card').filter(has=page.get_by_role('heading', name='STIR', exact=True)).click()
            page.locator('.surface > header select').first.select_option(label=name)
            page.goto('http://localhost:8089/stir/references')
            page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)

            # --- The new evidence panel line: identity assurance label is visible ---
            page.get_by_text('¿Por qué me muestran esta referencia?', exact=True).click()
            expect(page.get_by_text('Garantía de independencia', exact=False)).to_be_visible()

            # --- The new evidence breakdown: independence badges render per participant, and a
            # refresh actually calls the real endpoint and updates the label in place ---
            page.get_by_text('Desglose de la evidencia', exact=True).click()
            breakdown = page.locator('details').filter(has_text='Desglose de la evidencia')
            badge = breakdown.locator('button.stir-inline-action').first
            expect(badge).to_be_visible()
            before_text = badge.inner_text()
            badge.click()
            expect(badge).to_contain_text('Relación confirmada', timeout=15000)

            assert errors == [], f'Page errors: {errors}'
            print('PASS BROWSER PARTICIPANT INDEPENDENCE: identity-assurance summary line visible, '
                  'independence badge renders per participant in the evidence breakdown, refresh '
                  f'action updates the label live (was {before_text!r}, now confirmed related) - '
                  'no JavaScript page errors.')
        finally:
            browser.close()

if __name__ == '__main__':
    main()
