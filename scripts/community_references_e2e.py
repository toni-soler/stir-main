"""Local development only. Real HTTP reference governance, two-party agreement and tenant privacy.
No osTRIS table writes; community/unit provisioning uses the existing public binding flow.
"""
import hashlib
import json
import uuid
from smoke import ROOT, request
from multitenant import sql
from economic_exchange_e2e import PERMISSIONS as ECONOMIC, make_user

MEMBER = ECONOMIC + ['stir.references.read', 'stir.references.propose']

def fixtures():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email':'admin@stir.test',
        'password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix=uuid.uuid4().hex[:8]
    tenant=sql(f"select tenant_id from idax_core.tenant_create('stir-ref-{suffix}','STIR References {suffix}','active',false);")
    other=sql(f"select tenant_id from idax_core.tenant_create('stir-ref-b-{suffix}','STIR Reference B','active',false);")
    def role(t,key,permissions):
        base=f'/api/shell/v1/tenants/{t}/roles'
        row=request(base,'POST',{'key':key+suffix,'name':key,'description':'Local reference test','enabled':True},admin,(200,201))
        request(base+'/'+row['id']+'/permissions','PUT',permissions,admin)
        return row['id']
    member=role(tenant,'member',MEMBER)
    publisher=role(tenant,'publisher',MEMBER+['stir.references.publish'])
    b_role=role(other,'other',MEMBER)
    ana=make_user(admin,tenant,publisher,'ana',suffix)
    pedro=make_user(admin,tenant,member,'pedro',suffix)
    carlos=make_user(admin,tenant,member,'carlos',suffix)
    bea=make_user(admin,other,b_role,'bea',suffix)
    base=f'/api/stir/tenants/{tenant}'
    binding=request(base+'/economic/marketplace/bootstrap','POST',{'communityName':'References '+suffix,'unitCode':'REF','unitScale':2},ana['accessToken'])
    return tenant,base,ana,pedro,carlos,bea,binding

def main():
    tenant,base,ana,pedro,carlos,bea,binding=fixtures()
    a,p=ana['accessToken'],pedro['accessToken']
    definition=request(base+'/references','POST',{'name':'Bread 500g','scope':'One plain 500g loaf',
        'attributes':{'weight':'500g'},'quantityBasis':'1','quantityUnit':'loaf'},a)
    ref=base+'/references/'+definition['id']
    assert definition['unit_id']==binding['unitId']
    evidence=request(ref,token=p)['evidence']
    assert evidence['status']=='INSUFFICIENT_DATA' and evidence['median'] is None
    def proposal(amount):
        return request(ref+'/proposals','POST',{'kind':'CONVENTION','lowerValue':amount,'upperValue':amount,
            'explanation':'Assembly convention, not an observed price','origin':'Community assembly','validDays':90},p)
    first=proposal('10')
    request(base+'/references/proposals/'+first['id']+'/publish','POST',{'decision':'Unauthorized'},p,403)
    v1=request(base+'/references/proposals/'+first['id']+'/publish','POST',{'decision':'Initial assembly decision'},a)
    assert v1['version']==1
    listing=request(base+'/listings','POST',{'direction':'OFFER','title':'Bread with a reference',
        'description':'Test loaf','category':'home','resourceKind':'physical','referenceDefinitionId':definition['id']},a,201)
    assert request(base+'/listings/'+listing['id'],token=p)['referenceDefinitionId']==definition['id']
    def agree(amount,consent=True):
        n=request(base+'/listings/'+listing['id']+'/offers','POST',{'message':'Free proposal',
            'quantity':'1','unitLabel':'loaf','proposedAmount':amount,'proposedUnitRef':definition['unit_ref'],
            'shareReferenceObservation':consent},p,201)
        return request(base+'/negotiations/'+n['id']+'/accept','POST',{'offerId':n['offers'][-1]['id'],
            'expectedVersion':n['version'],'shareReferenceObservation':consent},a)
    agreements=[agree(amount) for amount in ['1','10','100']]
    historic=agreements[0]
    context_url=base+'/references/agreements/'+historic['id']+'/context'
    context=request(context_url,token=p)
    data=json.loads(context['canonicalJson'])
    assert data['reference']['version']==1 and data['contractual'] is False
    assert data['contractualDigestSha256']==historic['snapshot']['digestSha256']
    assert hashlib.sha256(context['canonicalJson'].encode()).hexdigest()==context['digestSha256']
    assert sql(f"select count(*) from stir.reference_observation where tenant_id='{tenant}' and source='AGREEMENT' and aggregate_consent;")=='3'
    assert request(ref,token=p)['evidence']==evidence, 'Daily cut must resist live differencing'
    second=proposal('25')
    request(base+'/references/proposals/'+second['id']+'/publish','POST',{'decision':'Deliberate revised convention'},a)
    assert request(ref,token=p)['reference']['version']==2
    assert len(request(ref+'/history',token=p))==2
    assert request(context_url,token=a)==context
    reloaded=request(base+'/agreements/'+historic['id'],token=p)['snapshot']
    assert all(reloaded[k]==historic['snapshot'][k] for k in ['canonicalJson','digestSha256','schemaVersion'])
    no=agree('30',False)
    assert sql(f"select aggregate_consent from stir.reference_observation where tenant_id='{tenant}' and source_id='{no['id']}' and source='AGREEMENT';")=='f'
    request(context_url,token=carlos['accessToken'],expected=404)
    request(base+'/agreements/'+historic['id'],token=carlos['accessToken'],expected=404)
    request(ref,token=bea['accessToken'],expected=(400,403,404))
    request(context_url,token=bea['accessToken'],expected=(400,403,404))
    other_base=f"/api/stir/tenants/{bea['tenants'][0]['id']}"
    request(other_base+'/references/'+definition['id'],token=bea['accessToken'],expected=404)
    request(other_base+'/references/agreements/'+historic['id']+'/context',token=bea['accessToken'],expected=404)
    request(ref,expected=401)
    print('PASS HTTP: versions, convention with insufficient evidence, below/within/above agreements, bilateral opt-in, immutable historical context and hashes, permissions, tenant A/B, daily reconstruction.')

if __name__=='__main__': main()
