"""Local-only HTTP proof of Seven Keys; uses disposable tenants and real Ed25519 signatures."""
import base64
import hashlib
import json
import uuid
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from community_references_e2e import fixtures
from smoke import request

DOMAIN='STIR:MARKET:CONSTITUTION:V1'
GUARDIAN='STIR:MARKET:GUARDIAN:V1'
POSSESSION='STIR:MARKET:POSSESSION:V1'
BOOTSTRAP='STIR:MARKET:BOOTSTRAP:V1'

def canonical(value):
    # This fixture uses ASCII strings, UUIDs, booleans and integers; this is JCS for those values.
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()

def digest(value): return hashlib.sha256(canonical(value)).hexdigest()
def rawkey(key): return base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode().rstrip('=')
def sign(key,domain,payload):
    return base64.urlsafe_b64encode(key.sign(domain.encode()+b'\x00'+canonical(payload))).decode().rstrip('=')

def main():
    tenant,base,ana,pedro,carlos,bea,binding=fixtures()
    token=ana['accessToken']; community=binding['communityId']; authority=str(uuid.uuid4())
    api=base+'/references/governance'
    keys=[Ed25519PrivateKey.generate() for _ in range(7)]
    guardian=Ed25519PrivateKey.generate()
    credentials=[str(uuid.uuid4()) for _ in keys]
    controllers=[str(uuid.uuid4()) for _ in keys]
    guardian_id=str(uuid.uuid4())
    public_seats=[dict(ordinal=i+1,controllerId=controllers[i],credentialId=credentials[i],publicKey=rawkey(keys[i])) for i in range(7)]
    constitution=dict(schema='STIR-MARKET-CONSTITUTION-1',provenanceRequired=True,historyImmutable=True,
        independenceChecksRequired=True,concentrationChecksRequired=True,minimumObservationFloor=5,
        minimumParticipantFloor=6,maximumParticipantShareCeiling='0.50',guardianMayGovern=False,constitutionalThreshold=7)
    bootstrap=dict(format='STIR-SEVEN-KEYS-BOOTSTRAP-1',tenantId=tenant,communityId=community,authorityId=authority,
        seats=public_seats,guardianCredentialId=guardian_id,guardianPublicKey=rawkey(guardian),constitutionDigest=digest(constitution))
    seats=[{**seat,'possessionSignature':sign(keys[i],BOOTSTRAP,bootstrap)} for i,seat in enumerate(public_seats)]
    creation=dict(authorityId=authority,communityId=community,seats=seats,
        guardianCredentialId=guardian_id,guardianPublicKey=rawkey(guardian),guardianPossessionSignature=sign(guardian,BOOTSTRAP,bootstrap))
    # Bootstrap signs the unsigned seat list, not the possession signatures themselves.
    view=request(api+'/bootstrap','POST',creation,token)
    assert view['threshold']==7 and len(view['seats'])==7
    definition=request(base+'/references','POST',dict(name='Integrity service',scope='One hour of service',
        attributes={},quantityBasis='1',quantityUnit='hour'),token)
    policy=dict(windowDays=90,minimumObservations=8,minimumParticipants=6,
        maximumParticipantShare='0.40',freshnessDays=30,explanation='Ordinary bounded adjustment')
    adjusted=request(base+'/references/'+definition['id']+'/policies','POST',policy,token)
    assert adjusted['version']==2
    request(base+'/references/'+definition['id']+'/policies','POST',
        {**policy,'independenceChecksRequired':False},token,409)

    def propose(action,after,fields,reason='Explicit community decision'):
        return request(api+'/'+community+'/proposals','POST',dict(proposalId=str(uuid.uuid4()),actionType=action,
            after=after,affectedFields=fields,reason=reason,evidenceRefs=['assembly-record-1']),token)
    def sign_seat(p,seat,key=None,credential=None):
        payload=json.loads(p['payloadJson']);i=seat-1
        return request(api+'/proposals/'+p['id']+'/signatures','POST',dict(seatOrdinal=seat,
            credentialId=credential or credentials[i],signatureBase64url=sign(key or keys[i],DOMAIN,payload)),token)
    def execute(p,guardian_key=None,new_key=None,expected=200):
        payload=json.loads(p['payloadJson'])
        body=dict(guardianSignature=sign(guardian_key,GUARDIAN,payload) if guardian_key else None,
            newKeyPossessionSignature=sign(new_key,POSSESSION,payload) if new_key else None)
        return request(api+'/proposals/'+p['id']+'/activate','POST',body,token,expected)

    changed={**constitution,'independenceChecksRequired':False}
    p=propose('AMEND_CONSTITUTION',changed,['independenceChecksRequired'])
    for seat in range(1,7):sign_seat(p,seat)
    execute(p,expected=409)
    assert request(api+'/proposals/'+p['id'],token=pedro['accessToken'])['signatureCount']==6
    sign_seat(p,7);execute(p)
    assert request(api+'/'+community,token=pedro['accessToken'])['constitutionVersion']==2

    sequence=request(api+'/'+community,token=token)['nextSequence']
    from datetime import datetime,timezone
    declared=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    suspension=dict(format='STIR-KEY-SUSPENSION-1',tenantId=tenant,communityId=community,authorityId=authority,
        seat=4,credentialId=credentials[3],reasonCode='KEY_COMPROMISED',evidenceRefs=['incident-1'],
        sequence=sequence,declaredAt=declared)
    body=dict(seatOrdinal=4,credentialId=credentials[3],expectedSequence=sequence,declaredAt=declared,
        reasonCode='KEY_COMPROMISED',evidenceRefs=['incident-1'],guardianSignature=sign(guardian,GUARDIAN,suspension))
    request(api+'/'+community+'/emergency-suspensions','POST',body,token)
    request(api+'/'+community+'/emergency-suspensions','POST',{**body,'seatOrdinal':5},token,409)
    frozen=propose('AMEND_CONSTITUTION',{**changed,'concentrationChecksRequired':False},['concentrationChecksRequired'])
    execute(frozen,expected=409)

    replacement_key=Ed25519PrivateKey.generate()
    replacement=propose('REPLACE_CONTROLLER',dict(affectedSeat=4,oldCredentialId=credentials[3],
        newCredentialId=str(uuid.uuid4()),newPublicKey=rawkey(replacement_key),controllerId=str(uuid.uuid4()),
        finalResolutionId=str(uuid.uuid4()),finalResolutionDigest='0'*64,reason='Proposed controller replacement'),
        ['controllerId','credentialId'])
    execute(replacement,guardian,replacement_key,409)

    newkey=Ed25519PrivateKey.generate();newcred=str(uuid.uuid4())
    recovery=dict(affectedSeat=4,oldCredentialId=credentials[3],newCredentialId=newcred,newPublicKey=rawkey(newkey),
        controllerId=controllers[3],continuityEvidenceRefs=['continuity-1'],reason='Same controller credential rotation')
    r=propose('ROTATE_CREDENTIAL',recovery,['credentialId'])
    for seat in [1,2,3,5,6]:sign_seat(r,seat)
    execute(r,guardian,newkey,409)
    sign_seat(r,7);execute(r,guardian,newkey)
    seats_after=request(api+'/'+community,token=token)['seats']
    assert seats_after[3]['credential_id']==newcred and seats_after[3]['status']=='ACTIVE'
    assert any(item['status']=='REVOKED' for item in request(api+'/'+community+'/credentials',token=token))
    request(api+'/proposals/'+frozen['id']+'/signatures','POST',dict(seatOrdinal=4,credentialId=credentials[3],
        signatureBase64url=sign(keys[3],DOMAIN,json.loads(frozen['payloadJson']))),token,409)

    removal=propose('REMOVE_GUARDIAN',{'guardianCredentialId':guardian_id,'status':'REMOVED'},['guardianStatus'])
    for seat in [1,2,3,5,6]:sign_seat(removal,seat)
    execute(removal)
    assert request(api+'/'+community,token=token)['guardianStatus']=='REMOVED'
    request(api+'/'+community+'/emergency-suspensions','POST',body,token,409)
    events=request(api+'/'+community+'/events',token=pedro['accessToken'])
    assert events and all('payload_json' not in event for event in events)
    assert request(api+'/'+community+'/audit',token=pedro['accessToken'])['valid'] is True
    other_base=f"/api/stir/tenants/{bea['tenants'][0]['id']}/references/governance"
    request(other_base+'/'+community,token=bea['accessToken'],expected=404)
    print('PASS HTTP SEVEN KEYS: 6/7 blocked, 7/7 activated, one guardian suspension, constitutional freeze, 6/6 recovery, old credential history, 5/7 guardian removal, tenant isolation and redaction.')

if __name__=='__main__':main()
