"""Isolated development fixtures: public Core tenant capability + real Shell user/role APIs.
No economic fixtures. Never invoke against a production deployment.
"""
import json
import secrets
import subprocess
import uuid
from smoke import ROOT, request

def sql(statement,runtime=False):
    args=['docker','compose','exec','-T']
    if runtime:args+=['-e','PGPASSWORD='+(ROOT/'.local/secrets/runtime_password').read_text().strip()]
    args+=['postgres','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','idax_backend' if runtime else 'postgres','-d','idax']
    if runtime:args+=['-h','postgres']
    result=subprocess.run(args,input=statement,text=True,cwd=ROOT,capture_output=True)
    if result.returncode:raise AssertionError('SQL fixture/proof failed: '+result.stderr[:400])
    return result.stdout.strip()

def main():
    admin=request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test','password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix=uuid.uuid4().hex[:10]
    tenants=[sql(f"select tenant_id from idax_core.tenant_create('stir-e2e-{name}-{suffix}', 'STIR E2E {name}', 'active', false);") for name in ['a','b']]
    sessions=[]; listings=[]
    for i,tenant in enumerate(tenants):
        base=f'/api/shell/v1/tenants/{tenant}'
        role='stir_e2e_'+suffix
        created_role=request(base+'/roles','POST',{'key':role,'name':'STIR E2E participant','description':'Isolated development fixture','enabled':True},admin,expected=(200,201))
        request(base+'/roles/'+created_role['id']+'/permissions','PUT',['stir.listings.read','stir.listings.create','stir.listings.update'],admin)
        email=f'participant-{i}-{suffix}@stir.test';password=secrets.token_urlsafe(32)
        request(base+'/users','POST',{'email':email,'displayName':'STIR E2E participant','authProvider':'local','subject':email,'password':password,'role':'member','enabled':True},admin,expected=(200,201))
        session=request('/api/shell/v1/auth/login','POST',{'email':email,'password':password})
        request(base+'/roles/users/'+session['user']['id'],'PUT',{'roleIds':[created_role['id']]},admin,204)
        session=request('/api/shell/v1/auth/login','POST',{'email':email,'password':password});sessions.append(session)
        path=f'/api/stir/tenants/{tenant}/listings'
        payload={'direction':'OFFER' if i==0 else 'WANTED','title':'Isolation '+suffix,'description':'Test resource','category':'general','resourceKind':'service'}
        listing=request(path,'POST',payload,session['accessToken'],201,extra_headers={'X-Owner-Id':str(uuid.uuid4())})
        assert listing['ownerId']==session['user']['id'] and listing['tenantId']==tenant
        listings.append(listing)
    for i in range(2):
        other=1-i;token=sessions[i]['accessToken'];base=f'/api/stir/tenants/{tenants[i]}/listings';foreign=f'/api/stir/tenants/{tenants[other]}/listings'
        request(base+'/'+listings[i]['id'],token=token)
        own_payload={key:listings[i][key] for key in ['direction','title','description','category','resourceKind','version']}
        listings[i]=request(base+'/'+listings[i]['id'],'PUT',{**own_payload,'description':'Updated by its ordinary owner'},token)
        assert listings[i]['version']==own_payload['version']+1
        for query in ['', '?q='+suffix, '?category=general&resourceKind=service&status=ACTIVE&q='+suffix]:
            rows=request(base+query,token=token)['content']
            assert len(rows)==1 and rows[0]['id']==listings[i]['id']
        assert request(base+'?q='+listings[other]['id'],token=token)['content']==[]
        request(base+'/'+listings[other]['id'],token=token,expected=404)
        payload={'direction':'OFFER','title':'Intrusion','description':'Denied','category':'general','resourceKind':'service','version':0}
        request(base+'/'+listings[other]['id'],'PUT',payload,token,404)
        request(base+'/'+listings[other]['id']+'/close','POST',{'version':0},token,404)
        request(foreign,token=token,expected=(400,403,404))
        request(foreign+'/'+listings[other]['id'],'PUT',payload,token,expected=(400,403,404))
        request(foreign+'/'+listings[other]['id']+'/close','POST',{'version':0},token,expected=(400,403,404))
        request(base,token=token,expected=(400,403),extra_headers={'X-Tenant':tenants[other]})
        request(base,'POST',{**payload,'ownerId':sessions[other]['user']['id']},token,400)
    flags=sql("select current_user,rolsuper,rolbypassrls,has_schema_privilege(current_user,'stir','CREATE'),has_database_privilege(current_user,'idax','CREATE') from pg_roles where rolname=current_user;",runtime=True)
    assert flags=='idax_backend|f|f|f|f',flags
    print('Runtime identity:',flags)
    memberships=sql("select r.rolname from pg_auth_members m join pg_roles r on r.oid=m.roleid join pg_roles u on u.oid=m.member where u.rolname=current_user order by r.rolname;",runtime=True)
    assert {'idax_app','idax_admin'}<=set(memberships.splitlines()),memberships
    print('Granted roles:',memberships.replace('\n',', '))
    table=sql("select relname,relrowsecurity,relforcerowsecurity from pg_class where oid='stir.listing'::regclass;",runtime=True)
    assert table=='listing|t|t',table
    print('RLS enabled/forced:',table)
    print('Policies:',sql("select policyname,roles,cmd,qual,with_check from pg_policies where schemaname='stir' order by tablename,policyname;",runtime=True))
    runtime_sessions=sql("select distinct usename from pg_stat_activity where datname='idax' and backend_type='client backend' and application_name='PostgreSQL JDBC Driver';")
    assert runtime_sessions=='idax_backend',runtime_sessions
    print('Live JDBC session identities:',runtime_sessions)
    count=sql('select count(*) from stir.listing;',runtime=True);assert count=='0',count
    for role in ['idax_app','idax_admin']:
        output=sql(f"begin; set local role {role}; select set_config('app.tenant_id','{tenants[0]}',true); select count(*) from stir.listing where tenant_id='{tenants[1]}'; select count(*) from stir.listing where tenant_id='{tenants[0]}'; rollback;",runtime=True)
        assert output.splitlines()[-3:-1]==['0','1'],output
    (ROOT/'.local/e2e-fixtures.json').write_text(json.dumps({'tenants':tenants,'listingIds':[r['id'] for r in listings]}),encoding='utf-8')
    print('PASS: A/B ordinary owner read/update; foreign UUID read/update/close 404; foreign path denied; cross-tenant query isolated; contradictory X-Tenant denied; owner forgery rejected.')
    print('PASS: live idax_backend login NOSUPERUSER/NOBYPASSRLS/no schema or database CREATE; no tenant sees zero rows; idax_app and idax_admin both isolate A/B.')

if __name__=='__main__':main()
