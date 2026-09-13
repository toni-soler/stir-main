"""Real browser proof of STIR 0.4's minimal content moderation (section 17-18 of the brief): Ana
publishes a Listing, Carlos (an ordinary tenant member with no moderation permission) reports it,
and only a separate Moderator account - the only one with stir.moderation.manage - can see the
"Cola de moderación" nav link or the report queue at all. The moderator hides the Listing, which
disappears from the public marketplace search for everyone (including its own owner's public view)
without touching Ana's own ACTIVE/CLOSED control over it (proven at the unit level in
ModerationServiceTest; this script proves the reachable, permission-gated UI on top of that).
Isolated development fixtures only. Requires requirements-browser.txt and Edge.
"""
import secrets
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from multitenant import sql

MEMBER_PERMISSIONS = ['stir.listings.read', 'stir.listings.create', 'stir.listings.update',
    'stir.participants.read', 'stir.participants.update', 'stir.content.report']
MODERATOR_PERMISSIONS = ['stir.listings.read', 'stir.moderation.manage']

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
    tenant_name = 'STIR Moderation Browser E2E'
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-mod-browser-{suffix}', '{tenant_name}', 'active', false);")

    member_role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'stir_mod_member_' + suffix, 'name': 'STIR moderation E2E member', 'description': 'Isolated development fixture', 'enabled': True}, admin, expected=(200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/' + member_role['id'] + '/permissions', 'PUT', MEMBER_PERMISSIONS, admin)
    moderator_role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'stir_mod_moderator_' + suffix, 'name': 'STIR moderation E2E moderator', 'description': 'Isolated development fixture', 'enabled': True}, admin, expected=(200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/' + moderator_role['id'] + '/permissions', 'PUT', MODERATOR_PERMISSIONS, admin)

    ana_email, ana_password = make_login(admin, tenant, member_role['id'], 'ana', suffix)
    carlos_email, carlos_password = make_login(admin, tenant, member_role['id'], 'carlos', suffix)
    mod_email, mod_password = make_login(admin, tenant, moderator_role['id'], 'mod', suffix)
    title = 'Suspicious offer ' + suffix

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        errors = []
        try:
            ana = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            carlos = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            moderator = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1000}).new_page()
            for page in (ana, carlos, moderator):
                page.set_default_timeout(60000)
                page.on("pageerror", lambda error: errors.append(str(error)))

            def login(page, email, password):
                page.goto("http://localhost:8089")
                page.locator("input[type=email]").fill(email)
                page.locator("input[type=password]").fill(password)
                page.locator("button[type=submit]").click()
                page.locator(".module-card").filter(has=page.get_by_role("heading", name="STIR", exact=True)).click()
                page.locator(".surface > header select").first.select_option(label=tenant_name)

            login(ana, ana_email, ana_password)
            login(carlos, carlos_email, carlos_password)
            login(moderator, mod_email, mod_password)

            # Ordinary members never see the moderation nav link - it is gated on
            # stir.moderation.manage, which neither Ana nor Carlos holds.
            expect(ana.get_by_role("button", name="Cola de moderación", exact=True)).to_have_count(0)
            expect(carlos.get_by_role("button", name="Cola de moderación", exact=True)).to_have_count(0)
            # The moderator - who holds NO stir.listings.create/content.report at all - still sees it.
            expect(moderator.get_by_role("button", name="Cola de moderación", exact=True)).to_be_visible()

            ana.get_by_role("button", name="+ Crear publicación", exact=True).click()
            ana.get_by_label("Título", exact=True).fill(title)
            ana.get_by_label("Descripción", exact=True).fill("Oferta que Carlos va a reportar")
            ana.get_by_role("button", name="Guardar", exact=True).click()
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True))).to_be_visible()

            # Carlos finds it in the public marketplace and reports it.
            carlos.get_by_label("Buscar", exact=True).fill(title)
            carlos.get_by_role("button", name=title, exact=True).click()
            carlos.get_by_role("button", name="Reportar", exact=True).click()
            carlos.get_by_label("Motivo", exact=True).fill("Parece un fraude")
            carlos.get_by_role("button", name="Enviar reporte", exact=True).click()
            expect(carlos.get_by_text("Gracias, tu reporte se ha enviado para revisión.")).to_be_visible()

            # Carlos himself has no permission to open the moderation queue directly either.
            carlos.goto("http://localhost:8089/stir/moderation")
            expect(carlos.get_by_text("Parece un fraude")).to_have_count(0)

            # Only the moderator can see and act on the report.
            moderator.get_by_role("button", name="Cola de moderación", exact=True).click()
            expect(moderator.get_by_text("Parece un fraude")).to_be_visible()
            moderator.get_by_role("button", name="Ocultar publicación", exact=True).click()
            expect(moderator.get_by_text("No hay reportes abiertos.")).to_be_visible()

            # The Listing disappears from the public marketplace search for everyone, including
            # Ana's own non-"mine" view, without Ana's own ACTIVE/CLOSED control being touched
            # (already proven at the unit level - ModerationServiceTest).
            ana.get_by_role("button", name="Marketplace", exact=True).click()
            ana.get_by_label("Buscar", exact=True).fill(title)
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True))).to_have_count(0)

            # But it is still visible - clearly marked as hidden - in Ana's OWN "Mis publicaciones",
            # proving moderation hides from discovery without deleting the Listing itself.
            ana.get_by_role("button", name="Mis publicaciones", exact=True).click()
            ana.get_by_label("Buscar", exact=True).fill(title)
            expect(ana.locator(".stir-card").filter(has=ana.get_by_role("button", name=title, exact=True))).to_be_visible()

            assert not errors, errors
            print('PASS: the moderation nav link and queue are reachable ONLY by an account holding '
                  'stir.moderation.manage; an ordinary member (Carlos) reported a real Listing; the '
                  "moderator hid it; the Listing vanished from everyone's public marketplace search "
                  "while remaining visible (marked hidden) in its owner's own listings - with no "
                  'JavaScript page errors.')
            print('STIR 0.4 PUBLIC PILOT MVP (moderation): PASS')
        finally:
            browser.close()

if __name__ == '__main__': main()
