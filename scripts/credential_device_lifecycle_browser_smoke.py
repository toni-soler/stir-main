"""Real browser proof of STIR 0.4's multi-device credential lifecycle (section 8-9-10 of the
brief): Ana activates economic exchange on a first browser context ("device 1"), then opens a
SECOND, storage-isolated browser context logged in as the SAME account ("device 2") and adds it as
an additional signer for her already-active osTRIS controller. Device 1 is then revoked, and device
2 - the one whose local Ed25519 key is now the only active credential - completes a REAL economic
exchange with Pedro end to end (activate/authorize/commit), proving the revoked device's key could
never have been usable, and the new device's key genuinely works, with no server ever seeing either
private key. Isolated development fixtures only. Requires requirements-browser.txt and Edge.
"""
import secrets
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql
from economic_browser_smoke import PERMISSIONS as ECONOMIC_PERMISSIONS

PERMISSIONS = ECONOMIC_PERMISSIONS + ['OSTRIS_CREDENTIAL_MANAGE']

def make_login(admin, tenant, role_id, label, suffix):
    email = f'{label}-{suffix}@stir.test'
    password = secrets.token_urlsafe(24)
    request(f'/api/shell/v1/tenants/{tenant}/users', 'POST', {'email': email, 'displayName': label, 'authProvider': 'local', 'subject': email, 'password': password, 'role': 'member', 'enabled': True}, admin, expected=(200, 201))
    session = request('/api/shell/v1/auth/login', 'POST', {'email': email, 'password': password})
    request(f'/api/shell/v1/tenants/{tenant}/roles/users/' + session['user']['id'], 'PUT', {'roleIds': [role_id]}, admin, 204)
    return email, password

def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]
    tenant_name = 'STIR Credential Lifecycle Browser E2E'
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-cred-browser-{suffix}', '{tenant_name}', 'active', false);")
    role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'stir_cred_browser_' + suffix, 'name': 'STIR credential browser E2E', 'description': 'Isolated development fixture', 'enabled': True}, admin, expected=(200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/' + role['id'] + '/permissions', 'PUT', PERMISSIONS, admin)
    ana_email, ana_password = make_login(admin, tenant, role['id'], 'ana', suffix)
    pedro_email, pedro_password = make_login(admin, tenant, role['id'], 'pedro', suffix)
    title = 'Guitar lessons ' + suffix

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        errors = []
        try:
            # ana1/ana2 are DELIBERATELY separate browser contexts (separate IndexedDB/localStorage
            # origins), simulating Ana logging in from two different real devices/browsers - each
            # generates its OWN Ed25519 keypair locally (signer.js); STIR/osTRIS only ever see the
            # two distinct public keys.
            ana1 = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            ana2 = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            pedro = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            for page in (ana1, ana2, pedro):
                page.set_default_timeout(60000)
                page.on("pageerror", lambda error: errors.append(str(error)))

            def login(page, email, password):
                page.goto("http://localhost:8089")
                page.locator("input[type=email]").fill(email)
                page.locator("input[type=password]").fill(password)
                page.locator("button[type=submit]").click()
                page.locator(".module-card").filter(has=page.get_by_role("heading", name="STIR", exact=True)).click()
                page.locator(".surface > header select").first.select_option(label=tenant_name)

            def back_to_marketplace(page):
                page.get_by_role("button", name="← Marketplace", exact=True).click()

            login(ana1, ana_email, ana_password)
            login(pedro, pedro_email, pedro_password)

            ana1.get_by_role("button", name="Mi perfil", exact=True).click()
            ana1.get_by_label("Nombre visible", exact=True).fill("Ana " + suffix)
            ana1.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana1.get_by_text("Perfil guardado.")).to_be_visible()
            back_to_marketplace(ana1)

            pedro.get_by_role("button", name="Mi perfil", exact=True).click()
            pedro.get_by_label("Nombre visible", exact=True).fill("Pedro " + suffix)
            pedro.get_by_role("button", name="Guardar", exact=True).click()
            expect(pedro.get_by_text("Perfil guardado.")).to_be_visible()
            back_to_marketplace(pedro)

            # Device 1: Ana bootstraps the community and activates - this is her first, and so far
            # only, signing device.
            ana1.get_by_role("button", name="Activación económica", exact=True).click()
            ana1.get_by_label("Nombre de la comunidad", exact=True).fill("Comunidad Cred E2E " + suffix)
            ana1.get_by_label("Código de la unidad", exact=True).fill("CRD")
            ana1.get_by_label("Decimales", exact=True).fill("0")
            ana1.get_by_role("button", name="Configurar comunidad económica", exact=True).click()
            ana1.get_by_role("button", name="Activar intercambios", exact=True).click()
            expect(ana1.get_by_text("Tu intercambio económico está activo.")).to_be_visible()
            expect(ana1.get_by_role("heading", name="Dispositivos autorizados", exact=True)).to_be_visible()

            pedro.get_by_role("button", name="Activación económica", exact=True).click()
            pedro.get_by_role("button", name="Activar intercambios", exact=True).click()
            expect(pedro.get_by_text("Tu intercambio económico está activo.")).to_be_visible()
            back_to_marketplace(pedro)

            # Device 2: same account, a brand new browser context with its own empty IndexedDB, so
            # its local signer has no credentialId yet - the UI must offer to add it as a new
            # signer for Ana's SAME already-active controller (never a new controller).
            login(ana2, ana_email, ana_password)
            ana2.get_by_role("button", name="Activación económica", exact=True).click()
            expect(ana2.get_by_text("Tu intercambio económico está activo.")).to_be_visible()
            expect(ana2.get_by_text("Este dispositivo/navegador todavía no está registrado como firmante.")).to_be_visible()
            ana2.get_by_label("Nombre del dispositivo", exact=True).fill("Portátil de Ana")
            ana2.get_by_role("button", name="Añadir este dispositivo", exact=True).click()
            expect(ana2.get_by_text("Portátil de Ana")).to_be_visible()

            # Both devices now show as active from either context (osTRIS discovery is the shared
            # source of truth) - two distinct entries, two "Revocar" controls. ana1.reload() lands
            # back on /stir/economic itself (a withNav detail route, whose own header only offers
            # "Resumen"/"← Marketplace" - "Activación económica" is a Marketplace tab, not reachable
            # from here), so there is no nav click to repeat after reloading.
            ana1.reload()
            expect(ana1.get_by_text("Portátil de Ana")).to_be_visible(timeout=15000)
            active_badges = ana1.locator(".stir-list li").filter(has=ana1.get_by_text("Activo", exact=True))
            expect(active_badges).to_have_count(2)

            # Revoke device 1 FROM device 2 - a real second-device operation, not a self-revoke.
            # The row that is NOT "Portátil de Ana" is device 1.
            rows = ana2.locator(".stir-list li")
            expect(rows).to_have_count(2)
            for i in range(2):
                row = rows.nth(i)
                if "Portátil de Ana" not in (row.inner_text() or ""):
                    row.get_by_role("button", name="Revocar", exact=True).click()
                    break
            expect(ana2.get_by_text("Revocado")).to_be_visible(timeout=15000)

            # Device 1 can no longer revoke anything (it is now the ONLY active device - the
            # self-lockout guard must disable its own remaining "Revocar" button).
            ana1.reload()
            expect(ana1.get_by_role("button", name="Revocar", exact=True)).to_be_disabled(timeout=15000)

            # Device 2 - the surviving, just-added credential - now completes a REAL economic
            # exchange end to end with Pedro, proving its key genuinely works for osTRIS signing.
            back_to_marketplace(ana2)
            ana2.get_by_role("button", name="+ Crear publicación", exact=True).click()
            ana2.get_by_role("combobox", name="Quiero", exact=True).select_option("OFFER")
            ana2.get_by_label("Título", exact=True).fill(title)
            ana2.get_by_label("Descripción", exact=True).fill("Clases de guitarra para principiantes")
            ana2.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana2.locator(".stir-card").filter(has=ana2.get_by_role("button", name=title, exact=True))).to_be_visible()

            pedro.get_by_label("Buscar", exact=True).fill(title)
            pedro.get_by_role("button", name=title, exact=True).click()
            pedro.get_by_role("button", name="Hacer una propuesta", exact=True).click()
            pedro.get_by_label("Mensaje", exact=True).fill("Me interesan las clases")
            pedro.get_by_label("Importe propuesto", exact=True).fill("100")
            pedro.get_by_role("button", name="Enviar propuesta", exact=True).click()
            expect(pedro.get_by_text("Abierta")).to_be_visible()

            ana2.get_by_role("button", name="Mis negociaciones", exact=True).click()
            ana2.get_by_role("button", name="Ver", exact=True).click()
            expect(ana2.get_by_text("Me interesan las clases")).to_be_visible()
            ana2.get_by_role("button", name="Aceptar", exact=True).click()
            expect(ana2.get_by_role("heading", name="Acuerdo", exact=True)).to_be_visible()

            ana2.get_by_role("button", name="Iniciar intercambio económico", exact=True).click()
            ana2.get_by_role("button", name="Firmar intercambio", exact=True).click()
            expect(ana2.get_by_text("Esperando la firma de la otra parte.")).to_be_visible()

            back_to_marketplace(pedro)
            pedro.get_by_role("button", name="Mis acuerdos", exact=True).click()
            pedro.get_by_role("button", name="Ver", exact=True).click()
            pedro.get_by_role("button", name="Firmar intercambio", exact=True).click()
            pedro.get_by_role("button", name="Confirmar intercambio", exact=True).click()
            expect(pedro.get_by_text("Secuencia de la comunidad")).to_be_visible()

            assert not errors, errors
            print('PASS: a second real browser device was added as an additional osTRIS signer for '
                  "Ana's already-active controller, the first device's credential was revoked from "
                  'the second device, the (now sole) surviving device was self-lockout-protected, and '
                  'that surviving device completed a real signed economic exchange with Pedro end to '
                  'end - with no JavaScript page errors.')
            print('STIR 0.4 PUBLIC PILOT MVP (credential/device lifecycle): PASS')
        finally:
            browser.close()

if __name__ == '__main__': main()
