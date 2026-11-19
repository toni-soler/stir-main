"""Two real browser sessions exercise reference publication, negotiation and historical context.
Only creates isolated local development fixtures; credentials remain in process memory.
"""
import uuid
import re
from playwright.sync_api import sync_playwright, expect
from smoke import BASE, ROOT, request
from multitenant import sql
from marketplace_browser_smoke import make_login
from community_references_e2e import MEMBER
from browser_ui import field

def main():
    admin=request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test',
        'password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix=uuid.uuid4().hex[:8]; name='Reference Browser '+suffix
    tenant=sql(f"select tenant_id from idax_core.tenant_create('ref-browser-{suffix}','{name}','active',false);")
    role=request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':'reference'+suffix,'name':'Reference publisher','description':'Local browser test','enabled':True},admin,(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+role['id']+'/permissions','PUT',MEMBER+['stir.references.publish'],admin)
    ana_login=make_login(admin,tenant,role['id'],'ana',suffix)
    pedro_login=make_login(admin,tenant,role['id'],'pedro',suffix)
    ana_session=request('/api/shell/v1/auth/login','POST',dict(zip(['email','password'],ana_login)))
    # Community authority while Ordinary Governance is disabled is the workspace owner/admin, not a permission
    # (ReferenceService.requireCommunityAuthority) - the same setup community_references_e2e.py uses.
    sql(f"update idax_core.tenant_user set role='admin' where tenant_id='{tenant}' and user_id='{ana_session['user']['id']}';")
    a=ana_session['accessToken']
    base=f'/api/stir/tenants/{tenant}'
    binding=request(base+'/economic/marketplace/bootstrap','POST',{'communityName':name,'unitCode':'REF','unitScale':2},a)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        errors=[]
        try:
            ana=browser.new_context(locale='es-ES',viewport={'width':1440,'height':1100}).new_page()
            pedro=browser.new_context(locale='es-ES',viewport={'width':1440,'height':1100}).new_page()
            def login(page,credentials):
                page.set_default_timeout(30000);page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(BASE);page.locator('input[type=email]').fill(credentials[0]);page.locator('input[type=password]').fill(credentials[1]);page.locator('button[type=submit]').click()
                page.locator('.module-card').filter(has=page.get_by_role('heading',name='STIR',exact=True)).click()
                page.locator('.surface > header select').first.select_option(label=name)
            login(ana,ana_login)
            ana.goto(BASE + '/stir/references')
            ana.get_by_text('Crear definición comparable',exact=True).first.click()
            ana.get_by_label(field('Nombre')).fill('Pan 500g '+suffix)
            ana.get_by_label(field('Ámbito de comparación')).fill('Una barra de pan de 500g')
            ana.get_by_label(field('Unidad de cantidad')).fill('barra')
            ana.get_by_role('button',name='Crear definición comparable',exact=True).click()
            expect(ana.get_by_text('Datos insuficientes',exact=True)).to_be_visible()
            def publish(page,amount,explanation,decision):
                summary=page.locator('summary').filter(has_text='Proponer referencia')
                if not page.get_by_label(field('Valor orientativo inferior')).is_visible():summary.click()
                page.get_by_label(field('Valor orientativo inferior')).fill(amount)
                page.get_by_label(field('Valor orientativo superior')).fill(amount)
                page.get_by_label(field('Explicación humana')).fill(explanation)
                page.get_by_label(field('Método / origen de la decisión')).fill('Asamblea comunitaria')
                page.get_by_role('button',name='Proponer referencia',exact=True).click()
                page.get_by_label(field('Decisión de publicación')).fill(decision)
                page.get_by_role('button',name='Publicar nueva versión',exact=True).click()
                expect(page.get_by_test_id('reference-panel').get_by_text(explanation,exact=True)).to_be_visible()
            publish(ana,'10','Convención inicial revisable','Primera asamblea')
            definition=request(base+'/references',token=a)[0]
            ana.goto(BASE + '/stir/new')
            title='Pan comunitario '+suffix
            ana.get_by_label('Título',exact=False).fill(title)
            ana.get_by_label('Descripción',exact=False).fill('Barra de pan de 500g')
            ana.get_by_label('Definición comparable',exact=False).select_option(value=definition['id'])
            ana.get_by_role('button',name='Guardar y añadir fotos',exact=True).click()
            ana.get_by_role('button',name='Listo',exact=True).click()
            login(pedro,pedro_login)
            pedro.goto(BASE + '/stir')
            pedro.get_by_label(field('Buscar')).fill(title)
            pedro.get_by_role('button',name=title,exact=True).click()
            expect(pedro.get_by_text('Convención inicial revisable',exact=True)).to_be_visible()
            pedro.get_by_text('¿Por qué me muestran esta referencia?',exact=True).click()
            expect(pedro.get_by_text('Datos insuficientes',exact=True)).to_be_visible()
            pedro.get_by_role('button',name='Hacer una propuesta',exact=True).click()
            pedro.get_by_label(field('Mensaje')).fill('Propongo por debajo de la referencia')
            pedro.get_by_label(field('Cantidad')).fill('1')
            pedro.get_by_label(field('Unidad')).fill('barra')
            pedro.get_by_label(field('Importe propuesto')).fill('1')
            expect(pedro.get_by_label(field('Referencia de unidad'))).to_have_value(binding['unitId'])
            pedro.get_by_role('checkbox').check()
            expect(pedro.get_by_text('Por debajo de la referencia (permitido)',exact=False)).to_be_visible()
            pedro.get_by_role('button',name='Enviar propuesta',exact=True).click()
            expect(pedro).to_have_url(re.compile(r'/stir/negotiations/'))
            negotiation_url=pedro.url
            ana.goto(negotiation_url)
            ana.get_by_role('button',name='Contraoferta',exact=True).click()
            ana.get_by_label(field('Mensaje')).fill('Propongo por encima de la referencia')
            ana.get_by_label(field('Cantidad')).fill('1');ana.get_by_label(field('Unidad')).fill('barra')
            ana.get_by_label(field('Importe propuesto')).fill('100');expect(ana.get_by_label(field('Referencia de unidad'))).to_have_value(binding['unitId'])
            ana.get_by_role('checkbox').last.check()
            expect(ana.get_by_text('Por encima de la referencia (permitido)',exact=False)).to_be_visible()
            ana.get_by_role('button',name='Enviar contraoferta',exact=True).click()
            pedro.reload();expect(pedro.get_by_text('Propongo por encima de la referencia',exact=True)).to_be_visible()
            pedro.get_by_role('checkbox').check();pedro.get_by_role('button',name='Aceptar',exact=True).click()
            expect(pedro).to_have_url(re.compile(r'/stir/agreements/'))
            expect(pedro.get_by_role('heading',name='Acuerdo',exact=True)).to_be_visible()
            expect(pedro.get_by_text('Lo que acordaste',exact=True)).to_be_visible()
            expect(pedro.get_by_text('Unidad comunitaria',exact=False).first).to_be_visible()
            agreement_url=pedro.url
            expect(pedro.get_by_text('Convención inicial revisable',exact=True)).to_be_visible()
            pedro.screenshot(path=str(ROOT/'.local/reference-agreement-v1.png'),full_page=True)
            ana.goto(BASE + '/stir/references')
            ana.get_by_label('Definición comparable',exact=False).select_option(value=definition['id'])
            publish(ana,'25','Convención revisada deliberadamente','Segunda asamblea')
            ana.screenshot(path=str(ROOT/'.local/reference-published-v2.png'),full_page=True)
            pedro.goto(agreement_url)
            expect(pedro.get_by_text('Convención inicial revisable',exact=True)).to_be_visible()
            expect(pedro.get_by_text('Convención revisada deliberadamente',exact=True)).to_have_count(0)
            assert not errors,errors
            print('PASS BROWSER: two sessions, definition, published v1, listing association, explanation, free counteroffer above guidance, consent, Agreement, v2 and unchanged historical v1; no page errors.')
        except Exception:
            for label,page in [('ana',ana),('pedro',pedro)]:
                page.screenshot(path=str(ROOT/f'.local/reference-failure-{label}.png'),full_page=True)
            raise
        finally: browser.close()

if __name__=='__main__':main()
