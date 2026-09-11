"""Real HTTP marketplace E2E: Ana and Pedro reach a verifiable Agreement; Carlos (same tenant,
uninvolved) is denied; a Tenant B participant is denied too. Isolated development fixtures only -
public Core tenant_create + real Shell user/role APIs, exactly like multitenant.py. Never invoke
against a production deployment.
"""
import secrets
import uuid
from smoke import ROOT, request
from multitenant import sql

PERMISSIONS = ['stir.listings.read','stir.listings.create','stir.listings.update',
    'stir.participants.read','stir.participants.update',
    'stir.negotiations.read','stir.negotiations.create','stir.negotiations.update',
    'stir.agreements.read']

def make_user(admin, tenant, role_id, label, suffix):
    email = f'{label}-{suffix}@stir.test'
    password = secrets.token_urlsafe(32)
    base = f'/api/shell/v1/tenants/{tenant}'
    request(base+'/users','POST',{'email':email,'displayName':label,'authProvider':'local','subject':email,'password':password,'role':'member','enabled':True},admin,expected=(200,201))
    session = request('/api/shell/v1/auth/login','POST',{'email':email,'password':password})
    request(base+'/roles/users/'+session['user']['id'],'PUT',{'roleIds':[role_id]},admin,204)
    return request('/api/shell/v1/auth/login','POST',{'email':email,'password':password})

def setup_tenant(admin, tenant, suffix, labels):
    role = 'stir_marketplace_'+suffix
    created_role = request(f'/api/shell/v1/tenants/{tenant}/roles','POST',{'key':role,'name':'STIR marketplace E2E','description':'Isolated development fixture','enabled':True},admin,expected=(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+created_role['id']+'/permissions','PUT',PERMISSIONS,admin)
    return {label: make_user(admin,tenant,created_role['id'],label,suffix) for label in labels}

def main():
    admin = request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test','password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:10]
    tenant_a = sql(f"select tenant_id from idax_core.tenant_create('stir-mkt-a-{suffix}', 'STIR Marketplace A', 'active', false);")
    tenant_b = sql(f"select tenant_id from idax_core.tenant_create('stir-mkt-b-{suffix}', 'STIR Marketplace B', 'active', false);")
    users_a = setup_tenant(admin,tenant_a,suffix,['ana','pedro','carlos'])
    users_b = setup_tenant(admin,tenant_b,suffix,['bea'])
    ana, pedro, carlos, bea = users_a['ana'], users_a['pedro'], users_a['carlos'], users_b['bea']

    base_a = f'/api/stir/tenants/{tenant_a}'
    request(base_a+'/participants/me','PUT',{'displayName':'Ana '+suffix,'bio':'Grows tomatoes','location':'Girona'},ana['accessToken'])
    request(base_a+'/participants/me','PUT',{'displayName':'Pedro '+suffix,'bio':'Looks for fresh produce','location':'Girona'},pedro['accessToken'])

    listing = request(base_a+'/listings','POST',{'direction':'OFFER','title':'20kg tomatoes '+suffix,'description':'Ripe San Marzano tomatoes','category':'home','resourceKind':'physical','location':'Girona'},ana['accessToken'],201)

    found = request(base_a+f"/listings?q={suffix}",token=pedro['accessToken'])
    assert any(row['id']==listing['id'] and row['ownerDisplayName']=='Ana '+suffix for row in found['content'])

    public_view = request(base_a+'/participants/'+listing['ownerId'],token=pedro['accessToken'])
    assert public_view['displayName']=='Ana '+suffix

    negotiation = request(base_a+f"/listings/{listing['id']}/offers",'POST',{'message':'I would like 15kg','quantity':15,'unitLabel':'kg','proposedAmount':'10.00','terms':'Pickup Saturday'},pedro['accessToken'],201)
    assert negotiation['status']=='OPEN' and len(negotiation['offers'])==1 and negotiation['offers'][0]['authorId']==pedro['user']['id']

    ana_view = request(base_a+f"/negotiations/{negotiation['id']}",token=ana['accessToken'])
    assert ana_view['id']==negotiation['id']

    countered = request(base_a+f"/negotiations/{negotiation['id']}/offers",'POST',{'message':'I can do 20kg for that price','quantity':20,'unitLabel':'kg','proposedAmount':'10.00','terms':'Pickup Saturday morning','expectedVersion':ana_view['version']},ana['accessToken'])
    assert len(countered['offers'])==2 and countered['offers'][0]['status']=='SUPERSEDED' and countered['offers'][1]['authorId']==ana['user']['id']

    pedro_view = request(base_a+f"/negotiations/{negotiation['id']}",token=pedro['accessToken'])
    head = pedro_view['offers'][-1]
    agreement = request(base_a+f"/negotiations/{negotiation['id']}/accept",'POST',{'offerId':head['id'],'expectedVersion':pedro_view['version']},pedro['accessToken'])
    assert agreement['economicPhase']=='AWAITING_ECONOMIC_EXECUTION'
    assert len(agreement['snapshot']['digestSha256'])==64
    assert agreement['listingId']==listing['id']

    closed = request(base_a+f"/negotiations/{negotiation['id']}",token=ana['accessToken'])
    assert closed['status']=='ACCEPTED' and closed['agreementId']==agreement['id']
    assert closed['offers'][-1]['status']=='ACCEPTED'

    ana_agreement = request(base_a+f"/agreements/{agreement['id']}",token=ana['accessToken'])
    pedro_agreement = request(base_a+f"/agreements/{agreement['id']}",token=pedro['accessToken'])
    assert ana_agreement['snapshot']['digestSha256']==pedro_agreement['snapshot']['digestSha256']==agreement['snapshot']['digestSha256']

    request(base_a+'/listings/'+listing['id'],token=carlos['accessToken'])
    request(base_a+f"/negotiations/{negotiation['id']}",token=carlos['accessToken'],expected=404)
    request(base_a+f"/agreements/{agreement['id']}",token=carlos['accessToken'],expected=404)
    request(base_a+f"/negotiations/{negotiation['id']}/decline",'POST',{'expectedVersion':closed['version']},carlos['accessToken'],expected=(400,403,404,409))

    request(base_a+'/listings/'+listing['id'],token=bea['accessToken'],expected=(400,403,404))
    request(base_a+f"/negotiations/{negotiation['id']}",token=bea['accessToken'],expected=(400,403,404))
    request(base_a+f"/agreements/{agreement['id']}",token=bea['accessToken'],expected=(400,403,404))

    print('PASS: Ana/Pedro profile setup, listing publish/discovery, offer, counteroffer, accept, Agreement + AgreementSnapshot, both parties read identical digest.')
    print('PASS: Carlos (same tenant, uninvolved) sees the public Listing but not the negotiation or agreement, and cannot act on it.')
    print('PASS: Tenant B participant cannot see the listing, negotiation or agreement at all.')

if __name__=='__main__':main()
