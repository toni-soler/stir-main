"""Real Shell/STIR catalog-to-negotiation browser proof in a new local test tenant.

Never run against production. Requires the optional community-catalog extension
mounted at /community-catalog and a local STIR development stack.
"""
import os
from pathlib import Path
import secrets
import struct
import uuid
import urllib.request
import zlib
import json
import binascii

from playwright.sync_api import sync_playwright, expect
import smoke
import multitenant

runtime_root = Path(os.environ.get("STIR_RUNTIME_ROOT", Path(__file__).resolve().parents[1]))
smoke.ROOT = runtime_root
multitenant.ROOT = runtime_root
request = smoke.request
sql = multitenant.sql

PERMISSIONS = [
    "stir.listings.read", "stir.listings.create", "stir.listings.update",
    "stir.negotiations.read", "stir.negotiations.create", "stir.negotiations.update",
]


def test_png():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xffffffff)
    pixels = (b"\x00" + b"\x30\x90\x60" * 8) * 8
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def upload_photo(base, listing_id, token):
    boundary = "catalog-e2e-" + uuid.uuid4().hex
    payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"fixture.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
               + test_png() + f"\r\n--{boundary}--\r\n".encode())
    request_url = smoke.BASE + base + "/listings/" + listing_id + "/photos"
    upload_request = urllib.request.Request(request_url, data=payload, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
    })
    with urllib.request.urlopen(upload_request, timeout=30) as response:
        assert response.status == 201
        return json.load(response)["id"]


def member(admin, tenant, role_id, label, suffix):
    email = f"{label}-{suffix}@stir.test"
    password = secrets.token_urlsafe(32)
    base = f"/api/shell/v1/tenants/{tenant}"
    request(base + "/users", "POST", {
        "email": email, "displayName": label, "authProvider": "local",
        "subject": email, "password": password, "role": "member", "enabled": True,
    }, admin, expected=(200, 201))
    initial = request("/api/shell/v1/auth/login", "POST", {"email": email, "password": password})
    request(base + "/roles/users/" + initial["user"]["id"], "PUT", {"roleIds": [role_id]}, admin, 204)
    session = request("/api/shell/v1/auth/login", "POST", {"email": email, "password": password})
    return email, password, session


def main():
    admin = request("/api/shell/v1/auth/login", "POST", {
        "email": "admin@stir.test",
        "password": (runtime_root / ".local/secrets/login_password").read_text().strip(),
    })["accessToken"]
    suffix = uuid.uuid4().hex[:10]
    tenant_name = "Community catalog E2E " + suffix
    tenant = sql(f"select tenant_id from idax_core.tenant_create('catalog-e2e-{suffix}', '{tenant_name}', 'active', false);")
    shell_base = f"/api/shell/v1/tenants/{tenant}"
    role = request(shell_base + "/roles", "POST", {
        "key": "catalog_e2e_" + suffix, "name": "Catalog E2E member",
        "description": "Isolated development fixture", "enabled": True,
    }, admin, expected=(200, 201))
    request(shell_base + "/roles/" + role["id"] + "/permissions", "PUT", PERMISSIONS, admin)
    _ana_email, _ana_password, ana = member(admin, tenant, role["id"], "ana", suffix)
    pedro_email, pedro_password, pedro = member(admin, tenant, role["id"], "pedro", suffix)
    base = f"/api/stir/tenants/{tenant}"
    title = "Community catalog resource " + suffix
    listing = request(base + "/listings", "POST", {
        "direction": "OFFER", "title": title, "description": "Dedicated browser test fixture",
        "category": "general", "resourceKind": "physical", "location": "Test tenant",
    }, ana["accessToken"], 201)
    photo_id = upload_photo(base, listing["id"], ana["accessToken"])
    assert request(base + "/listings/" + listing["id"] + "/photos", token=pedro["accessToken"])[0]["id"] == photo_id

    errors = []
    expect_community_photos = os.environ.get("STIR_CATALOG_EXPECT_PHOTOS", "1") != "0"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(locale="es-ES", viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(60000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(smoke.BASE)
            page.locator("input[type=email]").fill(pedro_email)
            page.locator("input[type=password]").fill(pedro_password)
            page.locator("button[type=submit]").click()
            page.locator(".surface > header select").first.select_option(label=tenant_name)
            page.locator(".module-card").filter(has=page.get_by_role("heading", name="Community catalog", exact=True)).click()
            card = page.locator(".community-grid article").filter(has=page.get_by_role("button", name=title, exact=True))
            expect(card).to_be_visible()
            if expect_community_photos:
                expect(card.locator("img.community-image")).to_be_visible()
            card.get_by_role("button", name=title, exact=True).click()
            expect(page.locator(".community-detail h1")).to_have_text(title)
            if expect_community_photos:
                expect(page.locator(".community-detail img.community-image")).to_be_visible()
            page.locator(".community-detail textarea").fill("I would like this resource")
            page.locator(".community-detail button[type=submit]").click()
            expect(page).to_have_url(__import__("re").compile(r"/stir/negotiations/[0-9a-f-]+$"))
            negotiation_id = page.url.rsplit("/", 1)[-1]
            negotiation = request(base + "/negotiations/" + negotiation_id, token=pedro["accessToken"])
            assert negotiation["listingId"] == listing["id"]
            assert negotiation["offers"][0]["message"] == "I would like this resource"
            assert negotiation["offers"][0]["authorId"] == pedro["user"]["id"]
            page.goto(smoke.BASE + "/stir/listing/" + listing["id"])
            expect(page.locator(".stir-gallery img")).to_be_visible()
            assert not errors, errors
            if marker_path := os.environ.get("STIR_CATALOG_UPGRADE_MARKER"):
                target = Path(marker_path).resolve()
                if not target.is_relative_to((runtime_root / ".local").resolve()):
                    raise ValueError("Upgrade marker must stay under the ignored .local directory")
                # Recorded so the upgrade script can assert the compatibility surface (COMMUNITY_EXTENSION_GUIDE.md)
                # stays stable across a frontend-only swap - the whole point of declaring it.
                instance = request("/api/stir/instance")
                target.write_text(json.dumps({
                    "tenant_name": tenant_name, "tenant_id": tenant,
                    "pedro_email": pedro_email, "pedro_password": pedro_password,
                    "listing_id": listing["id"], "negotiation_id": negotiation_id,
                    "photo_id": photo_id, "title": title,
                    "stir_version": instance["stirVersion"],
                    "catalog_contract_version": instance["catalogContractVersion"],
                    "external_contract_schema_version": instance["externalContractSchemaVersion"],
                }), encoding="utf-8")
            print("PASS: ordinary participant sent a real offer through the second Shell presentation; STIR gallery showed the authenticated photo")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
