"""Real browser proof of the Multi-Source Value Evidence UI (MULTI_SOURCE_VALUE_EVIDENCE.md):
publishing a LISTING and a WANTED with their own indicative price and explicit per-party consent
from the actually rendered listing form, seeing the source/lineage/seed breakdown render on the
references page, proving the reference policy panel no longer silently resets
listingSourceEnabled/wantedSourceEnabled on an unrelated field edit (the regression this session
fixed in PolicyForm), and proposing a Community Seed from the ordinary-governance panel - all
through the rendered UI, no JavaScript page errors. Setup (tenant, roles, users, marketplace,
reference definition, enabling both sources) reuses the same real HTTP calls
multi_source_value_evidence_e2e.py already proves end to end; this script's job is only to prove
the new UI renders and works.

Ordering note: snapshot()/sourceBreakdown cache their result per calendar day, the first time
they are computed for a definition (reference_snapshot keyed by cutoff=start of day) - a same-day
observation is structurally invisible to a snapshot already cached earlier that same day, even
after backdating it (CLAUDE.md; community_references_e2e.py's own daily-reconstruction proof).
So this script never visits /stir/references (which computes that snapshot) until AFTER both
listings are created and their observations backdated - visiting it any earlier here would freeze
an empty snapshot for the rest of the day, same trap the first draft of this script hit.

Community Seed close/execute mechanics are proven directly against PostgreSQL with a backdated
voting_closes_at (MultiSourceValueEvidencePostgresTest), same real-clock limitation as every other
governance browser script in this session.
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
    suffix = uuid.uuid4().hex[:8]; name = 'MultiSource Browser ' + suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('mvse-b-{suffix}','{name}','active',false);")
    shell = f'/api/shell/v1/tenants/{tenant}'
    manager_role = request(shell + '/roles', 'POST', {'key': 'manager' + suffix, 'name': 'Manager', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + manager_role['id'] + '/permissions', 'PUT',
        MEMBER + ['stir.references.publish', 'stir.governance.manage', 'stir.governance.vote'], admin)
    member_role = request(shell + '/roles', 'POST', {'key': 'member' + suffix, 'name': 'Member', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + member_role['id'] + '/permissions', 'PUT', MEMBER, admin)
    manager_login = make_login(admin, tenant, manager_role['id'], 'ana', suffix)
    pedro_login = make_login(admin, tenant, member_role['id'], 'pedro', suffix)
    manager = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], manager_login)))
    pedro = request('/api/shell/v1/auth/login', 'POST', dict(zip(['email', 'password'], pedro_login)))
    m, base = manager['accessToken'], f'/api/stir/tenants/{tenant}'
    binding = request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': name, 'unitCode': 'MVB', 'unitScale': 2}, m)
    definition = request(base + '/references', 'POST', {'name': 'Wool ' + suffix, 'scope': 'One kilogram of raw wool',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 'kg'}, m)
    definition_id = definition['id']
    # Enable LISTING/WANTED as eligible sources via real HTTP setup, before any UI page ever
    # computes today's snapshot for this definition - the UI regression fix itself is proven later
    # by editing an unrelated field through the rendered form and confirming these stay enabled.
    request(base + '/references/' + definition_id + '/policies', 'POST', {'windowDays': 90, 'minimumObservations': 5,
        'minimumParticipants': 6, 'maximumParticipantShare': '0.40', 'freshnessDays': 30,
        'explanation': 'Enable listing/wanted sources - browser fixture', 'listingSourceEnabled': True, 'wantedSourceEnabled': True}, m)

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

            ana_page = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1900}).new_page()
            pedro_page = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1900}).new_page()
            login(ana_page, manager_login)

            # --- Ana publishes a real LISTING with an indicative price and explicit consent ---
            ana_page.goto('http://localhost:8089/stir/new')
            ana_page.get_by_label('Título', exact=False).fill('Wool bundle ' + suffix)
            ana_page.get_by_label('Descripción', exact=False).fill('Real fleece, browser fixture')
            ana_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            ana_page.get_by_label('Importe indicativo', exact=True).fill('40')
            ana_page.get_by_label('Cantidad indicativa', exact=True).fill('1')
            ana_page.get_by_label('Unidad de cantidad', exact=True).fill('kg')
            ana_page.get_by_label('Referencia de unidad de cuenta', exact=True).fill(definition['unit_ref'])
            ana_page.get_by_label('Usar el precio indicativo de este anuncio como evidencia de referencia comunitaria', exact=True).check()
            ana_page.get_by_role('button', name='Guardar y añadir fotos', exact=True).click()
            expect(ana_page.get_by_role('button', name='Listo', exact=True)).to_be_visible()
            ana_page.get_by_role('button', name='Listo', exact=True).click()

            # --- Pedro publishes a real WANTED with his own indicative price ---
            login(pedro_page, pedro_login)
            pedro_page.goto('http://localhost:8089/stir/new')
            pedro_page.get_by_label('Quiero', exact=False).select_option(label='BUSCO')
            pedro_page.get_by_label('Título', exact=False).fill('Looking for wool ' + suffix)
            pedro_page.get_by_label('Descripción', exact=False).fill('Need fleece, browser fixture')
            pedro_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            pedro_page.get_by_label('Importe indicativo', exact=True).fill('38')
            pedro_page.get_by_label('Cantidad indicativa', exact=True).fill('1')
            pedro_page.get_by_label('Unidad de cantidad', exact=True).fill('kg')
            pedro_page.get_by_label('Referencia de unidad de cuenta', exact=True).fill(definition['unit_ref'])
            pedro_page.get_by_label('Usar el precio indicativo de este anuncio como evidencia de referencia comunitaria', exact=True).check()
            pedro_page.get_by_role('button', name='Guardar y añadir fotos', exact=True).click()
            expect(pedro_page.get_by_role('button', name='Listo', exact=True)).to_be_visible()

            # A same-day observation is structurally invisible to snapshot()/sourceBreakdown
            # (observed_at < today's cutoff, by design). Real HTTP cannot backdate observed_at
            # (there is no endpoint for it, and there should never be one); the same session-level
            # trigger bypass every other fixture in this session already uses for the same reason.
            # This runs BEFORE the first-ever /stir/references visit for this definition below, so
            # that visit's own snapshot computation already sees the backdated observations.
            sql(f"set session_replication_role='replica'; "
                f"update stir.reference_observation set observed_at=now()-interval '1 day' where tenant_id='{tenant}' and definition_id='{definition_id}'; "
                f"set session_replication_role='origin';")

            # --- Ana sees the source/lineage breakdown render, LISTING and WANTED kept separate,
            # never blended into one AGREEMENT-only median (MULTI_SOURCE_VALUE_EVIDENCE.md) ---
            ana_page.goto('http://localhost:8089/stir/references')
            ana_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            expect(ana_page.get_by_text('Por fuente', exact=False)).to_be_visible()
            expect(ana_page.get_by_text('Anuncios: 1', exact=False)).to_be_visible()
            expect(ana_page.get_by_text('Búsquedas: 1', exact=False)).to_be_visible()
            expect(ana_page.get_by_text('Evidencia de anuncios (LISTING)', exact=False)).to_be_visible()
            expect(ana_page.get_by_text('Evidencia de búsquedas (WANTED)', exact=False)).to_be_visible()

            # --- PolicyForm regression proof: an unrelated field edit through the direct publisher
            # path must never silently reset listingSourceEnabled/wantedSourceEnabled back to
            # disabled (the bug this session found and fixed) ---
            ana_page.get_by_text('Política de la referencia', exact=True).click()
            policy_panel = ana_page.locator('details').filter(has_text='Política de la referencia').first
            expect(policy_panel.get_by_label('Aceptar evidencia de anuncios (LISTING)', exact=True)).to_be_checked()
            expect(policy_panel.get_by_label('Aceptar evidencia de búsquedas (WANTED)', exact=True)).to_be_checked()
            policy_panel.get_by_label('Motivo de esta política', exact=True).fill('Unrelated edit - browser fixture')
            policy_panel.get_by_role('button', name='Guardar política', exact=True).click()
            ana_page.reload()
            ana_page.get_by_label('Definición comparable', exact=False).select_option(value=definition_id)
            ana_page.get_by_text('Política de la referencia', exact=True).click()
            policy_panel = ana_page.locator('details').filter(has_text='Política de la referencia').first
            expect(policy_panel.get_by_label('Aceptar evidencia de anuncios (LISTING)', exact=True)).to_be_checked()
            expect(policy_panel.get_by_label('Aceptar evidencia de búsquedas (WANTED)', exact=True)).to_be_checked()

            # --- Community Seed proposal through the ordinary-governance panel ---
            ana_page.get_by_text('Gobernanza comunitaria ordinaria', exact=True).click()
            gov = ana_page.locator('details').filter(has_text='Gobernanza comunitaria ordinaria').first
            gov.get_by_role('checkbox').first.click()
            gov.get_by_text('Censo electoral', exact=True).click()
            electorate = gov.locator('details').filter(has_text='Censo electoral')
            for user_id in [manager['user']['id'], pedro['user']['id']]:
                electorate.get_by_label('ID de usuario', exact=True).fill(user_id)
                electorate.get_by_role('button', name='Añadir miembro', exact=True).click()
                expect(electorate.get_by_text(user_id[:8], exact=False)).to_be_visible()
            gov.get_by_text('Política de votación', exact=True).click()
            voting_policy = gov.locator('details').filter(has_text='Política de votación')
            voting_policy.get_by_label('Numerador del quorum', exact=True).fill('1')
            voting_policy.get_by_label('Denominador del quorum', exact=True).fill('2')
            voting_policy.get_by_label('Numerador de aprobación', exact=True).fill('2')
            voting_policy.get_by_label('Denominador de aprobación', exact=True).fill('3')
            voting_policy.get_by_label('Motivo de esta política', exact=True).fill('v0.1 initial policy - browser fixture')
            voting_policy.get_by_role('button', name='Guardar política', exact=True).click()
            expect(voting_policy.get_by_text('Política actual', exact=False)).to_be_visible()

            gov.get_by_text('Iniciar una votación ordinaria', exact=True).click()
            start_vote = gov.locator('details').filter(has_text='Iniciar una votación ordinaria')
            start_vote.get_by_role('button', name='Proponer una orientación inicial comunitaria', exact=True).click()
            seed_form = start_vote.locator('form')
            seed_form.get_by_label('Valor orientativo inferior', exact=True).fill('30')
            seed_form.get_by_label('Valor orientativo superior', exact=True).fill('30')
            seed_form.get_by_label('Justificación', exact=True).fill('Initial orientation pending real evidence - browser fixture')
            seed_form.get_by_label('Base de gobernanza', exact=True).fill('Founding assembly')
            seed_form.get_by_role('button', name='Enviar propuesta', exact=True).click()
            expect(gov.get_by_text('Publicación de orientación inicial comunitaria', exact=True)).to_be_visible()
            expect(gov.get_by_text('Abierta a votación', exact=True)).to_be_visible()

            assert errors == [], f'Page errors: {errors}'
            print('PASS BROWSER MULTI-SOURCE VALUE EVIDENCE: a real LISTING and a real WANTED '
                  'published with their own indicative price and explicit consent through the rendered '
                  'listing form; the source/lineage breakdown rendered with LISTING and WANTED kept '
                  'separate from AGREEMENT; an unrelated policy field edit through the direct publisher '
                  'path never reset listingSourceEnabled/wantedSourceEnabled; and a Community Seed was '
                  'proposed through a real ordinary-governance vote - no page errors.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
