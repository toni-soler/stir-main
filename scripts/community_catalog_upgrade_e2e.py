"""Verify an earlier catalog fixture after replacing only the STIR frontend.

Uses a temporary ignored marker from community_catalog_browser_e2e.py. It
performs no business writes and never prints the marker's login credentials.
"""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
import smoke


def main():
    runtime_root = Path(os.environ.get("STIR_RUNTIME_ROOT", Path(__file__).resolve().parents[1])).resolve()
    marker_path = Path(os.environ["STIR_CATALOG_UPGRADE_MARKER"])
    if not marker_path.resolve().is_relative_to((runtime_root / ".local").resolve()):
        raise ValueError("Upgrade marker must stay under the ignored .local directory")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    session = smoke.request("/api/shell/v1/auth/login", "POST", {
        "email": marker["pedro_email"], "password": marker["pedro_password"],
    })
    base = f"/api/stir/tenants/{marker['tenant_id']}"
    listing = smoke.request(base + "/listings/" + marker["listing_id"], token=session["accessToken"])
    negotiation = smoke.request(base + "/negotiations/" + marker["negotiation_id"], token=session["accessToken"])
    photos = smoke.request(base + "/listings/" + marker["listing_id"] + "/photos", token=session["accessToken"])
    assert listing["title"] == marker["title"] and listing["mainPhotoId"] == marker["photo_id"]
    assert negotiation["listingId"] == listing["id"] and negotiation["offers"][0]["message"] == "I would like this resource"
    assert photos[0]["id"] == marker["photo_id"]

    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(locale="es-ES", viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(60000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(smoke.BASE)
            page.locator("input[type=email]").fill(marker["pedro_email"])
            page.locator("input[type=password]").fill(marker["pedro_password"])
            page.locator("button[type=submit]").click()
            page.locator(".surface > header select").first.select_option(label=marker["tenant_name"])
            page.locator(".module-card").filter(has=page.get_by_role("heading", name="Community catalog", exact=True)).click()
            card = page.locator(".community-grid article").filter(has=page.get_by_role("button", name=marker["title"], exact=True))
            expect(card).to_be_visible()
            expect(card.locator("img.community-image")).to_be_visible()
            card.get_by_role("button", name=marker["title"], exact=True).click()
            expect(page.locator(".community-detail h1")).to_have_text(marker["title"])
            expect(page.locator(".community-detail img.community-image")).to_be_visible()
            page.goto(smoke.BASE + "/stir/negotiations/" + marker["negotiation_id"])
            expect(page.locator(".stir-thread")).to_contain_text("I would like this resource")
            page.goto(smoke.BASE + "/stir/listing/" + marker["listing_id"])
            expect(page.locator(".stir-gallery img")).to_be_visible()
            assert not errors, errors
            print("PASS: earlier tenant, listing, authenticated photo and negotiation survive the frontend upgrade")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
