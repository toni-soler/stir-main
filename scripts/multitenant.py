"""Isolated development fixtures: public Core tenant capability + real Shell user/role APIs.
No economic fixtures. Never invoke against a production deployment.

INT-P1-002 harness fix: compose.yml hardcodes `name: stir-dev`, so any bare `docker compose`
invocation from this directory - which is exactly what this module's sql() did before this fix -
silently resolves against the live stir-dev project whenever the caller hasn't separately
exported COMPOSE_PROJECT_NAME (e.g. via a .env file). That is how a routing bug in this exact
function wrote ~30 test tenants into the real stir-dev database (see stir-doc/INT_P1_002.md).
There is now no implicit/silent path: the target Compose project must be named explicitly via the
STIR_COMPOSE_PROJECT environment variable, "stir-dev" is refused outright regardless of what the
caller passes, and the container this would actually operate on is inspected and its compose
project label verified to match before any command runs.
"""
import json
import os
import secrets
import subprocess
import uuid
from smoke import ROOT, request


class ComposeSafetyError(RuntimeError):
    """Raised when the target Compose project for these fixture helpers is missing, is
    stir-dev, or does not match the container that would actually be operated on."""


def compose_project():
    """The isolated Compose project these fixture helpers must target. No default: an unset
    STIR_COMPOSE_PROJECT is a hard error, not a silent fall-through to whatever `docker compose`
    would resolve on its own (which is stir-dev, per compose.yml's hardcoded `name:`)."""
    project = os.environ.get('STIR_COMPOSE_PROJECT', '').strip()
    if not project:
        raise ComposeSafetyError(
            'STIR_COMPOSE_PROJECT is not set. Every docker compose invocation made by these E2E '
            'fixture helpers (multitenant.py:sql(), and any sibling that shells out to docker '
            'compose, e.g. backup_restore_e2e.py) must target an explicit, isolated project - '
            'there is no safe default. compose.yml hardcodes `name: stir-dev`, so a bare '
            '"docker compose ..." invocation without -p silently resolves against the live '
            'stir-dev project. Set STIR_COMPOSE_PROJECT=<your isolated project name> (e.g. '
            'stir-integration-gate-2) before running any script that imports this module.'
        )
    if project == 'stir-dev':
        raise ComposeSafetyError(
            'STIR_COMPOSE_PROJECT is set to "stir-dev". These fixture helpers create and delete '
            'throwaway test data - tenants, users, roles, and in some scripts entire Docker '
            'volumes - and must never target stir-dev, under any circumstance, even if that is '
            'genuinely the caller\'s intent. Use an isolated project.'
        )
    # Side effect, deliberate: docker compose natively honors COMPOSE_PROJECT_NAME from the
    # environment. Exporting it here means every subsequent `docker compose ...` call in this
    # process AND in any subprocess it spawns (e.g. backup_restore_e2e.py shelling out to
    # backup.py/restore.py, neither of which know about STIR_COMPOSE_PROJECT) inherits the same
    # safe routing automatically, without needing every one of those scripts individually
    # rewritten. subprocess.run() inherits the parent environment by default.
    os.environ['COMPOSE_PROJECT_NAME'] = project
    return project


def verify_isolated_container(project, service='postgres'):
    """Fail closed, before any mutating operation: resolve the real container docker compose
    would operate on for `service` in `project`, and confirm its actual
    com.docker.compose.project label matches `project` exactly (and is not stir-dev). Returns
    the verified container id. Raises ComposeSafetyError on any mismatch, absence, or ambiguity -
    never guesses, never proceeds on a partial match."""
    ps = subprocess.run(
        ['docker', 'compose', '-p', project, 'ps', '-q', service],
        cwd=ROOT, capture_output=True, text=True)
    container_id = ps.stdout.strip()
    if not container_id or '\n' in container_id:
        raise ComposeSafetyError(
            f'Expected exactly one running "{service}" container for compose project '
            f'"{project}", found {container_id.count(chr(10)) + (1 if container_id else 0)}. '
            f'Refusing to proceed - the isolated stack may not be up, or the project name is '
            f'ambiguous. stderr: {ps.stderr.strip()[:300]}'
        )
    inspect = subprocess.run(
        ['docker', 'inspect', container_id, '--format',
         '{{index .Config.Labels "com.docker.compose.project"}}'],
        capture_output=True, text=True)
    actual_project = inspect.stdout.strip()
    if actual_project != project:
        raise ComposeSafetyError(
            f'Container {container_id} (resolved for service "{service}" under compose '
            f'project "{project}") actually carries compose project label "{actual_project}". '
            f'Refusing to proceed - this would have operated on the wrong stack.'
        )
    if actual_project == 'stir-dev':
        raise ComposeSafetyError(
            f'Container {container_id} belongs to project "stir-dev" - refusing to proceed '
            f'under any circumstance, regardless of what was requested.'
        )
    return container_id


def sql(statement, runtime=False):
    project = compose_project()
    verify_isolated_container(project, 'postgres')
    args = ['docker', 'compose', '-p', project, 'exec', '-T']
    if runtime: args += ['-e', 'PGPASSWORD=' + (ROOT / '.local/secrets/runtime_password').read_text().strip()]
    args += ['postgres', 'psql', '-X', '-At', '-v', 'ON_ERROR_STOP=1', '-U', 'idax_backend' if runtime else 'postgres', '-d', 'idax']
    if runtime: args += ['-h', 'postgres']
    result = subprocess.run(args, input=statement, text=True, cwd=ROOT, capture_output=True)
    if result.returncode: raise AssertionError('SQL fixture/proof failed: ' + result.stderr[:400])
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
