"""Local development only, real HTTP. LISTING/WANTED/COMMUNITY_SEED
(MULTI_SOURCE_VALUE_EVIDENCE.md): OFFER != WANTED != PROPOSAL != AGREEMENT != COMMUNITY_SEED, and
several traces of the same economic process never become several independent voices.
"""
import uuid
from smoke import ROOT, request
from multitenant import sql
from economic_exchange_e2e import PERMISSIONS as ECONOMIC, make_user

MEMBER = ECONOMIC + ['stir.references.read', 'stir.references.propose', 'stir.consent.manage', 'stir.governance.vote']
PUBLISHER = MEMBER + ['stir.references.publish', 'stir.retention.manage', 'stir.governance.manage']


def fixtures():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-mvse-{suffix}','STIR MultiSource {suffix}','active',false);")
    other = sql(f"select tenant_id from idax_core.tenant_create('stir-mvse-b-{suffix}','STIR MultiSource B','active',false);")

    def role(t, key, permissions):
        base = f'/api/shell/v1/tenants/{t}/roles'
        row = request(base, 'POST', {'key': key + suffix, 'name': key, 'description': 'Local multi-source test', 'enabled': True}, admin, (200, 201))
        request(base + '/' + row['id'] + '/permissions', 'PUT', permissions, admin)
        return row['id']
    publisher_role = role(tenant, 'publisher', PUBLISHER)
    member_role = role(tenant, 'member', MEMBER)
    b_role = role(other, 'other', MEMBER)
    ana = make_user(admin, tenant, publisher_role, 'ana', suffix)
    # Community bootstrap authority while Ordinary Governance is disabled is tenant owner/admin; a custom
    # publisher role is platform permission only. Ana is the tenant administrator for this fixture.
    sql(f"update idax_core.tenant_user set role='admin' where tenant_id='{tenant}' and user_id='{ana['user']['id']}';")
    pedro = make_user(admin, tenant, member_role, 'pedro', suffix)
    carlos = make_user(admin, tenant, member_role, 'carlos', suffix)
    bea = make_user(admin, other, b_role, 'bea', suffix)
    base = f'/api/stir/tenants/{tenant}'
    binding = request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': 'MultiSource ' + suffix, 'unitCode': 'MS', 'unitScale': 2}, ana['accessToken'])
    return admin, tenant, base, ana, pedro, carlos, bea, binding


def main():
    admin, tenant, base, ana, pedro, carlos, bea, binding = fixtures()
    a, p = ana['accessToken'], pedro['accessToken']
    community = binding['communityId']
    definition = request(base + '/references', 'POST', {'name': 'Wool 1kg', 'scope': 'One kilogram of raw wool',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 'kg'}, a)
    definition_id = definition['id']
    ref = base + '/references/' + definition_id

    # --- Enable LISTING/WANTED as eligible sources (both off by default; every existing definition
    # keeps its old behaviour until explicitly opted in) ---
    policy = request(base + '/references/' + definition_id + '/policies', 'POST', {'windowDays': 90, 'minimumObservations': 5,
        'minimumParticipants': 6, 'maximumParticipantShare': '0.40', 'freshnessDays': 30,
        'explanation': 'Enable listing/wanted sources', 'listingSourceEnabled': True, 'wantedSourceEnabled': True}, a)
    assert policy['listing_source_enabled'] is True and policy['wanted_source_enabled'] is True

    # --- 1. A valid LISTING with an indicative price and explicit consent ---
    listing = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': 'Wool bundle', 'description': 'Test',
        'category': 'home', 'resourceKind': 'physical', 'referenceDefinitionId': definition_id,
        'indicativeAmount': '40', 'indicativeQuantity': '1', 'indicativeUnitLabel': 'kg',
        'indicativeUnitRef': definition['unit_ref'], 'shareReferenceObservation': True}, a, 201)
    observations = request(ref + '/observations', token=a)
    # source_id is the immutable ListingRevision id, never the mutable Listing id itself - the
    # listing is correlated back through economic_lineage_id instead (MULTI_SOURCE_VALUE_EVIDENCE.md).
    listing_obs = next(o for o in observations if o['economic_lineage_id'] == listing['id'])
    assert listing_obs['source'] == 'LISTING' and listing_obs['aggregate_consent'] is True

    # --- 2. A valid WANTED with an indicative price ---
    wanted = request(base + '/listings', 'POST', {'direction': 'WANTED', 'title': 'Looking for wool', 'description': 'Test',
        'category': 'home', 'resourceKind': 'physical', 'referenceDefinitionId': definition_id,
        'indicativeAmount': '38', 'indicativeQuantity': '1', 'indicativeUnitLabel': 'kg',
        'indicativeUnitRef': definition['unit_ref'], 'shareReferenceObservation': True}, p, 201)
    wanted_obs = next(o for o in request(ref + '/observations', token=a) if o['economic_lineage_id'] == wanted['id'])
    assert wanted_obs['source'] == 'WANTED'

    # --- 3. Editing a listing preserves history: the first observation keeps its own amount ---
    request(base + '/listings/' + listing['id'], 'PUT', {'direction': 'OFFER', 'title': 'Wool bundle', 'description': 'Updated',
        'category': 'home', 'resourceKind': 'physical', 'referenceDefinitionId': definition_id, 'version': listing['version'],
        'indicativeAmount': '55', 'indicativeQuantity': '1', 'indicativeUnitLabel': 'kg',
        'indicativeUnitRef': definition['unit_ref'], 'shareReferenceObservation': True}, a)
    rows = sql(f"select amount from stir.reference_observation where tenant_id='{tenant}' and economic_lineage_id='{listing['id']}' order by observed_at;")
    assert rows.split('\n') == ['40.00', '55.00'], 'the edit created a NEW observation; the first one was never rewritten'

    # --- 4. Listing -> Proposal -> Agreement: one economic lineage, not three independent voices ---
    listing2 = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': 'Wool bundle 2', 'description': 'Test',
        'category': 'home', 'resourceKind': 'physical', 'referenceDefinitionId': definition_id}, a, 201)
    offer = request(base + '/listings/' + listing2['id'] + '/offers', 'POST', {'message': 'Interested', 'quantity': '1',
        'unitLabel': 'kg', 'proposedAmount': '39', 'proposedUnitRef': definition['unit_ref'], 'shareReferenceObservation': True}, p, 201)
    request(base + '/negotiations/' + offer['id'] + '/accept', 'POST', {'offerId': offer['offers'][-1]['id'],
        'expectedVersion': offer['version'], 'shareReferenceObservation': True}, a)
    lineages = sql(f"select distinct economic_lineage_id from stir.reference_observation where tenant_id='{tenant}' and definition_id='{definition_id}' and economic_lineage_id='{listing2['id']}';")
    assert lineages == listing2['id']

    # --- 15. Tenant isolation: bea (tenant B) sees none of this ---
    request(base + '/listings/' + listing['id'], token=bea['accessToken'], expected=(403, 404))
    # 2 rows: the original observation plus the edit's own new observation (step 3 above never
    # rewrites the first) - both still belong to this tenant only.
    assert sql(f"select count(*) from stir.reference_observation where economic_lineage_id='{listing['id']}';") == '2'

    # --- 8-12: Community Seed through Ordinary Governance ---
    gov = base + '/references/governance/ordinary'
    request(gov + '/settings/' + community, 'PUT', {'enabled': True}, a)
    for member in (ana, pedro, carlos):
        request(gov + '/members/' + community + '/' + member['user']['id'], 'POST', {}, a)
    request(gov + '/policy/' + community, 'POST', {'quorumNumerator': 1, 'quorumDenominator': 2, 'approvalNumerator': 2,
        'approvalDenominator': 3, 'votingWindowHours': 1, 'abstentionRule': 'COUNTS_TOWARD_QUORUM_NOT_APPROVAL',
        'explanation': 'v0.1'}, a)

    # 11. SuperAdmin cannot propose a seed
    request(gov + '/proposals/' + definition_id + '/seed', 'POST', {'kind': 'VALUE', 'lowerValue': '30', 'upperValue': '30',
        'rationale': 'x', 'basis': 'x', 'validDays': 90}, admin, expected=(401, 403, 404))

    # 8. A real seed proposal, voted on by the real electorate. The real vote is time-boxed
    # (votingWindowHours has a hard minimum of 1 - never fast-forwarded over real HTTP); this
    # script proves the proposal/vote mechanics, the same discipline ordinary_governance_e2e.py
    # already established. Close/execute/double-execution/version-history are proven directly
    # against PostgreSQL with a backdated voting_closes_at
    # (MultiSourceValueEvidencePostgresTest.seedApprovedThroughOrdinaryGovernance and
    # .aNewSeedIsANewVersionNeverARewriteOfTheOlderOne).
    proposal = request(gov + '/proposals/' + definition_id + '/seed', 'POST', {'kind': 'VALUE', 'lowerValue': '30',
        'upperValue': '30', 'rationale': 'Initial orientation pending real evidence', 'basis': 'Founding assembly', 'validDays': 90}, a)
    proposal_id = proposal['id']
    # 11 (continued). SuperAdmin cannot vote either.
    request(gov + '/proposal/' + proposal_id + '/vote', 'POST', {'choice': 'APPROVE'}, admin, expected=(401, 403, 404))
    request(gov + '/proposal/' + proposal_id + '/vote', 'POST', {'choice': 'APPROVE'}, a)
    request(gov + '/proposal/' + proposal_id + '/vote', 'POST', {'choice': 'APPROVE'}, p)
    # 17. Replay/double vote blocked
    request(gov + '/proposal/' + proposal_id + '/vote', 'POST', {'choice': 'APPROVE'}, a, expected=409)
    tally = request(gov + '/proposal/' + proposal_id, token=a)
    assert tally['votesAPPROVE'] == 2 and tally['electorateSize'] == 3

    print('PASS: LISTING and WANTED recorded with real indicative prices and per-party consent; '
          'editing a listing preserves its earlier observation untouched; a listing->proposal->agreement '
          'chain shares one economic lineage; tenant isolation holds; SuperAdmin cannot propose or vote on '
          'a community seed; double voting is rejected; a real vote over the real electorate reaches quorum '
          'and majority')


if __name__ == '__main__':
    main()
