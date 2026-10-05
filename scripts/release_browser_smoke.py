"""Release browser smoke for a deployed STIR (DEV or PRODUCTION), through the rendered UI only.

Two independent browser sessions (Ana and Pedro) complete one normal marketplace flow on the deployment's own demo tenant:
login, profile, listing publish, offer, counteroffer and acceptance, ending on a visible Agreement, with no JavaScript page
error. Only the public Shell HTTP API is used for provisioning (users and a permission role); no database access, so it can
run against stir-dev, which the SQL helpers deliberately refuse.

Environment:
  STIR_BROWSER_URL          base URL of the Shell/STIR UI, e.g. http://localhost:28089 (required)
  STIR_BROWSER_LOGIN_EMAIL  platform administrator login (default admin@stir.test)
  STIR_BROWSER_SECRETS      directory containing login_password (default <repo>/.local/secrets)
  STIR_BROWSER_CHANNEL      Playwright channel, default empty (bundled Chromium)
Run on the DEV VM only; this script creates test users and a test listing on the deployment it targets.
"""
import os
import secrets
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from marketplace_e2e import PERMISSIONS

BASE = os.environ.get('STIR_BROWSER_URL')
SECRETS = Path(os.environ.get('STIR_BROWSER_SECRETS', ROOT / '.local/secrets'))
CHANNEL = os.environ.get('STIR_BROWSER_CHANNEL') or None


def field(page, label_text):
    """The STIR forms render labels without a for/id association, and some labels carry a help sentence, so the control is
    located as the first input or textarea that follows the matching label in the document."""
    return page.locator(f"xpath=//label[contains(., '{label_text}')]/following::*[self::input or self::textarea][1]")


def make_user(admin, tenant, role_id, label, suffix):
    email = f'{label}-{suffix}@stir.test'
    password = secrets.token_urlsafe(24)
    request(f'/api/shell/v1/tenants/{tenant}/users', 'POST', {'email': email, 'displayName': label, 'authProvider': 'local',
            'subject': email, 'password': password, 'role': 'member', 'enabled': True}, admin, expected=(200, 201))
    session = request('/api/shell/v1/auth/login', 'POST', {'email': email, 'password': password})
    request(f'/api/shell/v1/tenants/{tenant}/roles/users/' + session['user']['id'], 'PUT', {'roleIds': [role_id]}, admin, 204)
    return email, password


def main():
    assert BASE, 'set STIR_BROWSER_URL to the deployment UI base URL'
    login_email = os.environ.get('STIR_BROWSER_LOGIN_EMAIL', 'admin@stir.test')
    session = request('/api/shell/v1/auth/login', 'POST', {'email': login_email,
                      'password': (SECRETS / 'login_password').read_text().strip()})
    admin = session['accessToken']
    tenant_row = session['tenants'][0]
    tenant = tenant_row['id']
    tenant_label = tenant_row.get('name') or tenant_row.get('displayName')
    suffix = uuid.uuid4().hex[:8]
    role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'release_browser_' + suffix, 'name': 'Release browser smoke',
                   'description': 'Release browser smoke fixture', 'enabled': True}, admin, expected=(200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/' + role['id'] + '/permissions', 'PUT', PERMISSIONS, admin)
    ana_email, ana_password = make_user(admin, tenant, role['id'], 'ana', suffix)
    pedro_email, pedro_password = make_user(admin, tenant, role['id'], 'pedro', suffix)
    title = 'Tomates ' + suffix

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=CHANNEL, headless=True)
        errors = []
        try:
            ana = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1000}).new_page()
            pedro = browser.new_context(locale='es-ES', viewport={'width': 1440, 'height': 1000}).new_page()
            for page in (ana, pedro):
                page.set_default_timeout(60000)
                page.on('pageerror', lambda error: errors.append(str(error)))

            def login(page, email, password):
                page.goto(BASE)
                expect(page.locator('input[type=email]')).to_be_visible()
                page.locator('input[type=email]').fill(email)
                page.locator('input[type=password]').fill(password)
                page.locator('button[type=submit]').click()
                page.locator('.module-card').filter(has=page.get_by_role('heading', name='STIR', exact=True)).click()
                page.locator('.surface > header select').first.select_option(label=tenant_label)

            login(ana, ana_email, ana_password)
            ana.get_by_role('button', name='Mi perfil', exact=True).click()
            field(ana, 'Nombre visible').fill('Ana ' + suffix)
            field(ana, 'Biografía breve').fill('Cultivo tomates')
            ana.get_by_role('button', name='Guardar', exact=True).click()
            expect(ana.get_by_text('Perfil guardado.')).to_be_visible()

            ana.get_by_role('button', name='← Marketplace', exact=True).click()
            ana.get_by_role('button', name='+ Crear publicación', exact=True).click()
            field(ana, 'Título').fill(title)
            field(ana, 'Descripción').fill('Tomates San Marzano maduros')
            ana.get_by_role('button', name='Guardar', exact=True).click()
            expect(ana.locator('.stir-card').filter(has=ana.get_by_role('button', name=title, exact=True))).to_be_visible()

            login(pedro, pedro_email, pedro_password)
            field(pedro, 'Buscar').fill(title)
            pedro.get_by_role('button', name=title, exact=True).click()
            pedro.get_by_role('button', name='Hacer una propuesta', exact=True).click()
            field(pedro, 'Mensaje').fill('Me interesan 15kg')
            pedro.get_by_role('button', name='Enviar propuesta', exact=True).click()
            expect(pedro.get_by_text('Abierta')).to_be_visible()

            ana.get_by_role('button', name='Mis negociaciones', exact=True).click()
            ana.get_by_role('button', name='Ver', exact=True).click()
            expect(ana.get_by_text('Me interesan 15kg')).to_be_visible()
            ana.get_by_role('button', name='Contraoferta', exact=True).click()
            field(ana, 'Mensaje').fill('Puedo hacer 20kg al mismo precio')
            ana.get_by_role('button', name='Enviar contraoferta', exact=True).click()
            expect(ana.get_by_text('Puedo hacer 20kg al mismo precio')).to_be_visible()

            pedro.reload()
            expect(pedro.get_by_text('Puedo hacer 20kg al mismo precio')).to_be_visible()
            pedro.get_by_role('button', name='Aceptar', exact=True).click()
            expect(pedro.get_by_role('heading', name='Acuerdo', exact=True)).to_be_visible()
            expect(pedro.get_by_text('Sin intercambio económico')).to_be_visible()

            assert not errors, errors
            print('BROWSER RELEASE SMOKE: PASS (two browser sessions: login, profile, listing, offer, counteroffer, acceptance and agreement on '
                  f'{BASE}; no JavaScript page errors)')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
