"""Real browser check of STIR's user manual: the "Manual de usuario" link in the STIR tabs opens the
static manual served next to the extension, with its own CSS and JS and every chapter. Uses an
isolated tenant, like the other E2E scripts."""
import os
import pathlib
import uuid
from playwright.sync_api import sync_playwright, expect
from economic_exchange_e2e import PERMISSIONS
from marketplace_browser_smoke import make_login
from multitenant import sql
from smoke import BASE, ROOT, request

ARTIFACTS = pathlib.Path(os.environ.get('STIR_E2E_ARTIFACTS', 'e2e-artifacts'))


ENGLISH_TITLE = 'STIR user manual'
MIN_FIGURES = 8


def main():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test', 'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]; name = 'Manual ' + suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('manual-{suffix}','{name}','active',false);")
    shell = f'/api/shell/v1/tenants/{tenant}'
    role = request(shell + '/roles', 'POST', {'key': 'manual' + suffix, 'name': 'Member', 'description': 'Local browser test', 'enabled': True}, admin, (200, 201))
    request(shell + '/roles/' + role['id'] + '/permissions', 'PUT', PERMISSIONS + ['stir.agreements.fulfil', 'stir.notifications.read'], admin)
    email, password = make_login(admin, tenant, role['id'], 'reader', suffix)
    problems = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 900})
            page = context.new_page(); page.set_default_timeout(30000)
            page.on('pageerror', lambda e: problems.append(str(e)))
            page.goto(BASE)
            page.locator('input[type=email]').fill(email); page.locator('input[type=password]').fill(password)
            page.locator('button[type=submit]').click()
            page.locator('.module-card').filter(has=page.get_by_role('heading', name='STIR', exact=True)).click()
            page.locator('.surface > header select').first.select_option(label=name)
            link = page.get_by_role('link', name='Manual de usuario')
            expect(link).to_be_visible()
            with context.expect_page() as opened:
                link.click()
            manual = opened.value
            manual.on('pageerror', lambda e: problems.append('manual: ' + str(e)))
            manual.on('console', lambda m: m.type == 'error' and problems.append('manual console: ' + m.text))
            manual.wait_for_load_state('networkidle')
            expect(manual.locator('h1')).to_have_text('Manual de usuario de STIR')
            chapters = manual.locator('main section').count()
            assert chapters == 21, chapters
            assert manual.locator('header').evaluate("e => getComputedStyle(e).backgroundColor") == 'rgb(15, 42, 42)'
            manual.get_by_role('link', name='Cuando algo no se cumplió: casos').click()
            expect(manual.locator('#casos h2')).to_be_in_viewport()
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            manual.goto(manual.url.split('#')[0])
            manual.screenshot(path=str(ARTIFACTS / 'manual-stir.png'))
            manual.emulate_media(media='print')
            assert manual.locator('aside').evaluate("e => getComputedStyle(e).display") == 'none'
            manual.emulate_media(media='screen')
            # Every screenshot in the manual loads, and the English copy is one link away with the same chapters.
            for copy in ('es', 'en'):
                if copy == 'en':
                    manual.locator('header a', has_text='English version').click()
                    expect(manual.locator('h1')).to_have_text(ENGLISH_TITLE)
                    assert manual.locator('main section').count() == chapters
                manual.wait_for_load_state('networkidle')
                images = manual.locator('main figure img')
                for i in range(images.count()):
                    images.nth(i).scroll_into_view_if_needed()
                    expect(images.nth(i)).to_have_js_property('complete', True)
                    assert images.nth(i).evaluate('img => img.naturalWidth') > 600, images.nth(i).get_attribute('src')
                assert images.count() >= MIN_FIGURES, images.count()
            assert not problems, problems
            print(f'PASS: the STIR manual opens from the tabs with its {chapters} chapters, its own CSS, prints without navigation, shows every screenshot, and has an English copy')
            print('STIR MANUAL BROWSER CHECK: PASS')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
