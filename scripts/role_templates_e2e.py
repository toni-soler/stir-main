"""Real HTTP proof of module role templates (idax-shell docs/MODULE_ROLE_TEMPLATES.md) for STIR.

STIR declares its roles in deploy/extensions.json. Nobody configures a role by hand here:

1. A workspace created from the platform API starts with STIR's template roles, and its first
   administrator is a STIR participant.
2. A member the administration registers joins as a STIR participant: they can complete their
   profile, publish, and activate economic exchange in osTRIS, with no role set up for them.
3. A disabled template role is given to nobody; the member it would have covered cannot publish.
4. A member who predates the templates (no roles) gets the participant role when the
   administration applies the templates to current members; others are left alone.
5. A template role keeps its link when renamed (no duplicate appears), cannot be deleted, and gets
   back a permission removed from it while keeping one an administrator added.

Isolated synthetic workspace, made-up people, STIR_TEST_URL (smoke.BASE). Exit code 1 on failure.
"""
import secrets
import uuid

from economic_exchange_e2e import activate_economic
from smoke import ROOT, request

SHELL = '/api/shell/v1'
RESULTS = []


def check(name, condition=True):
    RESULTS.append(name)
    if not condition:
        raise AssertionError(name)
    print('PASS: ' + name, flush=True)


def login(email, password):
    return request(SHELL + '/auth/login', 'POST', {'email': email, 'password': password})


def main():
    admin = login('admin@stir.test', (ROOT / '.local/secrets/login_password').read_text().strip())['accessToken']
    suffix = uuid.uuid4().hex[:8]
    owner_password = secrets.token_urlsafe(18)
    workspace = request(SHELL + '/platform/tenants', 'POST', {
        'tenantCode': 'roles-' + suffix, 'tenantName': 'Role templates ' + suffix,
        'adminEmail': f'owner-{suffix}@stir.test', 'adminDisplayName': 'Owner ' + suffix, 'adminPassword': owner_password}, admin, 201)
    tenant = workspace['id']
    t = f'{SHELL}/tenants/{tenant}'
    stir = f'/api/stir/tenants/{tenant}'

    templates = {item['key']: item for item in request(t + '/roles/templates', token=admin) if item['extensionId'] == 'stir'}
    check('the new workspace has every STIR template role', set(templates) == {'participant', 'case-reviewer', 'moderator', 'reference-publisher', 'electorate'}
          and all(item['roleId'] and item['roleEnabled'] for item in templates.values()))
    check('every STIR template permission is known to the Core catalog', all(not item['unavailablePermissions'] for item in templates.values()))
    participant = templates['participant']['roleId']
    check('only the participant template is a default for new members',
          [key for key, item in templates.items() if item['defaultForNewMembers']] == ['participant'])
    roles = {role['id']: role for role in request(t + '/roles', token=admin)}
    check('each template role records its origin', roles[participant]['sourceType'] == 'EXTENSION_TEMPLATE' and roles[participant]['sourceKey'] == 'stir:participant')

    def role_ids(user_id):
        return set(next(row for row in request(t + '/roles/users', token=admin) if row['userId'] == user_id)['roleIds'])

    owner = login(f'owner-{suffix}@stir.test', owner_password)
    check('the first administrator is a STIR participant', participant in role_ids(owner['user']['id']))

    def new_member(label):
        email, password = f'{label}-{suffix}@stir.test', secrets.token_urlsafe(18)
        created = request(t + '/users', 'POST', {'email': email, 'subject': email, 'displayName': label.title(), 'authProvider': 'local',
                                                 'password': password, 'role': 'member', 'enabled': True}, admin, (200, 201))
        return created['userId'], login(email, password)

    # The administration also gives itself the economic-administration permission it needs to set up
    # the community unit: that is a deliberate administrative grant, not a participant one.
    economic = request(t + '/roles', 'POST', {'key': 'economic_admin', 'name': 'Economic administration', 'enabled': True}, admin, (200, 201))
    request(t + f"/roles/{economic['id']}/permissions", 'PUT', ['OSTRIS_COMMUNITY_MANAGE'], admin)
    request(t + f"/roles/users/{owner['user']['id']}", 'PUT', {'roleIds': sorted(role_ids(owner['user']['id']) | {economic['id']})}, admin, 204)
    owner = login(f'owner-{suffix}@stir.test', owner_password)
    request(stir + '/participants/me', 'PUT', {'displayName': 'Owner ' + suffix}, owner['accessToken'])
    request(stir + '/economic/marketplace/bootstrap', 'POST', {'communityName': 'Roles ' + suffix, 'unitCode': 'CRD', 'unitScale': 0}, owner['accessToken'])

    ana_id, ana = new_member('ana')
    check('a registered member joins with the participant role and nothing else', role_ids(ana_id) == {participant})
    request(stir + '/participants/me', 'PUT', {'displayName': 'Ana ' + suffix}, ana['accessToken'])
    listing = request(stir + '/listings', 'POST', {'direction': 'OFFER', 'title': 'Seeds ' + suffix, 'description': 'Tomato seeds',
                                                   'category': 'general', 'resourceKind': 'physical'}, ana['accessToken'], 201)
    activate_economic(stir, ana)
    me = request(stir + '/economic/me', token=ana['accessToken'])
    check('with no role set up by hand, the member completes their profile, publishes and activates exchange in osTRIS',
          listing['id'] and me.get('balanceProjection') == '0')

    disabled = dict(roles[participant], enabled=False)
    request(t + f'/roles/{participant}', 'PUT', {k: disabled[k] for k in ('key', 'name', 'description', 'enabled')}, admin)
    bea_id, bea = new_member('bea')
    check('a disabled template role is given to nobody', role_ids(bea_id) == set())
    request(stir + '/listings', 'POST', {'direction': 'OFFER', 'title': 'x', 'description': 'x', 'category': 'general',
                                         'resourceKind': 'physical'}, bea['accessToken'], 403)
    check('and the member it would have covered cannot publish')
    request(t + f'/roles/{participant}', 'PUT', {'key': roles[participant]['key'], 'name': 'Participantes de la huerta', 'enabled': True}, admin)
    check('renaming and re-enabling it keeps its link to the template',
          {r['id']: r for r in request(t + '/roles', token=admin)}[participant]['sourceKey'] == 'stir:participant')

    result = request(t + '/roles/templates/apply', 'POST', {'assignToExistingMembers': True}, admin)
    check('applying the templates to current members gives the participant role to the one who lacked it',
          result['membersAssigned'] == 1 and participant in role_ids(bea_id) and role_ids(ana_id) == {participant})
    check('no duplicate template role appears after the rename',
          sum(1 for r in request(t + '/roles', token=admin) if r.get('sourceKey') == 'stir:participant') == 1)
    request(stir + '/participants/me', 'PUT', {'displayName': 'Bea ' + suffix}, bea['accessToken'])
    request(stir + '/listings', 'POST', {'direction': 'WANTED', 'title': 'Help ' + suffix, 'description': 'Help in the garden',
                                         'category': 'general', 'resourceKind': 'service'}, bea['accessToken'], 201)
    check('and that member can now use STIR, with the session they already had')

    request(t + f'/roles/{participant}', 'DELETE', None, admin, 409)
    check('a template role cannot be deleted', participant in {r['id'] for r in request(t + '/roles', token=admin)})

    current = set(request(t + f'/roles/{participant}/permissions', token=admin))
    edited = sorted((current - {'stir.content.report'}) | {'stir.moderation.manage'})
    request(t + f'/roles/{participant}/permissions', 'PUT', edited, admin)
    request(t + '/roles/templates/apply', 'POST', {'assignToExistingMembers': False}, admin)
    after = set(request(t + f'/roles/{participant}/permissions', token=admin))
    check('reconciling gives back a template permission that was removed, and keeps one an administrator added',
          'stir.content.report' in after and 'stir.moderation.manage' in after)

    print(f'ROLE TEMPLATES E2E: PASS ({len(RESULTS)} checks)')



if __name__ == '__main__':
    main()
