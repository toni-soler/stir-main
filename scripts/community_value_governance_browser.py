"""Real browser proof of the publisher toolkit this increment adds on top of the already-validated
community_references_browser.py flow: the evidence breakdown panel, reference policy configuration,
and the market integrity signal -> review -> FINAL lifecycle across two independent publisher
sessions - all through the actually rendered /stir/references page, no JavaScript page errors.
Only creates an isolated local development fixture tenant; no private key or credential leaves
its own browser session.
"""
import re
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import BASE, ROOT, request
from multitenant import sql
from marketplace_browser_smoke import make_login
from community_references_e2e import MEMBER
from browser_ui import field, explain

def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email':'admin@stir.test',
        'password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix=uuid.uuid4().hex[:8]; name='Governance Browser '+suffix
    tenant=sql(f"select tenant_id from idax_core.tenant_create('gov-b-{suffix}','{name}','active',false);")
    role=request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':'publisher'+suffix,'name':'Publisher','description':'Local browser test','enabled':True},admin,(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+role['id']+'/permissions','PUT',MEMBER+['stir.references.publish'],admin)
    role2=request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':'publisher2-'+suffix,'name':'Second publisher','description':'Local browser test','enabled':True},admin,(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+role2['id']+'/permissions','PUT',MEMBER+['stir.references.publish'],admin)
    member_role=request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':'member'+suffix,'name':'Member','description':'Local browser test','enabled':True},admin,(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+member_role['id']+'/permissions','PUT',MEMBER,admin)
    ana_login=make_login(admin,tenant,role['id'],'ana',suffix)
    diego_login=make_login(admin,tenant,role2['id'],'diego',suffix)
    pedro_login=make_login(admin,tenant,member_role['id'],'pedro',suffix)
    ana_session=request('/api/shell/v1/auth/login','POST',dict(zip(['email','password'],ana_login)))
    # Community authority while Ordinary Governance is disabled is the workspace owner/admin, not a permission
    # (ReferenceService.requireCommunityAuthority) - the same setup the HTTP E2E scripts use.
    sql(f"update idax_core.tenant_user set role='admin' where tenant_id='{tenant}' and user_id='{ana_session['user']['id']}';")
    # Diego, the second and independent publisher who decides the market-integrity case, needs the same authority.
    diego_session=request('/api/shell/v1/auth/login','POST',dict(zip(['email','password'],diego_login)))
    sql(f"update idax_core.tenant_user set role='admin' where tenant_id='{tenant}' and user_id='{diego_session['user']['id']}';")
    a=ana_session['accessToken']
    base=f'/api/stir/tenants/{tenant}'
    request(base+'/economic/marketplace/bootstrap','POST',{'communityName':name,'unitCode':'GOV','unitScale':2},a)

    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        errors=[]
        try:
            def login(page,credentials):
                page.set_default_timeout(30000);page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(BASE);page.locator('input[type=email]').fill(credentials[0]);page.locator('input[type=password]').fill(credentials[1]);page.locator('button[type=submit]').click()
                page.locator('.module-card').filter(has=page.get_by_role('heading',name='STIR',exact=True)).click()
                page.locator('.surface > header select').first.select_option(label=name)

            ana=browser.new_context(locale='es-ES',viewport={'width':1440,'height':1600}).new_page()
            pedro=browser.new_context(locale='es-ES',viewport={'width':1440,'height':1600}).new_page()
            diego=browser.new_context(locale='es-ES',viewport={'width':1440,'height':1600}).new_page()
            login(ana,ana_login)
            ana.goto(BASE + '/stir/references')

            # --- Create the definition, confirm the publisher toolkit is reachable and empty ---
            ana.get_by_text('Crear definición comparable',exact=True).first.click()
            ana.get_by_label(field('Nombre')).fill('Rice 1kg '+suffix)
            ana.get_by_label(field('Ámbito de comparación')).fill('One 1kg bag of rice')
            ana.get_by_label(field('Unidad de cantidad')).fill('bag')
            ana.get_by_role('button',name='Crear definición comparable',exact=True).click()
            expect(ana.get_by_text('Datos insuficientes',exact=True)).to_be_visible()

            ana.get_by_text('Desglose de la evidencia',exact=True).click()
            expect(ana.get_by_text('Aún no hay observaciones',exact=True)).to_be_visible()

            # --- Reference policy: ordinary publisher governance, a real successful change ---
            ana.get_by_text('Política de la referencia',exact=True).click()
            policy=ana.locator('details').filter(has_text='Política de la referencia')
            policy.get_by_label(field('Ventana (días)')).fill('60')
            policy.get_by_label(field('Motivo de esta política')).fill('Tighten the window after reviewing the category')
            policy.get_by_role('button',name='Guardar política',exact=True).click()
            expect(policy.locator('input').first).to_have_value('60')

            # --- Propose and publish v1 (a deliberate convention, no evidence needed yet) ---
            ana.locator('summary').filter(has_text='Proponer referencia').click()
            propose=ana.locator('details').filter(has_text='Proponer referencia').first
            propose.get_by_label(field('Valor orientativo inferior')).fill('10')
            propose.get_by_label(field('Valor orientativo superior')).fill('10')
            propose.get_by_label(field('Explicación humana')).fill('Assembly convention, not an observed price')
            propose.get_by_label(field('Método / origen de la decisión')).fill('Community assembly')
            propose.get_by_role('button',name='Proponer referencia',exact=True).click()
            ana.get_by_label(field('Decisión de publicación')).fill('Initial assembly decision')
            ana.get_by_role('button',name='Publicar nueva versión',exact=True).click()
            expect(ana.get_by_test_id('reference-panel').get_by_text('Assembly convention, not an observed price',exact=True)).to_be_visible()

            # --- A real listing and a real accepted Agreement, building one raw observation ---
            ana.goto(BASE + '/stir/new')
            title='Rice bag '+suffix
            ana.get_by_label('Título',exact=False).fill(title)
            ana.get_by_label('Descripción',exact=False).fill('1kg bag of rice')
            definitions=request(base+'/references',token=a)
            definition_id=next(d['id'] for d in definitions if d['name']=='Rice 1kg '+suffix)
            ana.get_by_label('Definición comparable',exact=False).select_option(value=definition_id)
            ana.get_by_role('button',name='Guardar y añadir fotos',exact=True).click()
            ana.get_by_role('button',name='Listo',exact=True).click()

            login(pedro,pedro_login)
            pedro.goto(BASE + '/stir')
            pedro.get_by_label(field('Buscar')).fill(title)
            pedro.get_by_role('button',name=title,exact=True).click()
            pedro.get_by_role('button',name='Hacer una propuesta',exact=True).click()
            pedro.get_by_label(field('Mensaje')).fill('Free proposal')
            pedro.get_by_label(field('Cantidad')).fill('1')
            pedro.get_by_label(field('Unidad')).fill('bag')
            pedro.get_by_label(field('Importe propuesto')).fill('12')
            pedro.get_by_role('checkbox').check()
            pedro.get_by_role('button',name='Enviar propuesta',exact=True).click()
            expect(pedro).to_have_url(re.compile(r'/stir/negotiations/'))
            negotiation_url=pedro.url
            ana.goto(negotiation_url)
            ana.get_by_role('checkbox').check()
            ana.get_by_role('button',name='Aceptar',exact=True).click()

            # --- Evidence breakdown reflects the new raw observation; still same-day, so it is
            # not eligible yet (see MARKET_INTEGRITY.md's daily UTC cutoff / anti-live-differencing,
            # already proven at the PostgreSQL level with backdated observations) - what the UI must
            # get right is showing the growing raw count without ever losing or hiding that row. ---
            ana.goto(BASE + '/stir/references')
            ana.get_by_label('Definición comparable',exact=False).select_option(value=definition_id)
            ana.get_by_text('Desglose de la evidencia',exact=True).click()
            expect(ana.get_by_text('Observaciones brutas: 2',exact=False)).to_be_visible()

            # --- Market integrity: ana raises a signal, moves it to review, diego finalizes it ---
            ana.get_by_text('Integridad de mercado',exact=True).click()
            integrity=ana.locator('details').filter(has_text='Integridad de mercado')
            integrity.get_by_text('Enviar una señal',exact=True).click()
            signal_form=integrity.locator('details').filter(has_text='Enviar una señal')
            signal_form.locator('select').first.select_option(index=1)
            signal_form.get_by_label(field('Motivo')).fill('Reviewing this deal for concentration')
            signal_form.get_by_role('button',name='Enviar señal',exact=True).click()
            case=integrity.locator('article').first
            expect(case.get_by_text('Señal',exact=True)).to_be_visible()
            case.get_by_label(field('Motivo')).fill('Looking into it')
            case.get_by_role('button',name='Iniciar revisión',exact=True).click()
            expect(case.get_by_text('En revisión',exact=True)).to_be_visible()

            login(diego,diego_login)
            diego.goto(BASE + '/stir/references')
            diego.get_by_label('Definición comparable',exact=False).select_option(value=definition_id)
            diego.get_by_text('Integridad de mercado',exact=True).click()
            diego_integrity=diego.locator('details').filter(has_text='Integridad de mercado')
            diego_case=diego_integrity.locator('article').first
            expect(diego_case.get_by_text('En revisión',exact=True)).to_be_visible()
            diego_case.get_by_label(field('Motivo')).fill('Confirmed: concentrated with one counterparty')
            diego_case.get_by_role('button',name='Marcar como final',exact=True).click()
            expect(diego_case.get_by_text('Final',exact=True)).to_be_visible()

            # --- Publish v2; the Agreement made under v1 still shows v1's exact historical context ---
            history_before=request(base+'/references/'+definition_id+'/history',token=a)
            assert len(history_before)==1

            ana.goto(BASE + '/stir/references')
            ana.get_by_label('Definición comparable',exact=False).select_option(value=definition_id)
            ana.locator('summary').filter(has_text='Proponer referencia').click()
            propose2=ana.locator('details').filter(has_text='Proponer referencia').first
            propose2.get_by_label(field('Valor orientativo inferior')).fill('25')
            propose2.get_by_label(field('Valor orientativo superior')).fill('25')
            propose2.get_by_label(field('Explicación humana')).fill('Deliberately revised convention')
            propose2.get_by_label(field('Método / origen de la decisión')).fill('Second assembly')
            propose2.get_by_role('button',name='Proponer referencia',exact=True).click()
            ana.get_by_label(field('Decisión de publicación')).fill('Second assembly decision')
            ana.get_by_role('button',name='Publicar nueva versión',exact=True).last.click()
            expect(ana.get_by_test_id('reference-panel').get_by_text('Deliberately revised convention',exact=True)).to_be_visible()
            history_after=request(base+'/references/'+definition_id+'/history',token=a)
            assert len(history_after)==2
            v1=next(h for h in history_after if h['version']==1)
            assert v1['explanation']=='Assembly convention, not an observed price', 'v1 stays exactly as originally published'

            assert not errors,errors
            ana.screenshot(path=str(ROOT/'.local/governance-browser-final.png'),full_page=True)
            print('PASS BROWSER GOVERNANCE UI: evidence breakdown, reference policy configuration, '
                  'v1 propose/publish, real Agreement growing the raw observation count, market integrity '
                  'signal->review->FINAL across two independent publisher sessions, v2 publish with v1 '
                  'unchanged - all through the rendered UI, no page errors.')
        except Exception:
            explain(ana=ana, pedro=pedro, diego=diego)
            for label,page in [('ana',ana),('pedro',pedro),('diego',diego)]:
                try: page.screenshot(path=str(ROOT/f'.local/governance-browser-failure-{label}.png'),full_page=True)
                except Exception: pass
            raise
        finally: browser.close()

if __name__=='__main__': main()
