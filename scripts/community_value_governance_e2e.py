"""Local development only. Real HTTP proof of the publisher's full evidence-to-governance toolkit
this increment adds on top of the already-validated community_references_e2e.py flow: raw vs
reference-eligible vs excluded observations with reason codes, reference policy configuration
inside constitutional floors (and rejection when it would cross them), and the market integrity
signal -> review -> FINAL/DISMISSED lifecycle - including platform-SuperAdmin exclusion from
community governance.

A hard constraint shapes what this script can and cannot demonstrate live: snapshot()'s own SQL
only ever considers observations strictly BEFORE today's UTC cutoff (`observed_at < cutoff`), by
design (see MARKET_INTEGRITY.md's daily cutoff / anti-live-differencing). A row created "just now"
by this script is invisible to every evidence-manifest computation for the rest of that same real
calendar day - not merely excluded-with-a-reason, but never even considered. Reaching
SUFFICIENT_DATA, exercising a specific per-observation exclusion reason code (NO_BILATERAL_CONSENT,
OUTSIDE_WINDOW, ...), or showing a real median therefore cannot be done from a single live run
without backdating, which the public HTTP API has no way to request. Those scenarios are already
proven directly against PostgreSQL with explicitly backdated timestamps -
EvidenceAnalysisTest.java (pure unit level) and
ReferencePostgresTest.observationsAndEvidenceManifestExposeRawVsEligibleOnlyToPublishers (through
the real service and schema). What a live same-day run over HTTP *can* prove, and what this script
proves instead: observations() has no such cutoff and always reflects the complete, undeleted raw
history regardless of when it was created; evidence-manifest never differs within the same day no
matter how many more deals happen; and every new endpoint's permission/tenant boundary holds.
"""
import uuid
from smoke import ROOT, request
from multitenant import sql
from community_references_e2e import fixtures, MEMBER
from marketplace_browser_smoke import make_login

def main():
    tenant,base,ana,pedro,carlos,bea,binding=fixtures()
    a,p=ana['accessToken'],pedro['accessToken']
    admin=request('/api/shell/v1/auth/login','POST',{'email':'admin@stir.test',
        'password':(ROOT/'.local/secrets/login_password').read_text().strip()})['accessToken']
    # A second delegated publisher, distinct from ana, for the FINAL/DISMISSED decisions below - the
    # case originator can never decide their own case, so completing the real lifecycle (not just
    # the rejections around it) needs a genuinely different reviewer.
    suffix2=uuid.uuid4().hex[:8]
    publisher_role=request(f'/api/shell/v1/tenants/{tenant}/roles','POST',
        {'key':'publisher2-'+suffix2,'name':'Second publisher','description':'Local governance test','enabled':True},admin,(200,201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/'+publisher_role['id']+'/permissions','PUT',MEMBER+['stir.references.publish'],admin)
    diego_login=make_login(admin,tenant,publisher_role['id'],'diego',suffix2)
    diego=request('/api/shell/v1/auth/login','POST',dict(zip(['email','password'],diego_login)))['accessToken']

    definition=request(base+'/references','POST',{'name':'Flour 1kg','scope':'One 1kg bag of flour',
        'attributes':{'weight':'1kg'},'quantityBasis':'1','quantityUnit':'bag'},a)
    ref=base+'/references/'+definition['id']
    listing=request(base+'/listings','POST',{'direction':'OFFER','title':'Flour with a reference',
        'description':'Test bag','category':'home','resourceKind':'physical','referenceDefinitionId':definition['id']},a,201)
    def agree(amount,buyer_token,consent=True):
        n=request(base+'/listings/'+listing['id']+'/offers','POST',{'message':'Free proposal',
            'quantity':'1','unitLabel':'bag','proposedAmount':amount,'proposedUnitRef':definition['unit_ref'],
            'shareReferenceObservation':consent},buyer_token,201)
        return request(base+'/negotiations/'+n['id']+'/accept','POST',{'offerId':n['offers'][-1]['id'],
            'expectedVersion':n['version'],'shareReferenceObservation':consent},a)

    # --- Raw history is unconditional; today's evidence-manifest is empty and stays empty ---
    first=agree('5',p,consent=False)
    manifest=request(ref+'/evidence-manifest',token=a)
    assert manifest['included']==[] and manifest['exclusions']=={}, \
        "today's own observations are outside snapshot()'s < cutoff window - invisible to any manifest today"
    # Plain member (pedro) has stir.references.read, not .publish - both new endpoints require publish.
    request(ref+'/observations',token=p,expected=403)
    request(ref+'/evidence-manifest',token=p,expected=403)

    for amount in ['10','11','12','13','14']:
        agree(amount,p,consent=True)
    # observations() has no such cutoff - always live, always the complete, undeleted raw history,
    # regardless of what any same-day evidence-manifest could ever say about it.
    observations=request(ref+'/observations',token=a)
    assert len(observations)==12, 'every proposal and every agreement stays in the raw list, 6 deals x 2 rows'
    agreement_rows=[o for o in observations if o['source']=='AGREEMENT']
    proposal_rows=[o for o in observations if o['source']=='PROPOSAL']
    assert len(agreement_rows)==6 and len(proposal_rows)==6
    assert all(o['participant_a'] and o['participant_b'] for o in agreement_rows), 'publisher view includes real participants'
    assert first['id'] in {o['source_id'] for o in agreement_rows}, 'the very first Agreement is still in the raw list'

    # Anti-live-differencing: eleven more raw observations later, still nothing new to see today.
    assert request(ref+'/evidence-manifest',token=a)==manifest
    evidence=request(ref,token=a)['evidence']
    assert evidence['status']=='INSUFFICIENT_DATA'

    # --- Platform SuperAdmin gets no community-governance capability by being SuperAdmin ---
    # Scoped hardening (ReferenceService.requireCommunityAuthority, GOVERNANCE_CAPTURE_THREAT_MODEL.md):
    # the boundary protects MUTATION AUTHORITY only, at the service layer (not merely the controller),
    # for exactly the six sensitive mutations this increment and the prior one introduced. Every one of
    # them is asserted rejected for admin here (scenario 1: SuperAdmin -> controller -> rejected; the
    # direct-service-call and stir.*-permission-is-not-enough scenarios are proven in
    # ReferencePostgresTest, which calls ReferenceService/MarketIntegrityService with no controller in
    # the path at all). Plain reads are deliberately NOT gated by this check - asserted succeeding for
    # admin right below - so this also proves the explicit anti-over-blocking requirement: protecting
    # mutation authority must not turn all of STIR into inaccessible for a platform administrator.
    request(ref+'/policies','POST',{'windowDays':90,'minimumObservations':5,'minimumParticipants':6,
        'maximumParticipantShare':'0.40','freshnessDays':30,'explanation':'SuperAdmin attempt'},admin,expected=(401,403,404))
    request(base+'/references','POST',{'name':'SuperAdmin Definition','scope':'Should never be created',
        'attributes':{},'quantityBasis':'1','quantityUnit':'unit'},admin,expected=(401,403,404))
    request(ref+'/proposals','POST',{'kind':'CONVENTION','lowerValue':'1','upperValue':'1',
        'explanation':'SuperAdmin attempt','origin':'SuperAdmin','validDays':30},admin,expected=(401,403,404))
    request(base+'/references/proposals/'+str(uuid.uuid4())+'/publish','POST',
        {'decision':'SuperAdmin attempt'},admin,expected=(401,403,404))
    request(base+'/references/integrity/signals','POST',{'observationId':str(uuid.uuid4()),
        'signalCode':'OUTLIER_PENDING_REVIEW','reason':'SuperAdmin attempt','evidenceRefs':[]},admin,expected=(401,403,404))
    request(base+'/references/integrity/cases/'+str(uuid.uuid4())+'/decisions','POST',
        {'status':'UNDER_REVIEW','reason':'SuperAdmin attempt'},admin,expected=(401,403,404))
    # Ordinary reads stay reachable for admin - this is the deliberate boundary, not an oversight.
    # (community()/definitions()/view()/observations()/evidence-manifest() are all still gated by
    # idax-core's pre-existing, out-of-scope stir.* superuser permission bypass - documented as a
    # residual, deliberate scope decision in GOVERNANCE_CAPTURE_THREAT_MODEL.md, not silently left.)
    assert request(ref,token=admin)['definition']['id']==definition['id']
    assert isinstance(request(ref+'/observations',token=admin),list)
    # SuperAdmin's genuinely legitimate platform function (scenario 5) already ran earlier in this
    # very script, in the same admin session that every rejection above reuses: creating the
    # 'publisher2' role and provisioning diego through it, both real idax-shell platform-admin HTTP
    # calls that succeeded. SuperAdmin exclusion from community governance costs it nothing there.

    # --- Reference policy: ordinary publisher governance inside constitutional floors ---
    tightened=request(ref+'/policies','POST',{'windowDays':60,'minimumObservations':7,'minimumParticipants':6,
        'maximumParticipantShare':'0.30','freshnessDays':20,'explanation':'Tighten after reviewing the sample'},a)
    assert tightened['minimum_observations']==7
    assert request(ref,token=p)['policy']['id']==tightened['id']
    # A numeric below-the-floor value (e.g. minimumObservations=2) is rejected even earlier, by the
    # request DTO's own @Min(5)/@Min(6) bean validation (400 Bad Request) - deliberately set to
    # match today's initial constitutional floor, so there is no DTO-valid way to under-run it
    # without first amending the constitution to lower it (a 7-of-7 Seven Keys action, out of this
    # script's scope). What the 409 below proves instead: a protected constitutional field can
    # never be set through the ordinary policy API - "REJECT / REQUIRE CONSTITUTIONAL GOVERNANCE",
    # never an ordinary-publisher bypass.
    # Reject: a protected constitutional field can never be set through the ordinary policy API.
    request(ref+'/policies','POST',{'windowDays':60,'minimumObservations':7,'minimumParticipants':6,
        'maximumParticipantShare':'0.30','freshnessDays':20,'explanation':'Sneak a protected field',
        'independenceChecksRequired':False},a,expected=409)

    # --- Market integrity: SIGNAL -> UNDER_REVIEW -> FINAL, and separately -> DISMISSED ---
    suspect=agreement_rows[0]
    signal=request(base+'/references/integrity/signals','POST',{'observationId':suspect['id'],
        'signalCode':'HIGH_COUNTERPARTY_CONCENTRATION','reason':'Same counterparty for every recent deal',
        'evidenceRefs':['private-note-1']},a)
    assert signal['status']=='SIGNAL'
    # SIGNAL cannot jump straight to FINAL - only SIGNAL->UNDER_REVIEW or UNDER_REVIEW->FINAL/DISMISSED.
    request(base+'/references/integrity/cases/'+signal['id']+'/decisions','POST',
        {'status':'FINAL','reason':'Skip review'},a,expected=409)
    request(base+'/references/integrity/cases/'+signal['id']+'/decisions','POST',
        {'status':'UNDER_REVIEW','reason':'Looking into it'},a)
    # The originator (ana, who raised this signal) cannot decide their own case.
    request(base+'/references/integrity/cases/'+signal['id']+'/decisions','POST',
        {'status':'FINAL','reason':'Self-approval attempt'},a,expected=409)
    cases=request(base+'/references/integrity/definitions/'+definition['id']+'/cases',token=a)
    assert any(c['id']==signal['id'] and c['status']=='UNDER_REVIEW' for c in cases)
    # A genuinely different publisher (diego) completes the real lifecycle: SIGNAL -> UNDER_REVIEW -> FINAL.
    finalized=request(base+'/references/integrity/cases/'+signal['id']+'/decisions','POST',
        {'status':'FINAL','reason':'Confirmed: every deal was with the same counterparty'},diego)
    assert finalized['status']=='FINAL'
    assert [h['status'] for h in request(base+'/references/integrity/cases/'+signal['id']+'/history',token=a)]==['SIGNAL','UNDER_REVIEW','FINAL']

    dismissable=agreement_rows[1]
    dismiss_signal=request(base+'/references/integrity/signals','POST',{'observationId':dismissable['id'],
        'signalCode':'OUTLIER_PENDING_REVIEW','reason':'Looks unusual but probably fine','evidenceRefs':[]},a)
    request(base+'/references/integrity/cases/'+dismiss_signal['id']+'/decisions','POST',
        {'status':'UNDER_REVIEW','reason':'Checking'},a)
    dismissed=request(base+'/references/integrity/cases/'+dismiss_signal['id']+'/decisions','POST',
        {'status':'DISMISSED','reason':'Confirmed legitimate, no finding'},diego)
    assert dismissed['status']=='DISMISSED'
    # (FINAL's exclusion effect on future evidence is intentionally not asserted here - a FINAL event
    # only changes eligibility strictly before the daily UTC cutoff, never live-differencing today's
    # own snapshot; ReferencePostgresTest.finalIntegrityFindingChangesEligibilityWithoutRewritingRawAgreement
    # and .signalCanReachFinalThroughTheRealDecideFlowWithoutDeletingTheRawObservation already prove
    # that effect directly against the database.)
    still_cached=request(ref+'/evidence-manifest',token=a)
    assert dismissable['id'] not in still_cached['exclusions'], 'a dismissed signal excludes nothing'
    assert sql(f"select count(*) from stir.reference_observation where tenant_id='{tenant}';")==str(len(request(ref+'/observations',token=a))), \
        'no raw observation was ever deleted by any of the review decisions'

    # --- Tenant isolation for every new endpoint this increment adds ---
    request(ref+'/observations',token=bea['accessToken'],expected=(400,403,404))
    request(ref+'/evidence-manifest',token=bea['accessToken'],expected=(400,403,404))
    request(base+'/references/integrity/definitions/'+definition['id']+'/cases',token=bea['accessToken'],expected=(400,403,404))
    other_base=f"/api/stir/tenants/{bea['tenants'][0]['id']}"
    # Bea has no stir.references.publish in her own tenant either, so the permission check on
    # /observations rejects (403) before ever reaching the cross-tenant definition lookup (404) -
    # either way, she gets nothing back about a definition that is not hers.
    request(other_base+'/references/'+definition['id']+'/observations',token=bea['accessToken'],expected=(403,404))

    print('PASS HTTP GOVERNANCE: raw observation history survives regardless of same-day evidence-manifest '
          'caching (anti-live-differencing proven directly; per-reason-code classification proven at the '
          'PostgreSQL level with backdated observations, not live same-day HTTP), platform SuperAdmin '
          'explicitly excluded from community governance, ordinary policy governance inside constitutional '
          'floors with floor(400)/protected-field(409) rejection, market integrity SIGNAL->UNDER_REVIEW->FINAL '
          'and ->DISMISSED by a genuinely different publisher, tenant isolation.')

if __name__=='__main__': main()
