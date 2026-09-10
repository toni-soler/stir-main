"""Browser proof against the isolated local instance. Requires requirements-browser.txt."""
from pathlib import Path
import uuid
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(channel="msedge",headless=True)
    try:
        page=browser.new_page(locale="es-ES",viewport={"width":1440,"height":1000})
        page.set_default_timeout(60000)
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto("http://localhost:8089")
        page.locator("input[type=email]").fill("admin@stir.test")
        page.locator("input[type=password]").fill((ROOT/".local/secrets/login_password").read_text().strip())
        page.locator("button[type=submit]").click()
        page.locator(".module-card").filter(has=page.get_by_role("heading",name="STIR",exact=True)).click()
        page.locator(".surface > header select").first.select_option(label="STIR Development")
        page.get_by_role("button",name="+ Crear publicación",exact=True).click()
        title="Browser resource "+uuid.uuid4().hex[:8]
        page.get_by_label("Quiero").select_option("WANTED")
        page.get_by_label("Título",exact=True).fill(title)
        page.get_by_label("Descripción",exact=True).fill("Recurso de prueba de navegador")
        page.get_by_label("Categoría").select_option("general")
        page.get_by_label("Tipo de recurso").select_option("service")
        page.get_by_role("button",name="Guardar",exact=True).click()
        card=page.locator(".stir-card").filter(has=page.get_by_role("heading",name=title,exact=True))
        expect(card).to_be_visible()
        card.get_by_role("button",name="Editar",exact=True).click()
        page.get_by_label("Título",exact=True).fill(title+" edited")
        page.get_by_role("button",name="Guardar",exact=True).click()
        page.get_by_label("Buscar",exact=True).fill(title)
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
    finally:
        browser.close()
