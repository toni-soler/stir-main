"""Local development only. Community authority matrix over real HTTP against the real idax_core schema.

Accepted authority model (ReferenceService.requireCommunityAuthority):
- Ordinary Governance disabled: active tenant owner/admin of the target tenant is the bootstrap authority.
  user, readonly, service, SuperAdmin without tenant membership, cross-tenant and cross-community get none.
- Ordinary Governance enabled: active stir.community_governance_member only. No fallback to tenant owner/admin.
Platform permission only gates access; it never confers community authority. Each actor below holds the
publish permission, so every denial is an authority denial, not a permission denial.
"""
import uuid
from smoke import ROOT, request
from multitenant import sql
from economic_exchange_e2e import make_user
from community_references_e2e import fixtures, MEMBER

PUBLISH = MEMBER + ['stir.references.publish']
MANAGE = PUBLISH + ['stir.governance.manage', 'stir.governance.vote']


def role(admin, tenant, key, permissions, suffix):
    base = f'/api/shell/v1/tenants/{tenant}/roles'
    row = request(base, 'POST', {'key': key + suffix, 'name': key, 'description': 'Community authority E2E', 'enabled': True}, admin, (200, 201))
    request(base + '/' + row['id'] + '/permissions', 'PUT', permissions, admin)
    return row['id']


def set_tenant_role(tenant, user, value):
    sql(f"update idax_core.tenant_user set role='{value}' where tenant_id='{tenant}' and user_id='{user['user']['id']}';")


def expect_publish(name, base, token, status):
    got = request(base + '/references', 'POST', {'name': 'Authority ' + uuid.uuid4().hex[:6], 'scope': 'Authority matrix',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 'unit'}, token, status)
    print(f'PASS: {name} -> {status}')
    return got


def main():
    tenant, base, ana, pedro, carlos, bea, binding = fixtures()
    admin_token = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    admin = admin_token
    community = binding['communityId']
    other_tenant = bea['tenants'][0]['id']
    shell = f'/api/shell/v1/tenants/{tenant}'
    gov = base + '/references/governance/ordinary'
    suffix = uuid.uuid4().hex[:6]
    rid = role(admin, tenant, 'authority-publisher', PUBLISH, suffix)

    owner = make_user(admin, tenant, rid, 'owner', suffix)
    readonly = make_user(admin, tenant, rid, 'readonly', suffix)
    service = make_user(admin, tenant, rid, 'service', suffix)
    plain = make_user(admin, tenant, rid, 'plain', suffix)
    set_tenant_role(tenant, owner, 'owner')
    set_tenant_role(tenant, readonly, 'readonly')
    set_tenant_role(tenant, service, 'service')
    set_tenant_role(tenant, plain, 'user')
    # Manager holds governance permissions. It is tenant admin only for the disabled-phase bootstrap steps.
    manager = make_user(admin, tenant, role(admin, tenant, 'authority-manager', MANAGE, suffix), 'manager', suffix)
    set_tenant_role(tenant, manager, 'admin')
    print('PASS: fixture roles provisioned (owner, admin, user, readonly, service, governance manager)')

    # ----- Ordinary Governance disabled: bootstrap authority = tenant owner/admin -----
    assert request(gov + '/settings/' + community, token=ana['accessToken'])['ordinaryGovernanceEnabled'] is False
    expect_publish('governance disabled: tenant owner allowed', base, owner['accessToken'], 200)
    expect_publish('governance disabled: tenant admin allowed', base, ana['accessToken'], 200)
    expect_publish('governance disabled: tenant user denied (platform permission not authority)', base, plain['accessToken'], 403)
    expect_publish('governance disabled: readonly denied', base, readonly['accessToken'], 403)
    expect_publish('governance disabled: service denied', base, service['accessToken'], 403)
    expect_publish('governance disabled: SuperAdmin without tenant membership denied', base, admin, 403)
    expect_publish('cross-tenant: tenant A admin on tenant B path denied', f'/api/stir/tenants/{other_tenant}',
                   ana['accessToken'], 403)
    expect_publish('cross-tenant: tenant B member on tenant A path denied', base, bea['accessToken'], 403)
    foreign_community = str(uuid.uuid4())
    request(gov + '/settings/' + foreign_community, 'PUT', {'enabled': False}, manager['accessToken'], 403)
    print('PASS: cross-community: admin with manage permission cannot govern an unbound community -> 403')

    # ----- Ordinary Governance enabled: active governance member only, no fallback -----
    request(gov + '/members/' + community + '/' + manager['user']['id'], 'POST', {}, manager['accessToken'], 200)
    request(gov + '/settings/' + community, 'PUT', {'enabled': True}, manager['accessToken'], 200)
    # Demote the manager: enabled-mode authority must come from roster membership alone, not tenant role.
    set_tenant_role(tenant, manager, 'user')
    assert request(gov + '/settings/' + community, token=ana['accessToken'])['ordinaryGovernanceEnabled'] is True

    expect_publish('governance enabled: active eligible member (tenant role user) allowed', base, manager['accessToken'], 200)
    expect_publish('governance enabled: tenant owner not in roster denied', base, owner['accessToken'], 403)
    expect_publish('governance enabled: tenant admin not in roster denied (no fallback)', base, ana['accessToken'], 403)
    expect_publish('governance enabled: SuperAdmin without roster membership denied', base, admin, 403)
    request(gov + '/settings/' + foreign_community, 'PUT', {'enabled': False}, manager['accessToken'], 403)
    print('PASS: governance enabled: cross-community with an eligible member -> 403')

    stored = sql(f"select count(*) from stir.reference_definition where tenant_id='{tenant}' and name like 'Authority %';")
    print(f'INFO: authority-created definitions in tenant: {stored}')
    print('COMMUNITY AUTHORITY E2E: PASS')


if __name__ == '__main__':
    main()
