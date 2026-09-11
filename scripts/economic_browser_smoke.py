"""Real two-party browser proof of the 0.3 economic exchange (section 30 of the brief): each of
Ana's and Pedro's real Ed25519 signing keys is generated INSIDE that browser session via WebCrypto
(src/signer.js), never on the Python side - the opposite of economic_exchange_e2e.py, which signs
from Python because it needs no browser. One session can never use the other's key: each key lives
in that page's own IndexedDB, scoped to that browser context's own origin storage. Requires
requirements-browser.txt. Isolated development fixtures only.
"""
import secrets
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql
from marketplace_e2e import PERMISSIONS as MARKETPLACE_PERMISSIONS

# OstrisClient relays the CALLER'S OWN bearer token to osTRIS (never a separate service
# credential), so osTRIS's @PreAuthorize checks run against this same tenant role. Ana/Pedro need
# the osTRIS-side permissions too, not just the stir.* ones.
PERMISSIONS = MARKETPLACE_PERMISSIONS + ['stir.economic.read', 'stir.economic.manage',
    'OSTRIS_DISCOVERY_READ', 'OSTRIS_COMMUNITY_MANAGE', 'OSTRIS_PARTICIPANT_ACTIVATE',
    'OSTRIS_TRANSACTION_CREATE', 'OSTRIS_TRANSACTION_READ', 'OSTRIS_TRANSACTION_AUTHORIZE', 'OSTRIS_TRANSACTION_COMMIT']

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
    tenant_name = 'STIR Economic Browser E2E'
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-eco-browser-{suffix}', '{tenant_name}', 'active', false);")
    role = request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':'stir_eco_browser_'+suffix,'name':'STIR economic browser E2E','description':'Isolated development fixture','enabled':True},admin,expected=(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+role['id']+'/permissions','PUT',PERMISSIONS,admin)
    ana_email, ana_password = make_login(admin, tenant, role['id'], 'ana', suffix)
    pedro_email, pedro_password = make_login(admin, tenant, role['id'], 'pedro', suffix)
    title = 'Bicycle repair ' + suffix

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        errors = []
        try:
            ana = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            pedro = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            for page in (ana, pedro):
                page.set_default_timeout(60000)
                page.on("pageerror", lambda error: errors.append(str(error)))

            def login(page, email, password):
                page.goto("http://localhost:8089")
                page.locator("input[type=email]").fill(email)
                page.locator("input[type=password]").fill(password)
                page.locator("button[type=submit]").click()
                page.locator(".module-card").filter(has=page.get_by_role("heading", name="STIR", exact=True)).click()
                page.locator(".surface > header select").first.select_option(label=tenant_name)

            # "← Marketplace" (withNav) is the only way back to the full tab bar from a detail
            # page (listing/negotiation/agreement/profile/economic); every hop between those
            # pages and a tab-bar destination goes through it, exactly like a real user clicking
            # the module's own back link - there is no direct route between two detail pages.
            def back_to_marketplace(page):
                page.get_by_role("button", name="← Marketplace", exact=True).click()

            login(ana, ana_email, ana_password)
            ana.get_by_role("button", name="Mi perfil", exact=True).click()
            ana.get_by_label("Nombre visible", exact=True).fill("Ana " + suffix)
            ana.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana.get_by_text("Perfil guardado.")).to_be_visible()
            back_to_marketplace(ana)

            login(pedro, pedro_email, pedro_password)
            pedro.get_by_role("button", name="Mi perfil", exact=True).click()
            pedro.get_by_label("Nombre visible", exact=True).fill("Pedro " + suffix)
            pedro.get_by_role("button", name="Guardar", exact=True).click()
            expect(pedro.get_by_text("Perfil guardado.")).to_be_visible()
            back_to_marketplace(pedro)

            # Ana bootstraps the marketplace's one economic community/unit, then activates herself.
            ana.get_by_role("button", name="Activación económica", exact=True).click()
            ana.get_by_label("Nombre de la comunidad", exact=True).fill("Comunidad E2E " + suffix)
            ana.get_by_label("Código de la unidad", exact=True).fill("CRD")
            ana.get_by_label("Decimales", exact=True).fill("0")
            ana.get_by_role("button", name="Configurar comunidad económica", exact=True).click()
            ana.get_by_role("button", name="Activar intercambios", exact=True).click()
            expect(ana.get_by_text("Tu intercambio económico está activo.")).to_be_visible()
            back_to_marketplace(ana)

            pedro.get_by_role("button", name="Activación económica", exact=True).click()
            pedro.get_by_role("button", name="Activar intercambios", exact=True).click()
            expect(pedro.get_by_text("Tu intercambio económico está activo.")).to_be_visible()
            back_to_marketplace(pedro)

            # Ana publishes a WANTED listing; Pedro proposes; Ana accepts directly (no counter),
            # so the flow exercises the WANTED payer/payee direction in the browser too.
            ana.get_by_role("button", name="+ Crear publicación", exact=True).click()
            # get_by_role("combobox", ...) rather than get_by_label: Chromium's accessible-name
            # computation for a <select> wrapped in a <label> is less reliable than for <input>.
            ana.get_by_role("combobox", name="Quiero", exact=True).select_option("WANTED")
            ana.get_by_label("Título", exact=True).fill(title)
            ana.get_by_label("Descripción", exact=True).fill("Necesito reparar mi bicicleta")
            ana.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True))).to_be_visible()

            pedro.get_by_label("Buscar", exact=True).fill(title)
            pedro.get_by_role("button", name=title, exact=True).click()
            pedro.get_by_role("button", name="Hacer una propuesta", exact=True).click()
            pedro.get_by_label("Mensaje", exact=True).fill("Puedo arreglarla por 500")
            pedro.get_by_label("Importe propuesto", exact=True).fill("500")
            pedro.get_by_role("button", name="Enviar propuesta", exact=True).click()
            expect(pedro.get_by_text("Abierta")).to_be_visible()

            # Ana is on /stir/mine (full tab bar) after saving her listing - no back-link hop needed.
            ana.get_by_role("button", name="Mis negociaciones", exact=True).click()
            ana.get_by_role("button", name="Ver", exact=True).click()
            expect(ana.get_by_text("Puedo arreglarla por 500")).to_be_visible()
            ana.get_by_role("button", name="Aceptar", exact=True).click()
            expect(ana.get_by_role("heading", name="Acuerdo", exact=True)).to_be_visible()

            # Ana starts the exchange, then both sign with keys generated in their own browser.
            ana.get_by_role("button", name="Iniciar intercambio económico", exact=True).click()
            ana.get_by_role("button", name="Firmar intercambio", exact=True).click()
            expect(ana.get_by_text("Esperando la firma de la otra parte.")).to_be_visible()

            back_to_marketplace(pedro)
            pedro.get_by_role("button", name="Mis acuerdos", exact=True).click()
            pedro.get_by_role("button", name="Ver", exact=True).click()
            pedro.get_by_role("button", name="Firmar intercambio", exact=True).click()
            pedro.get_by_role("button", name="Confirmar intercambio", exact=True).click()
            expect(pedro.get_by_text("Secuencia de la comunidad")).to_be_visible()
            pedro.screenshot(path=str(ROOT / ".local/browser-economic-committed.png"), full_page=True)

            pedro.get_by_role("button", name="Ver mi saldo", exact=True).click()
            expect(pedro.get_by_text("Saldo:")).to_be_visible()

            ana.reload()
            expect(ana.get_by_text("Secuencia de la comunidad")).to_be_visible()

            assert not errors, errors
            print("PASS: two real browser sessions (Ana, Pedro), each with its own WebCrypto Ed25519 "
                  "key generated and kept in that session's own IndexedDB, activated economic exchange, "
                  "reached a WANTED Agreement, both signed the osTRIS EXCHANGE proposal, and committed it - "
                  "reaching a visible COMMITTED state with no JavaScript page errors.")
        finally:
            browser.close()

if __name__ == '__main__': main()
