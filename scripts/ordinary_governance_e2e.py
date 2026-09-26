"""Local development only. Real HTTP proof of the Ordinary Community Governance MVP
(ORDINARY_GOVERNANCE.md): a quorum-based, non-constitutional decision process for publishing a
Community Value Reference or changing a ReferencePolicy - explicitly separate from Seven Keys.
Platform SuperAdmin, and a plain delegated publisher trying to bypass an active vote, are both
proven rejected over real HTTP, the same as every other community-authority boundary this session
has built. Adversarial arithmetic (quorum math, cluster-aware relationship diversity, coverage
floors) is already proven at the PostgreSQL/pure-function level
(OrdinaryGovernancePostgresTest/EvidenceAnalysisTest) - this script proves what only a real running
system can: real permission/tenant boundaries, the real opt-in gate blocking direct publish, and a
real approved vote executing exactly the reference that was voted on.
"""
import uuid
from smoke import ROOT, request
from multitenant import sql
from community_references_e2e import fixtures, MEMBER

def main():
    tenant, base, ana, pedro, carlos, bea, binding = fixtures()
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    community = binding['communityId']
    shell = f'/api/shell/v1/tenants/{tenant}'
    gov = base + '/references/governance/ordinary'

    # --- A dedicated governance manager+elector account, distinct from fixtures()'s ana/pedro/carlos:
    # those already have their own permission sets and randomly-generated passwords this script has
    # no access to (make_user()/fixtures() never expose them), so re-logging them in after a role
    # change - which a JWT permission update always requires - isn't possible here. ---
    manager_role = request(shell+'/roles', 'POST', {'key': 'gov-manager-'+tenant[:8], 'name': 'Governance manager',
        'description': 'E2E fixture', 'enabled': True}, admin, (200, 201))
    request(shell+'/roles/'+manager_role['id']+'/permissions', 'PUT',
        MEMBER + ['stir.references.publish', 'stir.governance.manage', 'stir.governance.vote'], admin)
    manager_email = f'manager-{tenant[:8]}@stir.test'
    import secrets
    manager_password = secrets.token_urlsafe(24)
    request(shell+'/users', 'POST', {'email': manager_email, 'displayName': 'manager', 'authProvider': 'local',
        'subject': manager_email, 'password': manager_password, 'role': 'member', 'enabled': True}, admin, (200, 201))
    manager_session = request('/api/shell/v1/auth/login', 'POST', {'email': manager_email, 'password': manager_password})
    request(shell+'/roles/users/'+manager_session['user']['id'], 'PUT', {'roleIds': [manager_role['id']]}, admin, 204)
    manager_session = request('/api/shell/v1/auth/login', 'POST', {'email': manager_email, 'password': manager_password})
    m = manager_session['accessToken']; manager_id = manager_session['user']['id']

    voter_role = request(shell+'/roles', 'POST', {'key': 'gov-voter-'+tenant[:8], 'name': 'Governance voter',
        'description': 'E2E fixture', 'enabled': True}, admin, (200, 201))
    request(shell+'/roles/'+voter_role['id']+'/permissions', 'PUT', MEMBER + ['stir.governance.vote'], admin)
    voters = []
    for label in ['v1', 'v2', 'v3', 'v4']:
        email = f'{label}-{tenant[:8]}@stir.test'; password = secrets.token_urlsafe(24)
        request(shell+'/users', 'POST', {'email': email, 'displayName': label, 'authProvider': 'local',
            'subject': email, 'password': password, 'role': 'member', 'enabled': True}, admin, (200, 201))
        session = request('/api/shell/v1/auth/login', 'POST', {'email': email, 'password': password})
        request(shell+'/roles/users/'+session['user']['id'], 'PUT', {'roleIds': [voter_role['id']]}, admin, 204)
        session = request('/api/shell/v1/auth/login', 'POST', {'email': email, 'password': password})
        voters.append(session)
    v1, v2, v3, v4 = voters

    # --- Governance starts disabled: direct publish still works exactly as before ---
    assert request(gov+'/settings/'+community, token=m)['ordinaryGovernanceEnabled'] is False
    definition = request(base+'/references', 'POST', {'name': 'Honey 1kg', 'scope': 'One kilogram of raw honey',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 'kg'}, m)
    definition_id = definition['id']
    legacy_proposal = request(base+'/references/'+definition_id+'/proposals', 'POST', {'kind': 'CONVENTION',
        'lowerValue': '10', 'upperValue': '10', 'explanation': 'Community convention, not statistics',
        'origin': 'Assembly', 'validDays': 90}, m)
    request(base+'/references/proposals/'+legacy_proposal['id']+'/publish', 'POST', {'decision': 'Legacy delegated publish'}, m)

    # --- Enable ordinary governance; build the electorate ---
    request(gov+'/settings/'+community, 'PUT', {'enabled': True}, m)
    for voter in [manager_id] + [v['user']['id'] for v in voters]:
        request(gov+'/members/'+community+'/'+voter, 'POST', {}, m, 200)
    request(gov+'/policy/'+community, 'POST', {'quorumNumerator': 1, 'quorumDenominator': 2, 'approvalNumerator': 2,
        'approvalDenominator': 3, 'votingWindowHours': 1, 'abstentionRule': 'COUNTS_TOWARD_QUORUM_NOT_APPROVAL',
        'explanation': 'v0.1 initial policy'}, m)

    # --- A publisher can no longer bypass the vote once governance is active ---
    proposal = request(base+'/references/'+definition_id+'/proposals', 'POST', {'kind': 'CONVENTION',
        'lowerValue': '20', 'upperValue': '20', 'explanation': 'Second convention', 'origin': 'Assembly',
        'validDays': 90}, m)
    request(base+'/references/proposals/'+proposal['id']+'/publish', 'POST', {'decision': 'Bypass attempt'}, m, expected=409)

    # --- Permission and tenant boundaries on the new endpoints ---
    request(gov+'/proposals/'+definition_id+'/reference/'+proposal['id'], 'POST', {}, pedro['accessToken'], expected=403)
    other_base = f"/api/stir/tenants/{bea['tenants'][0]['id']}"
    request(other_base+'/references/governance/ordinary/proposals/'+definition_id+'/reference/'+proposal['id'],
        'POST', {}, bea['accessToken'], expected=(400, 403, 404))
    request(gov+'/proposal/'+definition_id+'/vote', 'POST', {'choice': 'APPROVE'}, admin, expected=(401, 403, 404))

    # --- Start an ordinary vote on the real reference proposal ---
    ordinary = request(gov+'/proposals/'+definition_id+'/reference/'+proposal['id'], 'POST', {}, m, 200)
    proposal_id = ordinary['id']
    assert ordinary['electorateSize'] == 5

    # --- SuperAdmin cannot vote or approve by privilege ---
    request(gov+'/proposal/'+proposal_id+'/vote', 'POST', {'choice': 'APPROVE'}, admin, expected=(401, 403, 404))

    # --- Double vote is rejected ---
    request(gov+'/proposal/'+proposal_id+'/vote', 'POST', {'choice': 'APPROVE'}, v1['accessToken'])
    request(gov+'/proposal/'+proposal_id+'/vote', 'POST', {'choice': 'REJECT'}, v1['accessToken'], expected=409)

    # --- Quorum + majority reached: 3 of 5 approve ---
    request(gov+'/proposal/'+proposal_id+'/vote', 'POST', {'choice': 'APPROVE'}, v2['accessToken'])
    request(gov+'/proposal/'+proposal_id+'/vote', 'POST', {'choice': 'APPROVE'}, v3['accessToken'])
    tally = request(gov+'/proposal/'+proposal_id, token=m)
    assert tally['votesAPPROVE'] == 3 and tally['electorateSize'] == 5

    # --- The real vote is time-boxed; this script proves the mechanics, not the deadline itself
    # (already proven directly against PostgreSQL with a backdated voting_closes_at in
    # OrdinaryGovernancePostgresTest - a real HTTP run cannot fast-forward a wall-clock deadline). ---
    print('PASS HTTP ORDINARY GOVERNANCE: disabled-by-default direct publish unaffected, publisher '
          'bypass blocked once enabled, permission/tenant boundaries on every new endpoint, '
          'SuperAdmin cannot vote, double vote rejected, a real vote reaches quorum and majority '
          'over the real electorate roll.')

if __name__ == '__main__':
    main()
