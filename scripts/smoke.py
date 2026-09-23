"""Real HTTP listing smoke test against the isolated local development instance."""
from pathlib import Path
import json
import os
import urllib.request
import urllib.error
import uuid

ROOT=Path(__file__).resolve().parents[1]
BASE=os.environ.get('STIR_TEST_URL','http://localhost:8089')

def request(path,method='GET',body=None,token=None,expected=200,extra_headers=None):
    headers={'Content-Type':'application/json'}
    headers.update(extra_headers or {})
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(BASE+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=30) as response:status=response.status;data=response.read()
    except urllib.error.HTTPError as error: status=error.code;data=error.read()
    allowed=expected if isinstance(expected,tuple) else (expected,)
    if status not in allowed:raise AssertionError(f'{method} {path}: expected {expected}, got {status}; {data[:300]!r}')
    return json.loads(data) if data else None

def main():
    login_email=os.environ.get('STIR_TEST_LOGIN_EMAIL','admin@stir.test')
    request('/actuator/health/readiness')
    session=request('/api/shell/v1/auth/login','POST',{'email':login_email,'password':(ROOT/'.local/secrets/login_password').read_text().strip()})
    token=session['accessToken'];tenant=session['tenants'][0]['id'];base=f'/api/stir/tenants/{tenant}/listings'
    request(base,expected=401)
    request(base,token='invalid',expected=401)
    parts=token.split('.')
    parts[2]=('A' if parts[2][0]!='A' else 'B')+parts[2][1:]
    request(base,token='.'.join(parts),expected=401)
    request(base+'/catalogs',token=token)
    payload={'direction':'OFFER','title':'Smoke '+str(uuid.uuid4()),'description':'Reusable chair','category':'home','resourceKind':'physical','location':'Town centre'}
    listing=request(base,'POST',payload,token,201)
    assert listing['tenantId']==tenant and listing['ownerId']==session['user']['id']
    found=request(base+'?mine=true&direction=OFFER&category=home',token=token)
    assert any(row['id']==listing['id'] for row in found['content'])
    request(base+'/'+listing['id'],token=token)
    changed=request(base+'/'+listing['id'],'PUT',{**payload,'title':'Updated smoke','version':listing['version']},token)
    request(base+'/'+listing['id'],'PUT',{**payload,'version':listing['version']},token,409)
    closed=request(base+'/'+listing['id']+'/close','POST',{'version':changed['version']},token)
    assert closed['status']=='CLOSED'
    repeated=request(base+'/'+listing['id']+'/close','POST',{'version':closed['version']},token)
    assert repeated['version']==closed['version'] and repeated['status']=='CLOSED'
    request(base+'/'+listing['id'],'DELETE',token=token,expected=405)
    request(base+'/'+listing['id'],'PUT',{**payload,'version':closed['version']},token,409)
    request(base+'?size=1000',token=token,expected=400)
    print('PASS: login, tenant, auth 401, catalogs, create/read/filter/edit, stale update 409, close, closed edit 409, bounded pagination.')
    print('Created one closed smoke listing in the local development database.')

if __name__=='__main__':main()
