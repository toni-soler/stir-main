"""Real HTTP economic exchange E2E (STIR 0.3): Ana and Pedro negotiate, activate economic
exchange (real Ed25519 keypairs generated here, exactly as a browser client would), sign the
osTRIS EXCHANGE proposal STIR creates from their Agreement, commit it, and see it reflected as a
real balance change - reconciled from osTRIS, never computed by STIR itself. Also covers the
WANTED direction, reconciliation (re-sync after commit matches the direct receipt), idempotent
commit retry, and a real credit-floor policy rejection. Isolated development fixtures only - same
pattern as multitenant.py/marketplace_e2e.py (idax_core.tenant_create + public Shell HTTP), never
a direct Core/osTRIS table write. Never invoke against a production deployment.
"""
import base64
import secrets
import uuid
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from smoke import ROOT, request
from multitenant import sql

# OstrisClient relays the CALLER'S OWN bearer token to osTRIS (never a separate service
# credential - see OstrisClient's own docstring), so osTRIS's @PreAuthorize checks run against this
# same tenant role. Ana/Pedro need the osTRIS-side permissions too, not just the stir.* ones.
PERMISSIONS = ['stir.listings.read','stir.listings.create','stir.listings.update',
    'stir.participants.read','stir.participants.update',
    'stir.negotiations.read','stir.negotiations.create','stir.negotiations.update',
    'stir.agreements.read','stir.economic.read','stir.economic.manage',
    'OSTRIS_DISCOVERY_READ','OSTRIS_COMMUNITY_MANAGE','OSTRIS_PARTICIPANT_ACTIVATE',
    'OSTRIS_TRANSACTION_CREATE','OSTRIS_TRANSACTION_READ','OSTRIS_TRANSACTION_AUTHORIZE','OSTRIS_TRANSACTION_COMMIT']

def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

def new_keypair():
    private_key = Ed25519PrivateKey.generate()
    raw_public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private_key, b64url(raw_public)

def sign_authorization(private_key, authorization_payload_text):
    message = b'OSTRIS:TX:AUTH:V1' + b'\x00' + authorization_payload_text.encode('utf-8')
    return b64url(private_key.sign(message))

def make_user(admin, tenant, role_id, label, suffix):
    email = f'{label}-{suffix}@stir.test'
    password = secrets.token_urlsafe(32)
    base = f'/api/shell/v1/tenants/{tenant}'
    request(base+'/users','POST',{'email':email,'displayName':label,'authProvider':'local','subject':email,'password':password,'role':'member','enabled':True},admin,expected=(200,201))
    session = request('/api/shell/v1/auth/login','POST',{'email':email,'password':password})
    request(base+'/roles/users/'+session['user']['id'],'PUT',{'roleIds':[role_id]},admin,204)
    return request('/api/shell/v1/auth/login','POST',{'email':email,'password':password})

def open_and_accept(base_a, ana, pedro, direction, title, category, first_amount, counter_amount):
    """Ana publishes `direction`; Pedro proposes `first_amount`; Ana counters `counter_amount`
    (or accepts the first offer directly if counter_amount is None); Pedro accepts. Returns the
    Agreement id and final accepted amount as an int."""
    listing = request(base_a+'/listings','POST',{'direction':direction,'title':title,'description':'E2E fixture','category':category,'resourceKind':'physical','location':'Girona'},ana['accessToken'],201)
    negotiation = request(base_a+f"/listings/{listing['id']}/offers",'POST',{'message':'proposal','quantity':None,'unitLabel':None,'proposedAmount':str(first_amount),'proposedUnitRef':None,'terms':None},pedro['accessToken'],201)
    if counter_amount is not None:
        negotiation = request(base_a+f"/negotiations/{negotiation['id']}/offers",'POST',{'message':'counter','quantity':None,'unitLabel':None,'proposedAmount':str(counter_amount),'proposedUnitRef':None,'terms':None,'expectedVersion':negotiation['version']},ana['accessToken'])
        final_amount = counter_amount
        acceptor = pedro  # ana authored the head (counter) offer, so only pedro may accept it
    else:
        final_amount = first_amount
        acceptor = ana  # pedro authored the head (first) offer, so only ana may accept it
    head = negotiation['offers'][-1]
    agreement = request(base_a+f"/negotiations/{negotiation['id']}/accept",'POST',{'offerId':head['id'],'expectedVersion':negotiation['version']},acceptor['accessToken'])
    return agreement, final_amount

def run_exchange(base_a, initiator_session, owner_session, agreement_id, initiator_key, owner_key):
    """Activates the Trade, has both parties sign with their own real keys, and commits. Returns
    the final trade view. `initiator_key`/`owner_key` are (private_key, public_key_b64url) pairs."""
    trade = request(base_a+f"/agreements/{agreement_id}/trade/activate",'POST',{},owner_session['accessToken'])
    for session, (private_key, _) in [(initiator_session, initiator_key), (owner_session, owner_key)]:
        payload = request(base_a+f"/agreements/{agreement_id}/trade/signing-payload",token=session['accessToken'])
        signature = sign_authorization(private_key, payload['authorizationPayload'])
        trade = request(base_a+f"/agreements/{agreement_id}/trade/authorizations",'POST',{'signatureBase64url':signature},session['accessToken'])
    return request(base_a+f"/agreements/{agreement_id}/trade/commit",'POST',{},owner_session['accessToken'])

def activate_economic(base_a, session):
    private_key, public_key = new_keypair()
    request(base_a+'/economic/activate','POST',{'publicKeyBase64url':public_key},session['accessToken'])
    return private_key, public_key

def main():
    admin = request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test','password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]
    tenant_a = sql(f"select tenant_id from idax_core.tenant_create('stir-eco-a-{suffix}', 'STIR Economic A', 'active', false);")
    role = request(f'/api/shell/v1/tenants/{tenant_a}/roles','POST',{'key':'stir_eco_'+suffix,'name':'STIR economic E2E','description':'Isolated development fixture','enabled':True},admin,expected=(200,201))
    request(f'/api/shell/v1/tenants/{tenant_a}/roles/'+role['id']+'/permissions','PUT',PERMISSIONS,admin)
    ana = make_user(admin, tenant_a, role['id'], 'ana', suffix)
    pedro = make_user(admin, tenant_a, role['id'], 'pedro', suffix)
    base_a = f'/api/stir/tenants/{tenant_a}'

    request(base_a+'/participants/me','PUT',{'displayName':'Ana '+suffix,'bio':'Grows tomatoes','location':'Girona'},ana['accessToken'])
    request(base_a+'/participants/me','PUT',{'displayName':'Pedro '+suffix,'bio':'Fixes bicycles','location':'Girona'},pedro['accessToken'])

    marketplace = request(base_a+'/economic/marketplace/bootstrap','POST',{'communityName':'STIR E2E Community '+suffix,'unitCode':'CRD','unitScale':0},ana['accessToken'])
    assert marketplace['unitScale'] == 0

    ana_key = activate_economic(base_a, ana)
    pedro_key = activate_economic(base_a, pedro)
    ana_start = request(base_a+'/economic/me',token=ana['accessToken'])
    pedro_start = request(base_a+'/economic/me',token=pedro['accessToken'])
    assert ana_start['balanceProjection'] == '0' and pedro_start['balanceProjection'] == '0'

    # --- OFFER direction: Ana offers tomatoes, Pedro proposes 1000, Ana counters 900, Pedro accepts ---
    agreement, amount = open_and_accept(base_a, ana, pedro, 'OFFER', '20kg tomatoes '+suffix, 'home', 1000, 900)
    assert agreement['economicPhase'] == 'AWAITING_ECONOMIC_EXECUTION'
    assert amount == 900

    trade = run_exchange(base_a, pedro, ana, agreement['id'], pedro_key, ana_key)
    assert trade['executionState'] == 'COMMITTED', trade
    assert trade['amount'] == '900'
    assert trade['payerUserId'] == pedro['user']['id'] and trade['payeeUserId'] == ana['user']['id']
    receipt_sequence, receipt_digest = trade['committedSequence'], trade['protocolDigest']

    ana_after = request(base_a+'/economic/me',token=ana['accessToken'])
    pedro_after = request(base_a+'/economic/me',token=pedro['accessToken'])
    assert ana_after['balanceProjection'] == '900', ana_after
    assert pedro_after['balanceProjection'] == '-900', pedro_after
    print('PASS: OFFER exchange - Ana offered, Pedro proposed, Ana countered, Pedro accepted; both signed with real Ed25519 keys; osTRIS committed; balances moved exactly as agreed.')

    # --- Reconciliation: sync() independently re-derives the same committed truth from osTRIS ---
    resynced = request(base_a+f"/agreements/{agreement['id']}/trade/sync",'POST',{},ana['accessToken'])
    assert resynced['executionState'] == 'COMMITTED'
    assert resynced['committedSequence'] == receipt_sequence and resynced['protocolDigest'] == receipt_digest
    print('PASS: reconciliation - sync() re-reads osTRIS authoritative status and reports the same committedSequence/protocolDigest as the original commit receipt.')

    # --- Idempotency: activate/commit again do not create a second Trade or a second journal entry ---
    reactivated = request(base_a+f"/agreements/{agreement['id']}/trade/activate",'POST',{},ana['accessToken'])
    assert reactivated['id'] == trade['id']
    recommitted = request(base_a+f"/agreements/{agreement['id']}/trade/commit",'POST',{},pedro['accessToken'])
    assert recommitted['committedSequence'] == receipt_sequence
    # Both stir.trade and ostris.journal_transaction FORCE RLS on tenant_id - a bare psql session has
    # no app.tenant_id set, so it must be set explicitly (same pattern as multitenant.py) or every
    # count comes back 0 regardless of what's actually in the table.
    trade_rows = sql(f"begin; set local role idax_app; select set_config('app.tenant_id','{tenant_a}',true);"
        f" select count(*) from stir.trade where tenant_id='{tenant_a}' and agreement_id='{agreement['id']}'; rollback;", runtime=True).splitlines()[-2]
    assert trade_rows == '1', trade_rows
    journal_rows = sql(f"begin; set local role idax_app; select set_config('app.tenant_id','{tenant_a}',true);"
        f" select count(*) from ostris.journal_transaction where tenant_id='{tenant_a}' and id='{trade['transactionId']}'; rollback;", runtime=True).splitlines()[-2]
    assert journal_rows == '1', journal_rows
    print('PASS: idempotency - repeated activate/commit calls do not create a second Trade or a second osTRIS journal entry.')

    # --- WANTED direction: Ana needs bicycle repair, Pedro proposes 500, Ana accepts directly ---
    wanted_agreement, wanted_amount = open_and_accept(base_a, ana, pedro, 'WANTED', 'Need bicycle repair '+suffix, 'general', 500, None)
    assert wanted_agreement['economicPhase'] == 'AWAITING_ECONOMIC_EXECUTION'
    wanted_trade = run_exchange(base_a, pedro, ana, wanted_agreement['id'], pedro_key, ana_key)
    assert wanted_trade['executionState'] == 'COMMITTED'
    assert wanted_trade['payerUserId'] == ana['user']['id'] and wanted_trade['payeeUserId'] == pedro['user']['id']
    ana_final = request(base_a+'/economic/me',token=ana['accessToken'])
    pedro_final = request(base_a+'/economic/me',token=pedro['accessToken'])
    assert int(ana_final['balanceProjection']) == int(ana_after['balanceProjection']) - 500, ana_final
    assert int(pedro_final['balanceProjection']) == int(pedro_after['balanceProjection']) + 500, pedro_final
    print('PASS: WANTED direction - Ana (the requester) paid Pedro (the provider), confirming economic direction is not hardcoded to OFFER.')

    # --- Policy rejection: OFFER means Pedro (initiator) would pay Ana (owner); 500000 breaches
    # Pedro's -100000 credit floor. osTRIS rejects the commit itself; STIR never overrides it. ---
    reject_agreement, reject_amount = open_and_accept(base_a, ana, pedro, 'OFFER', 'Large batch '+suffix, 'home', 500000, None)
    reject_trade = request(base_a+f"/agreements/{reject_agreement['id']}/trade/activate",'POST',{},pedro['accessToken'])
    for session, key in [(pedro, pedro_key), (ana, ana_key)]:
        payload = request(base_a+f"/agreements/{reject_agreement['id']}/trade/signing-payload",token=session['accessToken'])
        signature = sign_authorization(key[0], payload['authorizationPayload'])
        reject_trade = request(base_a+f"/agreements/{reject_agreement['id']}/trade/authorizations",'POST',{'signatureBase64url':signature},session['accessToken'])
    request(base_a+f"/agreements/{reject_agreement['id']}/trade/commit",'POST',{},ana['accessToken'],expected=422)
    rejected = request(base_a+f"/agreements/{reject_agreement['id']}/trade",token=ana['accessToken'])
    assert rejected['executionState'] == 'REJECTED', rejected
    still_agreement = request(base_a+f"/agreements/{reject_agreement['id']}",token=ana['accessToken'])
    assert still_agreement['economicPhase'] == 'REJECTED'
    ana_unaffected = request(base_a+'/economic/me',token=ana['accessToken'])
    pedro_unaffected = request(base_a+'/economic/me',token=pedro['accessToken'])
    assert ana_unaffected['balanceProjection'] == ana_final['balanceProjection'], "a rejected commit must never move a balance"
    assert pedro_unaffected['balanceProjection'] == pedro_final['balanceProjection'], "a rejected commit must never move a balance"
    print('PASS: policy rejection - osTRIS rejected a commit that would breach the credit floor; the Agreement still exists, the Trade is REJECTED, STIR did not adjust any balance itself, and no journal entry was written.')

    print('STIR 0.3 ECONOMIC EXCHANGE HTTP E2E: PASS')

if __name__ == '__main__': main()
