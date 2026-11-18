"""Real browser proof of module role templates in the Shell's administration, in Spanish.

The administrator of a new workspace opens "Crear" on the Users screen and finds "Participante STIR"
already ticked; creating the member without touching it leaves them a participant. On the Roles
screen, STIR's roles say they come from a module template, and cannot be deleted. Isolated
synthetic workspace, STIR_TEST_URL (smoke.BASE); no JavaScript page error is tolerated.
"""
import os
import pathlib
import secrets
import uuid

from playwright.sync_api import expect, sync_playwright

from smoke import BASE, ROOT, request

SHELL = '/api/shell/v1'
ARTIFACTS = pathlib.Path(os.environ.get('STIR_E2E_ARTIFACTS', 'e2e-artifacts'))
ADMINISTRATION = ['system.users.read', 'system.users.create', 'system.users.update', 'system.roles.read', 'system.roles.manage']


def main():
    admin = request(SHELL + '/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]
    name, email, password = 'Huerta ' + suffix, f'owner-{suffix}@stir.test', secrets.token_urlsafe(18)
    tenant = request(SHELL + '/platform/tenants', 'POST', {'tenantCode': 'roles-b-' + suffix, 'tenantName': name,
        'adminEmail': email, 'adminDisplayName': 'Owner ' + suffix, 'adminPassword': password}, admin, 201)['id']
    t = f'{SHELL}/tenants/{tenant}'
    # A deliberate administrative grant for the workspace's administrator: the Users and Roles screens.
    administration = request(t + '/roles', 'POST', {'key': 'workspace_admin', 'name': 'Administración', 'enabled': True}, admin, (200, 201))
    request(t + f"/roles/{administration['id']}/permissions", 'PUT', ADMINISTRATION, admin)
    owner_id = request(SHELL + '/auth/login', 'POST', {'email': email, 'password': password})['user']['id']
    current = next(row for row in request(t + '/roles/users', token=admin) if row['userId'] == owner_id)['roleIds']
    request(t + f'/roles/users/{owner_id}', 'PUT', {'roleIds': sorted(set(current) | {administration['id']})}, admin, 204)
    participant = next(item['roleId'] for item in request(t + '/roles/templates', token=admin)
                       if item['extensionId'] == 'stir' and item['key'] == 'participant')

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 900}).new_page()
            page.set_default_timeout(30000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(BASE)
            page.locator('input[type=email]').fill(email); page.locator('input[type=password]').fill(password)
            page.locator('button[type=submit]').click()
            page.locator('.surface > header select').first.select_option(label=name)

            # The administration screens are reached from the Shell's own navigation (they are not
            # server routes: loading /users directly is refused).
            page.locator('aside nav button[title="Usuarios"]').click()
            page.get_by_role('button', name='+ Crear').click()
            dialog = page.locator('.crud-dialog')
            box = dialog.locator('.permission-row', has_text='Participante STIR').locator('input[type=checkbox]')
            expect(box).to_be_checked()
            print('PASS: the creation form shows "Participante STIR" already ticked')
            member = f'bea-{suffix}@stir.test'
            dialog.locator('input[type=text]').first.fill('Bea ' + suffix)
            dialog.locator('input[type=email]').fill(member)
            dialog.locator('input[type=password]').fill(secrets.token_urlsafe(18))
            dialog.get_by_role('button', name='Guardar').click()
            expect(page.locator('td', has_text=member)).to_be_visible()
            created = next(u for u in request(t + '/users', token=admin) if u['email'] == member)
            roles = next(row for row in request(t + '/roles/users', token=admin) if row['userId'] == created['userId'])['roleIds']
            template_roles = {r['id'] for r in request(t + '/roles', token=admin) if r.get('sourceType') == 'EXTENSION_TEMPLATE'}
            # Core adds its own system role for the membership label ("user"); the template sits beside it.
            assert participant in roles and template_roles & set(roles) == {participant}, roles
            print('PASS: a member created without touching the roles is a STIR participant')

            page.locator('aside nav button[title="Roles"]').click()
            row = page.locator('tr', has_text='Participante STIR')
            expect(row).to_contain_text('Plantilla de módulo')
            expect(row.get_by_role('button', name='Eliminar')).to_be_disabled()
            expect(page.locator('tr', has_text='Administración')).to_contain_text('Este espacio')
            expect(page.get_by_role('button', name='Dar los roles de los módulos a los miembros actuales')).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / 'role-templates-roles.png'), full_page=True)
            print('PASS: the Roles screen shows which roles come from a module template, and cannot delete them')
            assert not errors, errors
            print('ROLE TEMPLATES BROWSER E2E: PASS')
        except Exception:
            page.screenshot(path=str(ARTIFACTS / 'role-templates-failure.png'), full_page=True)
            print('--- at ' + page.url)
            print(page.locator('body').inner_text()[:1500])
            raise
        finally:
            browser.close()


if __name__ == '__main__':
    main()
