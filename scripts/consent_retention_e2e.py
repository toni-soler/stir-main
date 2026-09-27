"""Local development only, real HTTP. Consent/Retention (CONSENT_RETENTION.md): having a datum,
having permission to use it, still being allowed to retain it, and still being eligible as current
evidence are four different questions - none of the adversarial scenarios below should ever
collapse them.
"""
import json
import uuid
from smoke import ROOT, request
from multitenant import sql
from economic_exchange_e2e import PERMISSIONS as ECONOMIC, make_user

MEMBER = ECONOMIC + ['stir.references.read', 'stir.references.propose', 'stir.consent.manage']
PUBLISHER = MEMBER + ['stir.references.publish', 'stir.retention.manage']


def fixtures():
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT / '.local/secrets/login_password').read_text().strip()})['accessToken']
    suffix = uuid.uuid4().hex[:8]
    tenant = sql(f"select tenant_id from idax_core.tenant_create('stir-cr-{suffix}','STIR ConsentRetention {suffix}','active',false);")
    other = sql(f"select tenant_id from idax_core.tenant_create('stir-cr-b-{suffix}','STIR ConsentRetention B','active',false);")

    def role(t, key, permissions):
        base = f'/api/shell/v1/tenants/{t}/roles'
        row = request(base, 'POST', {'key': key + suffix, 'name': key, 'description': 'Local consent/retention test', 'enabled': True}, admin, (200, 201))
        request(base + '/' + row['id'] + '/permissions', 'PUT', permissions, admin)
        return row['id']
    publisher_role = role(tenant, 'publisher', PUBLISHER)
    member_role = role(tenant, 'member', MEMBER)
    b_role = role(other, 'other', MEMBER)
    ana = make_user(admin, tenant, publisher_role, 'ana', suffix)
    pedro = make_user(admin, tenant, member_role, 'pedro', suffix)
    carlos = make_user(admin, tenant, member_role, 'carlos', suffix)
    bea = make_user(admin, other, b_role, 'bea', suffix)
    base = f'/api/stir/tenants/{tenant}'
    binding = request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': 'ConsentRetention ' + suffix, 'unitCode': 'CR', 'unitScale': 2}, ana['accessToken'])
    return admin, tenant, base, ana, pedro, carlos, bea, binding


def main():
    admin, tenant, base, ana, pedro, carlos, bea, binding = fixtures()
    a, p = ana['accessToken'], pedro['accessToken']
    community = binding['communityId']
    definition = request(base + '/references', 'POST', {'name': 'Firewood 10kg', 'scope': 'One 10kg bundle',
        'attributes': {'weight': '10kg'}, 'quantityBasis': '1', 'quantityUnit': 'bundle'}, a)
    ref = base + '/references/' + definition['id']
    listing = request(base + '/listings', 'POST', {'direction': 'OFFER', 'title': 'Firewood with a reference',
        'description': 'Test bundle', 'category': 'home', 'resourceKind': 'physical', 'referenceDefinitionId': definition['id']}, a, 201)

    def agree(amount, offeror_consent, acceptor_consent):
        n = request(base + '/listings/' + listing['id'] + '/offers', 'POST', {'message': 'Offer',
            'quantity': '1', 'unitLabel': 'bundle', 'proposedAmount': amount, 'proposedUnitRef': definition['unit_ref'],
            'shareReferenceObservation': offeror_consent}, p, 201)
        return request(base + '/negotiations/' + n['id'] + '/accept', 'POST', {'offerId': n['offers'][-1]['id'],
            'expectedVersion': n['version'], 'shareReferenceObservation': acceptor_consent}, a)

    def backdate(observation_id, days):
        # A real HTTP Agreement always has observed_at=now(); there is no API to backdate it, by
        # design. Only a genuine session-level trigger bypass (session_replication_role=replica,
        # itself requiring the real Postgres superuser multitenant.sql() already connects as) can
        # move it for fixture purposes - the reject_reference_observation_mutation() trigger blocks
        # every ordinary UPDATE to this column for every ordinary role, with no exception.
        sql(f"set session_replication_role='replica'; "
            f"update stir.reference_observation set observed_at=now()-interval '{days} days' where tenant_id='{tenant}' and id='{observation_id}'; "
            f"set session_replication_role='origin';")

    # --- 1. Bilateral valid consent: both grant, observation eligible for evidence ---
    bilateral = agree('10', True, True)
    observations = request(ref + '/observations', token=a)
    bilateral_obs = next(o for o in observations if o['source_id'] == bilateral['id'])
    assert bilateral_obs['aggregate_consent'] is True

    # --- 2. One party does not consent: observation recorded, but never eligible ---
    one_sided = agree('12', True, False)
    one_sided_obs = next(o for o in request(ref + '/observations', token=a) if o['source_id'] == one_sided['id'])
    assert one_sided_obs['aggregate_consent'] is False
    assert sql(f"select count(*) from stir.reference_observation where tenant_id='{tenant}' and id='{one_sided_obs['id']}';") == '1', \
        'declined consent must still be recorded as a datum, never silently dropped'

    # --- Individual per-party consent records exist for the bilateral one; pedro can see/withdraw his ---
    pedro_consents = request(base + '/references/consent/mine', token=p)
    pedro_consent = next(c for c in pedro_consents if c['observation_id'] == bilateral_obs['id'])
    assert pedro_consent['status'] == 'GRANT'
    ana_consents = request(base + '/references/consent/mine', token=a)
    ana_consent = next(c for c in ana_consents if c['observation_id'] == bilateral_obs['id'])
    assert ana_consent['status'] == 'GRANT'

    # --- 3. Only the consenting party can withdraw; another member cannot ---
    request(base + '/references/consent/' + pedro_consent['id'] + '/withdraw', 'POST', {'reason': 'not mine'}, carlos['accessToken'], expected=403)

    # --- 4. Withdrawal: allowed, idempotent, never rewrites the observation itself ---
    withdrawn = request(base + '/references/consent/' + pedro_consent['id'] + '/withdraw', 'POST', {'reason': 'changed my mind'}, p)
    assert withdrawn['status'] == 'WITHDRAW'
    replayed = request(base + '/references/consent/' + pedro_consent['id'] + '/withdraw', 'POST', {'reason': 'replayed'}, p)
    assert replayed['status'] == 'WITHDRAW'
    events = sql(f"select count(*) from stir.reference_consent_event where tenant_id='{tenant}' and consent_id='{pedro_consent['id']}';")
    assert events == '2', 'GRANT + exactly one WITHDRAW, never a duplicate from the replay'
    assert sql(f"select aggregate_consent from stir.reference_observation where tenant_id='{tenant}' and id='{bilateral_obs['id']}';") == 't', \
        'aggregate_consent as originally captured is never rewritten by a later withdrawal'

    # --- 5. Withdrawn observation is excluded from evidence with its own reason ---
    # Not re-proven here over HTTP: the very first accept() in this run already called
    # freezeContext() -> view() -> snapshot(), permanently caching today's UTC cutoff for this
    # definition before there was any chance to backdate an observation into it - the same
    # "daily cut resists live differencing" limitation community_references_e2e.py's own real
    # HTTP suite works around by only ever proving cache STABILITY, never real-time recomputation
    # (VALIDATION_COMMUNITY_VALUE_GOVERNANCE.md). The CONSENT_WITHDRAWN exclusion-reason mechanism
    # itself is proven directly against PostgreSQL, with observed_at controllable at insert time,
    # in ConsentRetentionPostgresTest.withdrawalAfterAcceptanceExcludesFromFutureSnapshotsWithDistinctReason
    # and .cachedSnapshotSurvivesALaterWithdrawalUnchanged.

    # --- Tenant isolation: bea (tenant B) cannot see or affect tenant A's consent ---
    request(base + '/references/consent/' + ana_consent['id'], token=bea['accessToken'], expected=(403, 404))
    request(base + '/references/consent/' + ana_consent['id'] + '/withdraw', 'POST', {'reason': 'cross tenant'}, bea['accessToken'], expected=(403, 404))

    # --- Retention: floor enforced, SuperAdmin excluded, policy settable by the publisher ---
    # @Min(90) on the controller's PolicyRequest rejects this before it even reaches
    # RetentionService.setPolicyDirect()'s own floor check (which the Postgres test proves
    # directly, since OrdinaryGovernanceService.execute() calls that method bypassing bean
    # validation entirely) - both layers enforce the same floor, from different directions.
    request(base + '/references/retention/policy/' + community, 'POST', {'retentionPeriodDays': 10, 'explanation': 'too short'}, a, expected=400)
    request(base + '/references/retention/policy/' + community, 'POST', {'retentionPeriodDays': 120, 'explanation': 'superadmin attempt'}, admin, expected=(401, 403, 404))
    policy = request(base + '/references/retention/policy/' + community, 'POST', {'retentionPeriodDays': 120, 'explanation': 'Community decision: 120 days'}, a)
    assert policy['retention_period_days'] == 120

    # --- Retention expiry vs market integrity hold: backdate two more agreements via SQL (no HTTP
    # path can set observed_at in the past) to simulate the retention window having elapsed. ---
    old_a = agree('20', True, True)
    old_a_obs = next(o for o in request(ref + '/observations', token=a) if o['source_id'] == old_a['id'])
    old_b = agree('22', True, True)
    old_b_obs = next(o for o in request(ref + '/observations', token=a) if o['source_id'] == old_b['id'])
    backdate(old_a_obs['id'], 200)
    backdate(old_b_obs['id'], 200)

    # old_b gets a market-integrity signal - even past the retention window, it must stay held.
    signal = request(base + '/references/integrity/signals', 'POST', {'observationId': old_b_obs['id'], 'signalCode': 'OUTLIER_PENDING_REVIEW',
        'reason': 'looks unusual', 'evidenceRefs': []}, a)
    status_b = request(base + '/references/retention/observation/' + old_b_obs['id'] + '/status', token=a)
    assert status_b['status'] == 'MUST_BE_RETAINED' and status_b['hold'] is True
    request(base + '/references/retention/observation/' + old_b_obs['id'] + '/anonymize', 'POST', {'reason': 'trying anyway'}, a, expected=409)

    due = request(base + '/references/retention/definition/' + definition['id'] + '/due', token=a)
    due_ids = {d['observationId'] for d in due}
    assert old_a_obs['id'] in due_ids and old_b_obs['id'] not in due_ids

    # --- 6. Anonymization: real, explicit, audited, never reversible ---
    anonymized = request(base + '/references/retention/observation/' + old_a_obs['id'] + '/anonymize',
        'POST', {'reason': 'retention window elapsed, no case ever opened'}, a)
    assert anonymized.get('participant_a') is None and anonymized.get('participant_b') is None
    assert float(anonymized['amount']) == 20, 'amount/history preserved - only identifiers are removed'
    request(base + '/references/retention/observation/' + old_a_obs['id'] + '/anonymize', 'POST', {'reason': 'again'}, a, expected=409)
    assert sql(f"select count(*) from stir.retention_lifecycle_event where tenant_id='{tenant}' and observation_id='{old_a_obs['id']}';") == '1'

    print('PASS: bilateral consent granted and individually recorded per party; one-sided decline recorded but never '
          'eligible; only the consenting party can withdraw; withdrawal is idempotent and never rewrites '
          'aggregate_consent or the observation; tenant isolation holds for consent records; retention floor and '
          'SuperAdmin exclusion enforced; a market-integrity hold blocks anonymization even past the retention '
          'window; anonymization removes only participant identifiers, is audited, and cannot repeat')


if __name__ == '__main__':
    main()
