"""Browser proof against the isolated local instance. Requires requirements-browser.txt.

A platform administrator cannot act as a STIR participant (V17 revokes STIR writes from its database role), so
the administrator only registers an ordinary member of "STIR Development"; that member, who joins as a STIR
participant through the module's role template, publishes, edits and closes the listing."""
from pathlib import Path
import secrets
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import BASE, request
from browser_ui import field, explain

ROOT=Path(__file__).resolve().parents[1]
admin=request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test','password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
tenant=next(t['id'] for t in request('/api/shell/v1/platform/tenants',token=admin) if t['name']=='STIR Development')
member_email=f'smoke-{uuid.uuid4().hex[:8]}@stir.test'; member_password=secrets.token_urlsafe(18)
request(f'/api/shell/v1/tenants/{tenant}/users','POST',{'email':member_email,'subject':member_email,'displayName':'Smoke member','authProvider':'local',
    'password':member_password,'role':'member','enabled':True},admin,(200,201))
member=request('/api/shell/v1/auth/login','POST',{'email':member_email,'password':member_password})
request(f'/api/stir/tenants/{tenant}/participants/me','PUT',{'displayName':'Smoke member'},member['accessToken'])
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(channel="msedge",headless=True)
    try:
        page=browser.new_page(locale="es-ES",viewport={"width":1440,"height":1000})
        page.set_default_timeout(60000)
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto(BASE)
        page.locator("input[type=email]").fill(member_email)
        page.locator("input[type=password]").fill(member_password)
        page.locator("button[type=submit]").click()
        page.locator(".module-card").filter(has=page.get_by_role("heading",name="STIR",exact=True)).click()
        page.locator(".surface > header select").first.select_option(label="STIR Development")
        page.get_by_role("button",name="+ Crear publicación",exact=True).click()
        title="Browser resource "+uuid.uuid4().hex[:8]
        page.get_by_label("Quiero").select_option("WANTED")
        page.get_by_label(field("Título")).fill(title)
        page.get_by_label(field("Descripción")).fill("Recurso de prueba de navegador")
        page.get_by_label("Categoría").select_option("general")
        page.get_by_label("Tipo de recurso").select_option("service")
        # A new listing is saved first and stays open so photos can be added; "Listo" leaves the editor.
        page.get_by_role("button",name="Guardar y añadir fotos",exact=True).click()
        expect(page.locator(".stir-success")).to_be_visible()
        page.get_by_role("button",name="Listo",exact=True).click()
        card=page.locator(".stir-card").filter(has=page.get_by_role("heading",name=title,exact=True))
        expect(card).to_be_visible()
        card.get_by_role("button",name="Editar",exact=True).click()
        page.get_by_label(field("Título")).fill(title+" edited")
        page.get_by_role("button",name="Guardar",exact=True).click()
        page.get_by_label(field("Buscar")).fill(title)
        card=page.locator(".stir-card").filter(has=page.get_by_role("heading",name=title+" edited",exact=True))
        expect(card).to_be_visible()
        expect(page.locator(".stir-card")).to_have_count(1)
        page.screenshot(path=str(ROOT/".local/browser-listing.png"),full_page=True)
        page.on("dialog",lambda dialog:dialog.accept())
        card.get_by_role("button",name="Cerrar publicación",exact=True).click()
        expect(page.locator(".stir-card")).to_have_count(0)
        page.get_by_label("Estado").select_option("CLOSED")
        expect(card).to_be_visible()
        page.get_by_role("button",name="Marketplace",exact=True).click()
        page.reload()
        expect(page.get_by_role("button",name="+ Crear publicación",exact=True)).to_be_visible()
        assert not errors,errors
        print("PASS: real browser login, tenant selection, Shell module, WANTED create/list/filter/edit/close, CLOSED query, navigation and deep-link reload; no JavaScript page errors.")
    except Exception:
        explain(page=page)
        raise
    finally:
        browser.close()
