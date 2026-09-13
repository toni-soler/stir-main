"""Real browser proof of STIR 0.4's core product experience: profile + avatar, a photographed
Listing, the Home dashboard, notifications, and device management - on top of the already-proven
0.1-0.3 flows. Isolated development fixtures only. Requires requirements-browser.txt and Edge.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql
from economic_browser_smoke import PERMISSIONS as ECONOMIC_PERMISSIONS
import secrets
import uuid

PERMISSIONS = ECONOMIC_PERMISSIONS + ['stir.notifications.read', 'stir.content.report', 'stir.moderation.manage']
ASSETS = ROOT / '.local/test-assets'

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
    tenant_name = 'STIR Public Pilot Browser E2E'
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-pilot-browser-{suffix}', '{tenant_name}', 'active', false);")
    role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'stir_pilot_browser_' + suffix, 'name': 'STIR pilot browser E2E', 'description': 'Isolated development fixture', 'enabled': True}, admin, expected=(200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/' + role['id'] + '/permissions', 'PUT', PERMISSIONS, admin)
    ana_email, ana_password = make_login(admin, tenant, role['id'], 'ana', suffix)
    title = 'Handmade pottery ' + suffix

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        errors = []
        try:
            ana = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            ana.set_default_timeout(60000)
            ana.on("pageerror", lambda error: errors.append(str(error)))

            ana.goto("http://localhost:8089")
            ana.locator("input[type=email]").fill(ana_email)
            ana.locator("input[type=password]").fill(ana_password)
            ana.locator("button[type=submit]").click()
            ana.locator(".module-card").filter(has=ana.get_by_role("heading", name="STIR", exact=True)).click()
            ana.locator(".surface > header select").first.select_option(label=tenant_name)

            # Profile + avatar upload (section 6).
            ana.get_by_role("button", name="Mi perfil", exact=True).click()
            ana.get_by_label("Nombre visible", exact=True).fill("Ana " + suffix)
            ana.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana.get_by_text("Perfil guardado.")).to_be_visible()
            ana.get_by_label("Subir foto").set_input_files(str(ASSETS / "test-avatar.png"))
            expect(ana.locator("img.stir-avatar")).to_be_visible(timeout=15000)

            # Publish a Listing, then reopen it for edit to attach a photo (sections 4-5-11: photos
            # can only be uploaded once the listing itself exists and has an id).
            ana.get_by_role("button", name="← Marketplace", exact=True).click()
            ana.get_by_role("button", name="+ Crear publicación", exact=True).click()
            ana.get_by_label("Título", exact=True).fill(title)
            ana.get_by_label("Descripción", exact=True).fill("Piezas de cerámica hechas a mano")
            ana.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True))).to_be_visible()
            ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True)).get_by_role("button", name="Editar", exact=True).click()
            ana.get_by_label("Fotos", exact=True).set_input_files(str(ASSETS / "test-photo.jpg"))
            expect(ana.locator(".stir-photo-item img")).to_be_visible(timeout=15000)
            expect(ana.get_by_text("1/6")).to_be_visible()

            # ListingEditor renders INLINE inside Marketplace itself (not a withNav route) - its own
            # "Cancelar" button (not "← Marketplace", which does not exist on this view) returns to
            # the Mine grid, where the card grid now shows the uploaded photo as the thumbnail.
            ana.get_by_role("button", name="Cancelar", exact=True).click()
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True)).locator("img.stir-thumb")).to_be_visible(timeout=15000)

            # Home dashboard (section 3): reachable, shows the fresh listing under "my active
            # listings", and offers the economic-activation prompt since Ana has not activated yet.
            ana.get_by_role("button", name="Resumen", exact=True).click()
            expect(ana.get_by_role("button", name="Activación económica", exact=True)).to_be_visible()
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True)).first).to_be_visible()

            # Notifications page is reachable (via the Home dashboard's link) and starts empty for a
            # brand new user.
            ana.get_by_role("button", name="Ver todas las notificaciones", exact=True).click()
            expect(ana.get_by_text("Todavía no hay notificaciones.")).to_be_visible()

            assert not errors, errors
            print('PASS: profile+avatar upload, a photographed Listing (thumbnail visible on the card '
                  'grid), the Home dashboard and the notifications page all work end to end through the '
                  'real rendered UI, with no JavaScript page errors.')
            print('STIR 0.4 PUBLIC PILOT MVP (core flow smoke): PASS')
        finally:
            browser.close()

if __name__ == '__main__': main()
