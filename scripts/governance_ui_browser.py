"""Real browser proof of the Seven Keys governance UI: bootstrap ceremony, an ordinary
constitutional amendment, an emergency suspension and a same-controller credential rotation -
all driven through the actual rendered page (real WebCrypto Ed25519 keys, real IndexedDB
custody, real backend verification), not the HTTP API directly. Only creates an isolated local
development fixture tenant; no private key ever leaves the browser process.
"""
import json
import re
import uuid
from playwright.sync_api import sync_playwright, expect
from smoke import ROOT, request
from marketplace_browser_smoke import make_login
from community_references_e2e import MEMBER

def main():
    admin=request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test',
        'password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix=uuid.uuid4().hex[:8]; name='Governance UI '+suffix
    from multitenant import sql
    tenant=sql(f"select tenant_id from idax_core.tenant_create('gov-ui-{suffix}','{name}','active',false);")
    role=request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':'publisher'+suffix,'name':'Governance publisher','description':'Local browser test','enabled':True},admin,(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+role['id']+'/permissions','PUT',MEMBER+['stir.references.publish'],admin)
    ana_login=make_login(admin,tenant,role['id'],'ana',suffix)
    ana=request('/api/shell/v1/auth/login','POST',dict(zip(['email','password'],ana_login)))['accessToken']
    base=f'/api/stir/tenants/{tenant}'
    request(base+'/economic/marketplace/bootstrap','POST',{'communityName':name,'unitCode':'GOV','unitScale':2},ana)

    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        errors=[]
        try:
            page=browser.new_context(locale='es-ES',viewport={'width':1440,'height':1400}).new_page()
            page.set_default_timeout(30000);page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://localhost:8089')
            page.locator('input[type=email]').fill(ana_login[0]);page.locator('input[type=password]').fill(ana_login[1])
            page.locator('button[type=submit]').click()
            page.locator('.module-card').filter(has=page.get_by_role('heading',name='STIR',exact=True)).click()
            page.locator('.surface > header select').first.select_option(label=name)
            page.goto('http://localhost:8089/stir/governance')
            expect(page.get_by_text('Define sus siete asientos',exact=False)).to_be_visible()

            # --- Bootstrap: generate all seven seat keys plus the guardian's, entirely client-side ---
            forms=page.locator('article.stir-form')
            expect(forms).to_have_count(8)
            for i in range(8):
                forms.nth(i).get_by_role('button',name='Generar una clave en este dispositivo',exact=True).click()
                expect(forms.nth(i).get_by_text('Clave publica:',exact=False)).to_be_visible()
            page.get_by_role('button',name='Construir la carga de inicio',exact=True).click()
            expect(page.get_by_text('Carga completa de inicio',exact=True)).to_be_visible()
            sign_forms=page.locator('article.stir-form')
            expect(sign_forms).to_have_count(8)
            for i in range(8):
                sign_forms.nth(i).get_by_role('button',name='Firmar con la clave de este dispositivo',exact=True).click()
            expect(page.get_by_text('Se han reunido las ocho firmas.',exact=True)).to_be_visible()
            page.get_by_role('button',name='Activar las siete llaves',exact=True).click()
            expect(page.get_by_role('heading',name='Gobernanza de las Siete Llaves',exact=True)).to_be_visible()
            for ordinal in range(1,8):
                expect(page.get_by_text(f'Asiento {ordinal}: ACTIVE',exact=False)).to_be_visible()
            expect(page.get_by_text('Version de la constitucion: 1',exact=False)).to_be_visible()

            view=request(f'{base}/references/governance/'+request(f'{base}/references/community',token=ana)['communityId'],token=ana)
            authority=view['authorityId']

            # --- Ordinary amendment: raise the operational observation floor, 7-of-7 ---
            page.get_by_text('Proponer un cambio',exact=True).click()
            page.get_by_label('Observaciones mínimas',exact=True).fill('7')
            page.get_by_test_id('proposal-reason').fill('Raise the observation floor after review')
            page.get_by_test_id('proposal-submit').click()
            proposal=page.locator('article.stir-panel').filter(has_text='Enmendar la constitucion').first
            proposal.get_by_text('Firmas',exact=True).click()
            proposal.get_by_role('button',name='Cargar carga de firma',exact=True).click()
            for ordinal in range(1,8):
                proposal.get_by_role('button',name=f'Firmar con la clave de este dispositivo (Asiento {ordinal})',exact=True).click()
                expect(proposal.get_by_text(f'Asiento {ordinal}: Firma lista',exact=True)).to_be_visible()
            proposal.locator('summary',has_text='Activar').click()
            proposal.get_by_role('button',name='Activar',exact=True).click()
            expect(page.get_by_text('Version de la constitucion: 2',exact=False)).to_be_visible()

            # --- Emergency suspension of seat 7 by the Guardian ---
            page.get_by_text('Suspender un asiento de emergencia',exact=True).click()
            suspend=page.locator('details').filter(has_text='Suspender un asiento de emergencia')
            suspend.locator('select').select_option('7')
            suspend.get_by_label('Codigo de motivo',exact=True).fill('DEVICE_LOST')
            suspend.get_by_role('button',name='Firmar con la clave de este dispositivo',exact=True).click()
            suspend.get_by_role('button',name='Suspender',exact=True).click()
            expect(page.get_by_text('Asiento 7: EMERGENCY_SUSPENDED',exact=False)).to_be_visible()
            old_key=page.locator('li').filter(has_text='Asiento 7:').inner_text()

            # --- Same-controller credential rotation for the suspended seat, via the cross-device
            # signing tool: obtain a fresh incoming public key, then the guardian and possession
            # signatures over the exact stored proposal payload, all through the generic tool. ---
            sign_tool=page.locator('section.stir-panel').filter(has_text='Herramienta de firma de gobernanza')
            sign_tool.get_by_test_id('sign-tool-input').fill(json.dumps({'kind':'INVITATION','authorityId':authority,
                'role':'seat-7-incoming','controllerId':'operator','credentialId':str(uuid.uuid4())}))
            sign_tool.get_by_test_id('sign-tool-process').click()
            expect(sign_tool.get_by_test_id('sign-tool-output')).to_have_value(re.compile('CONTRIBUTION'),timeout=15000)
            contribution=json.loads(sign_tool.get_by_test_id('sign-tool-output').input_value())
            new_public_key=contribution['publicKey']

            # The proposal form's <details> was already opened for the amendment above and stays
            # open across re-renders (native DOM state, not React state) - only open it if closed.
            propose_form=page.locator('details').filter(has_text='Proponer un cambio')
            if not propose_form.locator('select').first.is_visible():
                page.get_by_text('Proponer un cambio',exact=True).click()
            propose_form.locator('select').first.select_option(label='Rotar una credencial')
            propose_form.get_by_label('Clave publica',exact=True).fill(new_public_key)
            page.get_by_test_id('proposal-reason').fill('Lost-device recovery, same controller')
            page.get_by_test_id('proposal-submit').click()
            rotation=page.locator('article.stir-panel').filter(has_text='Rotar una credencial').first
            rotation.get_by_text('Firmas',exact=True).click()
            rotation.get_by_role('button',name='Cargar carga de firma',exact=True).click()
            # Capture the exact stored payload text (needed for the guardian/possession
            # signatures below) from seat 1's export panel BEFORE it signs and that panel
            # disappears - same text every seat would export, since it is the one proposal
            # payload everyone signs.
            rotation.get_by_text('Exportar tarea de firma',exact=True).first.click()
            payload_task=json.loads(rotation.locator('textarea[readonly]').first.input_value())
            canonical_text=payload_task['canonicalText']
            for ordinal in range(1,7):
                rotation.get_by_role('button',name=f'Firmar con la clave de este dispositivo (Asiento {ordinal})',exact=True).click()
                expect(rotation.get_by_text(f'Asiento {ordinal}: Firma lista',exact=True)).to_be_visible()

            sign_tool.get_by_test_id('sign-tool-input').fill(json.dumps({'kind':'SIGN','authorityId':authority,
                'role':'guardian','domain':'STIR:MARKET:GUARDIAN:V1','canonicalText':canonical_text}))
            sign_tool.get_by_test_id('sign-tool-process').click()
            expect(sign_tool.get_by_test_id('sign-tool-output')).to_have_value(re.compile('SIGNATURE'),timeout=15000)
            guardian_signature=json.loads(sign_tool.get_by_test_id('sign-tool-output').input_value())['signature']

            sign_tool.get_by_test_id('sign-tool-input').fill(json.dumps({'kind':'SIGN','authorityId':authority,
                'role':'seat-7-incoming','domain':'STIR:MARKET:POSSESSION:V1','canonicalText':canonical_text}))
            sign_tool.get_by_test_id('sign-tool-process').click()
            expect(sign_tool.get_by_test_id('sign-tool-output')).to_have_value(re.compile('SIGNATURE'),timeout=15000)
            possession_signature=json.loads(sign_tool.get_by_test_id('sign-tool-output').input_value())['signature']

            rotation.locator('summary',has_text='Activar').click()
            rotation.get_by_label('Firma del guardián',exact=True).fill(guardian_signature)
            rotation.get_by_label('Firma de posesion de la nueva clave',exact=True).fill(possession_signature)
            rotation.get_by_role('button',name='Activar',exact=True).click()
            expect(page.get_by_text('Asiento 7: ACTIVE',exact=False)).to_be_visible()
            new_key_row=page.locator('li').filter(has_text='Asiento 7:').inner_text()
            assert new_key_row!=old_key,'seat 7 public key fingerprint did not change after rotation'

            assert not errors,errors
            page.screenshot(path=str(ROOT/'.local/governance-ui-final.png'),full_page=True)
            print('PASS BROWSER GOVERNANCE: bootstrap (7 seats + guardian, WebCrypto Ed25519), '
                  '7-of-7 constitutional amendment, guardian emergency suspension, and '
                  '6-of-6 + guardian + possession same-controller credential rotation - all through the rendered UI.')
        except Exception:
            page.screenshot(path=str(ROOT/'.local/governance-ui-failure.png'),full_page=True)
            raise
        finally: browser.close()

if __name__=='__main__':main()
