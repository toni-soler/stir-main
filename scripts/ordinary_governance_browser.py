"""Real browser proof of the Ordinary Community Governance UI (ORDINARY_GOVERNANCE.md): enabling
governance, building an electorate, setting a voting policy, starting a vote on a real reference
proposal, and two independent voter sessions casting real votes that update the tally live - all
through the actually rendered /stir/references page, no JavaScript page errors. Setup (tenant,
roles, users, the definition and its reference proposal) reuses the same real HTTP calls
ordinary_governance_e2e.py already proves end to end; this script's job is only to prove the new UI
renders and works. The real time-boxed deadline (quorum/majority determined only after
voting_closes_at passes) is not fast-forwarded here, the same real-clock limitation every other
browser script in this session already works around - that arithmetic is proven directly against
PostgreSQL with a backdated voting_closes_at (OrdinaryGovernancePostgresTest).
"""
import secrets
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql
from marketplace_browser_smoke import make_login
from community_references_e2e import MEMBER

def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]; name = 'Ordinary Gov Browser '+suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('ordgov-b-{suffix}','{name}','active',false);")
    shell = f'/api/shell/v1/tenants/{tenant}'
    manager_role = request(shell+'/roles', 'POST', {'key': 'manager'+suffix, 'name': 'Manager', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell+'/roles/'+manager_role['id']+'/permissions', 'PUT',
        MEMBER + ['stir.references.publish', 'stir.governance.manage', 'stir.governance.vote'], admin)
    voter_role = request(shell+'/roles', 'POST', {'key': 'voter'+suffix, 'name': 'Voter', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell+'/roles/'+voter_role['id']+'/permissions', 'PUT', MEMBER + ['stir.governance.vote'], admin)
    manager_login = make_login(admin, tenant, manager_role['id'], 'manager', suffix)
    v1_login = make_login(admin, tenant, voter_role['id'], 'v1', suffix)
    v2_login = make_login(admin, tenant, voter_role['id'], 'v2', suffix)
    manager = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], manager_login)))
    v1 = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], v1_login)))
    v2 = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], v2_login)))
    m, base = manager['accessToken'], f'/api/stir/tenants/{tenant}'
    binding = request(base+'/economic/marketplace/bootstrap', 'POST', {'communityName': name, 'unitCode': 'OGB', 'unitScale': 2}, m)
    community = binding['communityId']
    definition = request(base+'/references', 'POST', {'name': 'Wheat '+suffix, 'scope': 'One tonne of wheat',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 't'}, m)
    definition_id = definition['id']
    reference_proposal = request(base+'/references/'+definition_id+'/proposals', 'POST', {'kind': 'CONVENTION',
        'lowerValue': '200', 'upperValue': '200', 'explanation': 'Community convention, not statistics',
        'origin': 'Assembly', 'validDays': 90}, m)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        errors = []
        try:
            def login(page, credentials):
                page.set_default_timeout(30000); page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto('http://localhost:8089')
                page.locator('input[type=email]').fill(credentials[0]); page.locator('input[type=password]').fill(credentials[1])
                page.locator('button[type=submit]').click()
                page.locator('.module-card').filter(has=page.get_by_role('heading', name='STIR', exact=True)).click()
                page.locator('.surface > header select').first.select_option(label=name)

            manager_page = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1800}).new_page()
            v1_page = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1800}).new_page()
            v2_page = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1800}).new_page()
            login(manager_page, manager_login)
            manager_page.goto('http://localhost:8089/stir/references')
            manager_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            manager_page.get_by_text('Gobernanza comunitaria ordinaria', exact=True).click()
            gov = manager_page.locator('details').filter(has_text='Gobernanza comunitaria ordinaria').first

            # --- Enable governance for this community ---
            gov.get_by_role('checkbox').click()
            expect(gov.get_by_text('La gobernanza ordinaria está desactivada', exact=False)).not_to_be_visible()

            # --- Build the electorate: manager + both voters ---
            gov.get_by_text('Censo electoral', exact=True).click()
            electorate = gov.locator('details').filter(has_text='Censo electoral')
            for user_id in [manager['user']['id'], v1['user']['id'], v2['user']['id']]:
                electorate.get_by_label('ID de usuario', exact=True).fill(user_id)
                electorate.get_by_role('button', name='Añadir miembro', exact=True).click()
                # The button is disabled while its own request is in flight - wait for this
                # member's own id to actually render in the list before starting the next add,
                # rather than racing three rapid submits against the same async form.
                expect(electorate.get_by_text(user_id[:8], exact=False)).to_be_visible()

            # --- Set the voting policy: quorum 1/2, approval 2/3 ---
            gov.get_by_text('Política de votación', exact=True).click()
            policy_form = gov.locator('details').filter(has_text='Política de votación')
            policy_form.get_by_label('Numerador del quorum', exact=True).fill('1')
            policy_form.get_by_label('Denominador del quorum', exact=True).fill('2')
            policy_form.get_by_label('Numerador de aprobación', exact=True).fill('2')
            policy_form.get_by_label('Denominador de aprobación', exact=True).fill('3')
            policy_form.get_by_label('Motivo de esta política', exact=True).fill('v0.1 initial policy - browser fixture')
            policy_form.get_by_role('button', name='Guardar política', exact=True).click()
            expect(policy_form.get_by_text('Política actual', exact=False)).to_be_visible()

            # --- Start an ordinary vote on the real reference proposal ---
            gov.get_by_text('Iniciar una votación ordinaria', exact=True).click()
            start_vote = gov.locator('details').filter(has_text='Iniciar una votación ordinaria')
            start_vote.get_by_role('button', name='Iniciar votación', exact=True).click()
            expect(gov.get_by_text('Publicar una referencia de valor comunitario', exact=True)).to_be_visible()
            expect(gov.get_by_text('Tamaño del censo: 3', exact=False)).to_be_visible()

            # --- Two independent voter sessions cast real votes; the tally updates live ---
            login(v1_page, v1_login)
            v1_page.goto('http://localhost:8089/stir/references')
            v1_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            v1_page.get_by_text('Gobernanza comunitaria ordinaria', exact=True).click()
            v1_gov = v1_page.locator('details').filter(has_text='Gobernanza comunitaria ordinaria').first
            v1_gov.get_by_role('button', name='Votar a favor', exact=True).click()
            expect(v1_gov.get_by_text('A favor: 1', exact=False)).to_be_visible()

            login(v2_page, v2_login)
            v2_page.goto('http://localhost:8089/stir/references')
            v2_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            v2_page.get_by_text('Gobernanza comunitaria ordinaria', exact=True).click()
            v2_gov = v2_page.locator('details').filter(has_text='Gobernanza comunitaria ordinaria').first
            v2_gov.get_by_role('button', name='Votar a favor', exact=True).click()
            expect(v2_gov.get_by_text('A favor: 2', exact=False)).to_be_visible()

            # --- A publisher trying to bypass the vote with the old direct-publish button fails ---
            manager_page.reload()
            manager_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            manager_page.get_by_label('Decisión de publicación', exact=True).fill('Bypass attempt')
            publish_buttons = manager_page.get_by_role('button', name='Publicar nueva versión', exact=True)
            if publish_buttons.count() > 0:
                publish_buttons.first.click()
                expect(manager_page.get_by_text('gobernanza ordinaria', exact=False)).to_be_visible()

            assert errors == [], f'Page errors: {errors}'
            print('PASS BROWSER ORDINARY GOVERNANCE: governance enabled, electorate built, voting policy '
                  'saved, a real ordinary vote started on a real reference proposal, two independent '
                  'voter sessions cast real votes with the tally updating live, direct publish blocked '
                  'once governance is active - all through the rendered UI, no page errors.')
        finally:
            browser.close()

if __name__ == '__main__':
    main()
