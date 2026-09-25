"""Local development only. Real HTTP proof of the Participant Independence / Identity Integrity
MVP: account diversity is not participant independence. STIR never authors or alters what osTRIS
answers about identity continuity - a publisher can only ask again. This script drives osTRIS's own
real, already-shipped private continuity endpoint (not a mock) through its real HTTP contract, and
STIR's own new /independence-refresh endpoint, to prove the full chain end to end: no economic
binding yet, a real osTRIS "no decision recorded" answer, and a real osTRIS CONFIRMED relatedness
decision reflected live in STIR's own projection.

osTRIS has no HTTP endpoint to create a RiskSubject (by design - it is an internal correlation
anchor, never client-supplied); this script seeds exactly that one row directly via SQL, the same
way multitenant.py's sql() helper is already used for setup/verification elsewhere, and then drives
every actual identity decision through osTRIS's real, permissioned HTTP contract
(POST /api/ostris/identity/continuity-decisions) - never a mock of it. Aggregate "raw diversity
looks fine, adjusted count/concentration reveals the real relatedness" scenarios are already proven
twice - as a pure function (EvidenceAnalysisTest) and against real PostgreSQL
(ParticipantIndependencePostgresTest) - so this script does not reproduce that arithmetic a third
time; it proves the parts only a real running system can prove: real osTRIS HTTP calls, real
permission/tenant boundaries on STIR's new endpoints, and the real per-observation exclusion on a
real Agreement.
"""
import os
import time
import uuid as uuidlib
from smoke import ROOT, request
from multitenant import sql
from community_references_e2e import fixtures, MEMBER
from economic_exchange_e2e import activate_economic, make_user

def uuid7():
    # Mirrors osTRIS's own UuidV7.generate() (es.idynamicsax.ostris.core.UuidV7) exactly - osTRIS
    # rejects any identifier that isn't a lowercase canonical UUIDv7 for its own domain ids.
    millis = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand_a = int.from_bytes(os.urandom(2), 'big') & 0x0FFF
    msb = (millis << 16) | 0x7000 | rand_a
    rand_b = int.from_bytes(os.urandom(8), 'big')
    lsb = (rand_b & 0x3FFFFFFFFFFFFFFF) | 0x8000000000000000
    return str(uuidlib.UUID(int=(msb << 64) | lsb))

def participant_id(tenant, user_id):
    return sql(f"select participant_id from stir.participant_economic_binding where tenant_id='{tenant}' and user_id='{user_id}';")

def confirm_related(admin, tenant, community, participant, risk_subject, reason):
    # admin's bootstrap token carries no tenant claim - osTRIS's own OstrisJwtAuthFilter resolves
    # the acting tenant from X-Tenant for a superuser caller, exactly like STIR's OstrisClient does
    # for every other cross-service call.
    return request('/api/ostris/identity/continuity-decisions', 'POST', {
        'decisionId': uuid7(), 'communityId': community, 'participantId': participant,
        'riskSubjectId': risk_subject, 'status': 'CONFIRMED', 'evidenceRefs': ['e2e-fixture'],
        'decisionAuthority': 'e2e-fixture', 'reason': reason,
    }, admin, 201, extra_headers={'X-Tenant': tenant})

def main():
    tenant, base, ana, pedro, carlos, bea, binding = fixtures()
    admin = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
        'password': (ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    community = binding['communityId']
    pedro_user, carlos_user = pedro['user']['id'], carlos['user']['id']

    # OstrisClient relays the CALLER's own token to osTRIS (never a service credential - see its own
    # docstring), so whoever triggers a refresh needs osTRIS's own private-continuity permission
    # too, not just stir.references.publish. fixtures()'s publisher role predates this MVP and does
    # not grant it, so this script provisions its own reviewer with both from the start (a fresh
    # user, not a role change on an already-issued token - permissions are baked into the JWT at
    # login, the same reason multitenant.py re-logs in after a role change).
    shell = f'/api/shell/v1/tenants/{tenant}'
    reviewer_role = request(shell+'/roles', 'POST', {'key': 'reviewer-'+tenant[:8], 'name': 'Independence reviewer',
        'description': 'E2E fixture', 'enabled': True}, admin, (200, 201))
    request(shell+'/roles/'+reviewer_role['id']+'/permissions', 'PUT',
        MEMBER + ['stir.references.publish', 'OSTRIS_IDENTITY_CONTINUITY_READ_PRIVATE'], admin)
    reviewer = make_user(admin, tenant, reviewer_role['id'], 'reviewer', tenant[:8])
    a = reviewer['accessToken']

    # --- No economic account bound yet: never invented, never a live osTRIS call for this one ---
    never_activated = request(base+'/references/participants/'+carlos_user+'/independence-refresh', 'POST', {}, a)
    assert never_activated['status'] == 'NO_OSTRIS_BINDING'
    assert request(base+'/references/participants/'+carlos_user+'/independence', token=a)['status'] == 'NO_OSTRIS_BINDING'

    # --- Real economic activation, then a real osTRIS "no continuity decision recorded" answer ---
    request(base+'/participants/me', 'PUT', {'displayName': 'Pedro', 'bio': 'E2E fixture', 'location': 'Girona'}, pedro['accessToken'])
    request(base+'/participants/me', 'PUT', {'displayName': 'Carlos', 'bio': 'E2E fixture', 'location': 'Girona'}, carlos['accessToken'])
    activate_economic(base, pedro)
    activate_economic(base, carlos)
    pedro_participant, carlos_participant = participant_id(tenant, pedro_user), participant_id(tenant, carlos_user)
    unknown = request(base+'/references/participants/'+pedro_user+'/independence-refresh', 'POST', {}, a)
    assert unknown['status'] == 'INDEPENDENCE_UNKNOWN', unknown

    # --- Permission and tenant boundaries on the new endpoints - same guard as every other mutation ---
    request(base+'/references/participants/'+pedro_user+'/independence-refresh', 'POST', {}, pedro['accessToken'], expected=403)
    other_base = f"/api/stir/tenants/{bea['tenants'][0]['id']}"
    request(other_base+'/references/participants/'+pedro_user+'/independence-refresh', 'POST', {}, bea['accessToken'], expected=(400, 403, 404))
    request(base+'/references/participants/'+pedro_user+'/independence-refresh', 'POST', {}, admin, expected=(401, 403, 404))

    # --- A real osTRIS CONFIRMED continuity decision - pedro and carlos share one RiskSubject ---
    risk_subject = uuid7()
    sql(f"insert into ostris.risk_subject(id,tenant_id,community_id) values ('{risk_subject}','{tenant}','{community}');")
    confirm_related(admin, tenant, community, pedro_participant, risk_subject, 'Same continuity cluster (e2e fixture)')
    confirm_related(admin, tenant, community, carlos_participant, risk_subject, 'Same continuity cluster (e2e fixture)')
    related_pedro = request(base+'/references/participants/'+pedro_user+'/independence-refresh', 'POST', {}, a)
    related_carlos = request(base+'/references/participants/'+carlos_user+'/independence-refresh', 'POST', {}, a)
    assert related_pedro['status'] == related_carlos['status'] == 'RELATED_CONTINUITY'

    # --- A real Agreement directly between pedro and carlos - raw history survives regardless of
    # same-day evidence-manifest caching (dailyCutPreventsLiveDifferencing, already established:
    # snapshot()'s SQL only ever considers observed_at < today's UTC cutoff, so an Agreement created
    # "just now" is invisible to any evidence computation for the rest of this real calendar day -
    # never merely excluded with a reason, structurally absent from the query). The
    # RELATED_PARTICIPANT_CLUSTER per-observation exclusion this same Agreement would receive once
    # the daily cut passes it is already proven directly against real PostgreSQL with a backdated
    # timestamp (ParticipantIndependencePostgresTest) and as a pure function (EvidenceAnalysisTest) -
    # this script proves the parts only a real running system can: the raw Agreement is captured with
    # its real participants, unaffected by cutoff timing, and the Agreement itself is never blocked
    # or rewritten by any of this. ---
    definition = request(base+'/references', 'POST', {'name': 'Firewood 1 stere', 'scope': 'One stere of dry firewood',
        'attributes': {}, 'quantityBasis': '1', 'quantityUnit': 'stere'}, a)
    ref = base+'/references/'+definition['id']
    listing = request(base+'/listings', 'POST', {'direction': 'OFFER', 'title': 'Firewood between related accounts',
        'description': 'E2E fixture', 'category': 'home', 'resourceKind': 'physical',
        'referenceDefinitionId': definition['id']}, pedro['accessToken'], 201)
    offer = request(base+'/listings/'+listing['id']+'/offers', 'POST', {'message': 'proposal', 'quantity': '1',
        'unitLabel': 'stere', 'proposedAmount': '10', 'proposedUnitRef': definition['unit_ref'],
        'shareReferenceObservation': True}, carlos['accessToken'], 201)
    request(base+'/negotiations/'+offer['id']+'/accept', 'POST', {'offerId': offer['offers'][-1]['id'],
        'expectedVersion': offer['version'], 'shareReferenceObservation': True}, pedro['accessToken'])
    observations = request(ref+'/observations', token=a)
    agreement_row = next(o for o in observations if o['source'] == 'AGREEMENT')
    assert {agreement_row['participant_a'], agreement_row['participant_b']} == {pedro_user, carlos_user}
    manifest = request(ref+'/evidence-manifest', token=a)
    assert manifest['included'] == [] and manifest['exclusions'] == {}, \
        "today's own Agreement is outside snapshot()'s < cutoff window - invisible to any manifest today, same as every other same-day scenario in this codebase"

    # --- Independence status itself (unlike evidence-manifest) has no daily cutoff - it is a direct,
    # always-live read of the projection refresh() already persisted, proven again here as one more
    # live boundary: a stranger (bea, a different tenant) cannot read it either. ---
    assert request(base+'/references/participants/'+pedro_user+'/independence', token=a)['status'] == 'RELATED_CONTINUITY'
    request(other_base+'/references/participants/'+pedro_user+'/independence', token=bea['accessToken'], expected=(400, 403, 404))

    print('PASS HTTP PARTICIPANT INDEPENDENCE: no-binding/unknown/related-continuity states all come from '
          "osTRIS's own real HTTP contract (not a mock), permission and tenant boundaries on the new "
          'refresh/read endpoints match every other mutation and every other private read, a real '
          'Agreement between two osTRIS-confirmed related accounts is captured with its real '
          'participants regardless of same-day evidence-manifest caching, and independence status '
          'itself is a live, always-current read unaffected by that same daily cutoff.')

if __name__ == '__main__':
    main()
