"""Real two-party browser proof: Ana publishes, Pedro proposes, Ana counters, Pedro accepts - all
through the actual rendered Shell + STIR UI, in two independent browser contexts (two real
sessions, like two people). Requires requirements-browser.txt. Isolated development fixtures only.
"""
import secrets
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql
from marketplace_e2e import PERMISSIONS

def make_login(admin, tenant, role_id, label, suffix):
    email = f'{label}-{suffix}@stir.test'
    password = secrets.token_urlsafe(24)
    request(f'/api/shell/v1/tenants/{tenant}/users','POST',{'email':email,'displayName':label,'authProvider':'local','subject':email,'password':password,'role':'member','enabled':True},admin,expected=(200,201))
    session = request('/api/shell/v1/auth/login','POST',{'email':email,'password':password})
    request(f'/api/shell/v1/tenants/{tenant}/roles/users/'+session['user']['id'],'PUT',{'roleIds':[role_id]},admin,204)
    return email, password

def main():
    admin = request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test','password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-browser-{suffix}', 'STIR Browser E2E', 'active', false);")
    role = request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':'stir_browser_'+suffix,'name':'STIR browser E2E','description':'Isolated development fixture','enabled':True},admin,expected=(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+role['id']+'/permissions','PUT',PERMISSIONS,admin)
    ana_email, ana_password = make_login(admin,tenant,role['id'],'ana',suffix)
    pedro_email, pedro_password = make_login(admin,tenant,role['id'],'pedro',suffix)
    title = 'Tomates '+suffix

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        errors = []
        try:
            ana = browser.new_context(locale="es-ES", viewport={"width":1440,"height":1000}).new_page()
            pedro = browser.new_context(locale="es-ES", viewport={"width":1440,"height":1000}).new_page()
            for page in (ana, pedro):
                page.set_default_timeout(60000)
                page.on("pageerror", lambda error: errors.append(str(error)))

            def login(page, email, password):
                page.goto("http://localhost:8089")
                page.locator("input[type=email]").fill(email)
                page.locator("input[type=password]").fill(password)
                page.locator("button[type=submit]").click()
                page.locator(".module-card").filter(has=page.get_by_role("heading", name="STIR", exact=True)).click()
                page.locator(".surface > header select").first.select_option(label="STIR Browser E2E")

            login(ana, ana_email, ana_password)
            ana.get_by_role("button", name="Mi perfil", exact=True).click()
            ana.get_by_label("Nombre visible", exact=True).fill("Ana "+suffix)
            ana.get_by_label("Biografía breve", exact=True).fill("Cultivo tomates")
            ana.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana.get_by_text("Perfil guardado.")).to_be_visible()

            ana.get_by_role("button", name="← Marketplace", exact=True).click()
            ana.get_by_role("button", name="+ Crear publicación", exact=True).click()
            ana.get_by_label("Título", exact=True).fill(title)
            ana.get_by_label("Descripción", exact=True).fill("Tomates San Marzano maduros")
            ana.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True))).to_be_visible()

            login(pedro, pedro_email, pedro_password)
            pedro.get_by_label("Buscar", exact=True).fill(title)
            pedro.get_by_role("button", name=title, exact=True).click()
            pedro.get_by_role("button", name="Hacer una propuesta", exact=True).click()
            pedro.get_by_label("Mensaje", exact=True).fill("Me interesan 15kg")
            pedro.get_by_role("button", name="Enviar propuesta", exact=True).click()
            expect(pedro.get_by_text("Abierta")).to_be_visible()

            ana.get_by_role("button", name="Mis negociaciones", exact=True).click()
            ana.get_by_role("button", name="Ver", exact=True).click()
            expect(ana.get_by_text("Me interesan 15kg")).to_be_visible()
            ana.get_by_role("button", name="Contraoferta", exact=True).click()
            ana.get_by_label("Mensaje", exact=True).fill("Puedo hacer 20kg al mismo precio")
            ana.get_by_role("button", name="Enviar contraoferta", exact=True).click()
            expect(ana.get_by_text("Puedo hacer 20kg al mismo precio")).to_be_visible()

            pedro.reload()
            expect(pedro.get_by_text("Puedo hacer 20kg al mismo precio")).to_be_visible()
            pedro.get_by_role("button", name="Aceptar", exact=True).click()
            expect(pedro.get_by_role("heading", name="Acuerdo", exact=True)).to_be_visible()
            expect(pedro.get_by_text("Pendiente de ejecución económica")).to_be_visible()
            pedro.screenshot(path=str(ROOT/".local/browser-agreement.png"), full_page=True)

            assert not errors, errors
            print("PASS: two real browser sessions (Ana, Pedro) completed profile setup, listing publish, offer, counteroffer and accept through the actual rendered UI, reaching a visible Agreement with no JavaScript page errors.")
        finally:
            browser.close()

if __name__ == '__main__': main()
