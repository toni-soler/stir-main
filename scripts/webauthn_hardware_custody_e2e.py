"""Local-only HTTP proof of WebAuthn/hardware-backed Seven Keys credentials
(WEBAUTHN_HARDWARE_CUSTODY.md): a real WebAuthn registration/assertion ceremony is simulated
end to end (ES256 keys, real CBOR/authenticatorData/attestationObject bytes, real ECDSA
signatures over authenticatorData||SHA-256(clientDataJSON)) exactly as a browser's WebAuthn API
and a real authenticator would produce, verified by the real backend over real HTTP. No
attestation trust chain is checked (attestation format "none", by policy - see the design doc),
so this script never needs to fake one. Disposable tenants, real signatures - never point this
at production. Complements market_integrity_e2e.py's pure-Ed25519 Seven Keys proof; this script
never re-proves what that one already covers (6-of-7 freeze, Guardian-cannot-substitute-a-seat,
REPLACE_CONTROLLER fail-closed, redaction, tenant isolation) except where a WebAuthn credential
changes the scenario.
"""
import base64
import hashlib
import json
import uuid
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.primitives import hashes
from community_references_e2e import fixtures
from smoke import request

DOMAIN='STIR:MARKET:CONSTITUTION:V1'
GUARDIAN='STIR:MARKET:GUARDIAN:V1'
POSSESSION='STIR:MARKET:POSSESSION:V1'
BOOTSTRAP='STIR:MARKET:BOOTSTRAP:V1'
RP_ID='localhost'
ORIGIN='http://localhost:8089'

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()
def rawkey(key): return base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode().rstrip('=')
def sign_ed(key,domain,payload): return base64.urlsafe_b64encode(key.sign(domain.encode()+b'\x00'+canonical(payload))).decode().rstrip('=')
def b64url(b): return base64.urlsafe_b64encode(b).decode().rstrip('=')
def b64url_decode(s): return base64.urlsafe_b64decode(s+'='*(-len(s)%4))

# --- A minimal, purpose-built CBOR encoder for exactly the structures a WebAuthn ceremony needs
# (a COSE_Key map, an attestationObject map) - the same narrow scope as WebAuthnCrypto.java's own
# decoder, so this script never needs a third-party CBOR dependency just for local E2E fixtures. ---
def cbor_uint(major,n):
    if n<24: return bytes([(major<<5)|n])
    if n<256: return bytes([(major<<5)|24,n])
    if n<65536: return bytes([(major<<5)|25])+n.to_bytes(2,'big')
    return bytes([(major<<5)|26])+n.to_bytes(4,'big')
def cbor_int(n): return cbor_uint(0,n) if n>=0 else cbor_uint(1,-n-1)
def cbor_bytes(b): return cbor_uint(2,len(b))+b
def cbor_text(s):
    e=s.encode('utf-8'); return cbor_uint(3,len(e))+e
def cbor_map(pairs):
    out=cbor_uint(5,len(pairs))
    for k,v in pairs: out+=k+v
    return out

def cose_key_es256(public_key):
    numbers=public_key.public_numbers()
    x=numbers.x.to_bytes(32,'big'); y=numbers.y.to_bytes(32,'big')
    return cbor_map([(cbor_int(1),cbor_int(2)),(cbor_int(3),cbor_int(-7)),(cbor_int(-1),cbor_int(1)),
        (cbor_int(-2),cbor_bytes(x)),(cbor_int(-3),cbor_bytes(y))])

def authenticator_data(rp_id,sign_count,credential_id=None,cose_key=None,uv=True,at=False):
    out=hashlib.sha256(rp_id.encode()).digest()
    flags=0x01|(0x04 if uv else 0)|(0x40 if at else 0)
    out+=bytes([flags])+sign_count.to_bytes(4,'big')
    if at: out+=b'\x00'*16+len(credential_id).to_bytes(2,'big')+credential_id+cose_key
    return out

def attestation_object(auth_data):
    return cbor_map([(cbor_text('fmt'),cbor_text('none')),(cbor_text('attStmt'),cbor_map([])),(cbor_text('authData'),cbor_bytes(auth_data))])

def client_data_json(type_,challenge_bytes,origin=ORIGIN):
    return json.dumps({'type':type_,'challenge':b64url(challenge_bytes),'origin':origin}).encode()

def sign_es256(private_key,signed_bytes): return private_key.sign(signed_bytes,ec.ECDSA(hashes.SHA256()))

def webauthn_registration(private_key,credential_id,challenge,uv=True):
    """Simulates navigator.credentials.create() end to end: real CBOR/authenticatorData bytes,
    attestation format "none", returned in exactly the shape the backend's finish-registration
    endpoint expects."""
    cose_key=cose_key_es256(private_key.public_key())
    auth_data=authenticator_data(RP_ID,0,credential_id,cose_key,uv,at=True)
    return dict(attestationObject=b64url(attestation_object(auth_data)),
        clientDataJson=b64url(client_data_json('webauthn.create',challenge)),
        webauthnCredentialId=b64url(credential_id),userVerificationRequired=uv)

def webauthn_assertion(private_key,sign_count,message,rp_id=RP_ID,origin=ORIGIN,uv=True):
    """Simulates navigator.credentials.get() end to end - the challenge is base64url(SHA-256(the
    exact domain-separated message)), exactly what a real browser client would compute before
    invoking the authenticator, and what WebAuthnCrypto.java independently recomputes to verify."""
    challenge=hashlib.sha256(message).digest()
    auth_data=authenticator_data(rp_id,sign_count,uv=uv,at=False)
    client_data=client_data_json('webauthn.get',challenge,origin)
    signed=auth_data+hashlib.sha256(client_data).digest()
    signature=sign_es256(private_key,signed)
    return dict(credentialType='WEBAUTHN',algorithm='ES256',signature=b64url(signature),
        clientDataJson=b64url(client_data),authenticatorData=b64url(auth_data))

def main():
    tenant,base,ana,pedro,carlos,bea,binding=fixtures()
    token=ana['accessToken']; community=binding['communityId']; authority=str(uuid.uuid4())
    api=base+'/references/governance'

    # Seats 1-6 stay SOFTWARE_ED25519 (the existing path, proving it keeps working unchanged
    # during the transition); seat 7 and the Guardian register real WebAuthn credentials.
    keys=[Ed25519PrivateKey.generate() for _ in range(6)]
    credentials=[str(uuid.uuid4()) for _ in range(7)]
    controllers=[str(uuid.uuid4()) for _ in range(7)]
    webauthn_seat_key=ec.generate_private_key(ec.SECP256R1())
    webauthn_seat_credential_id=b'seat7-credential'
    guardian_id=str(uuid.uuid4())
    guardian_key=ec.generate_private_key(ec.SECP256R1())
    guardian_credential_id=b'guardian-credential'

    # --- 1. Register WebAuthn credentials through the real begin/finish endpoints ---
    def register(context,credential_id_uuid,private_key,webauthn_cred_id):
        options=request(api+'/webauthn/register/begin','POST',dict(authorityId=authority,context=context),token)
        assert options['rpId']==RP_ID
        challenge=b64url_decode(options['challenge'])
        registration=webauthn_registration(private_key,webauthn_cred_id,challenge)
        result=request(api+'/webauthn/register/finish','POST',
            dict(authorityId=authority,context=context,credentialId=credential_id_uuid,**registration),token)
        assert result['algorithm']=='ES256'
        return result['publicKeyBase64url']
    seat7_public_key=register('seat-7',credentials[6],webauthn_seat_key,webauthn_seat_credential_id)
    guardian_public_key=register('guardian',guardian_id,guardian_key,guardian_credential_id)

    public_seats=[dict(ordinal=i+1,controllerId=controllers[i],credentialId=credentials[i],publicKey=rawkey(keys[i])) for i in range(6)]
    public_seats.append(dict(ordinal=7,controllerId=controllers[6],credentialId=credentials[6],publicKey=seat7_public_key))
    constitution=dict(schema='STIR-MARKET-CONSTITUTION-1',provenanceRequired=True,historyImmutable=True,
        independenceChecksRequired=True,concentrationChecksRequired=True,minimumObservationFloor=5,
        minimumParticipantFloor=6,maximumParticipantShareCeiling='0.50',guardianMayGovern=False,constitutionalThreshold=7)
    bootstrap_message=dict(format='STIR-SEVEN-KEYS-BOOTSTRAP-1',tenantId=tenant,communityId=community,authorityId=authority,
        seats=public_seats,guardianCredentialId=guardian_id,guardianPublicKey=guardian_public_key,constitutionDigest=digest(constitution))
    bootstrap_bytes=BOOTSTRAP.encode()+b'\x00'+canonical(bootstrap_message)

    # --- 2. Proof-of-possession for bootstrap: seats 1-6 sign Ed25519, seat 7 and Guardian sign WebAuthn ---
    seats=[{**public_seats[i],'possessionSignature':sign_ed(keys[i],BOOTSTRAP,bootstrap_message)} for i in range(6)]
    seats.append({**public_seats[6],'credentialType':'WEBAUTHN','algorithm':'ES256',
        'possessionEnvelope':webauthn_assertion(webauthn_seat_key,0,bootstrap_bytes)})
    creation=dict(authorityId=authority,communityId=community,seats=seats,
        guardianCredentialId=guardian_id,guardianPublicKey=guardian_public_key,
        guardianCredentialType='WEBAUTHN',guardianAlgorithm='ES256',
        guardianPossessionEnvelope=webauthn_assertion(guardian_key,0,bootstrap_bytes))
    view=request(api+'/bootstrap','POST',creation,token)
    assert view['threshold']==7 and len(view['seats'])==7
    assert view['seats'][6]['credential_type']=='WEBAUTHN'
    print('PASS: mixed-credential bootstrap (6 SOFTWARE_ED25519 seats + 1 WEBAUTHN seat + WEBAUTHN Guardian)')

    def propose(action,after,fields,reason='Explicit community decision'):
        return request(api+'/'+community+'/proposals','POST',dict(proposalId=str(uuid.uuid4()),actionType=action,
            after=after,affectedFields=fields,reason=reason,evidenceRefs=['assembly-record-1']),token)
    def sign_seat_ed(p,seat):
        payload=json.loads(p['payloadJson'])
        return request(api+'/proposals/'+p['id']+'/signatures','POST',dict(seatOrdinal=seat,
            credentialId=credentials[seat-1],signatureBase64url=sign_ed(keys[seat-1],DOMAIN,payload)),token)
    webauthn_sign_count=[0] # seat 7's authenticator counter, advances with each real use
    def sign_seat7_webauthn(p,expected=200):
        payload=json.loads(p['payloadJson'])
        webauthn_sign_count[0]+=1
        message=DOMAIN.encode()+b'\x00'+canonical(payload)
        envelope=webauthn_assertion(webauthn_seat_key,webauthn_sign_count[0],message)
        return request(api+'/proposals/'+p['id']+'/signatures','POST',
            dict(seatOrdinal=7,credentialId=credentials[6],credentialEnvelope=envelope),token,expected)

    # --- 3. A valid constitutional signature from the WebAuthn seat ---
    changed={**constitution,'independenceChecksRequired':False}
    p=propose('AMEND_CONSTITUTION',changed,['independenceChecksRequired'])
    for seat in range(1,7):sign_seat_ed(p,seat)
    sign_seat7_webauthn(p)
    activated=request(api+'/proposals/'+p['id']+'/activate','POST',{},token)
    assert activated['state']=='ACTIVATED'
    assert request(api+'/'+community,token=token)['constitutionVersion']==2
    print('PASS: WebAuthn seat produced a valid constitutional signature that activated a real amendment')

    # --- 4/5. A captured assertion cannot sign a second time (already-signed dedup) or a
    # different proposal (challenge is bound to that exact payload's digest) ---
    p2=propose('AMEND_CONSTITUTION',{**changed,'concentrationChecksRequired':False},['concentrationChecksRequired'])
    for seat in range(1,7):sign_seat_ed(p2,seat)
    payload2=json.loads(request(api+'/proposals/'+p2['id'],token=token)['payloadJson'])
    message2=DOMAIN.encode()+b'\x00'+canonical(payload2)
    webauthn_sign_count[0]+=1
    stale_envelope=webauthn_assertion(webauthn_seat_key,webauthn_sign_count[0],message2)
    request(api+'/proposals/'+p2['id']+'/signatures','POST',
        dict(seatOrdinal=7,credentialId=credentials[6],credentialEnvelope=stale_envelope),token)
    # Replaying the exact same HTTP body again must fail - the seat already signed this proposal.
    request(api+'/proposals/'+p2['id']+'/signatures','POST',
        dict(seatOrdinal=7,credentialId=credentials[6],credentialEnvelope=stale_envelope),token,409)
    # The same captured envelope cannot be presented as a signature for a THIRD, different proposal.
    p3=propose('AMEND_CONSTITUTION',{**changed,'minimumObservationFloor':6},['minimumObservationFloor'])
    request(api+'/proposals/'+p3['id']+'/signatures','POST',
        dict(seatOrdinal=7,credentialId=credentials[6],credentialEnvelope=stale_envelope),token,400)
    print('PASS: a WebAuthn signature cannot be replayed for a second proposal, and double-submission is rejected')

    # --- 6/7. Cross-community and cross-tenant replay: a real assertion computed for THIS
    # community/tenant's exact payload cannot verify against a forged payload naming another one ---
    payload3=json.loads(request(api+'/proposals/'+p3['id'],token=token)['payloadJson'])
    forged_community=dict(payload3, communityId=str(uuid.uuid4()))
    forged_message=DOMAIN.encode()+b'\x00'+canonical(forged_community)
    webauthn_sign_count[0]+=1
    cross_community_envelope=webauthn_assertion(webauthn_seat_key,webauthn_sign_count[0],forged_message)
    request(api+'/proposals/'+p3['id']+'/signatures','POST',
        dict(seatOrdinal=7,credentialId=credentials[6],credentialEnvelope=cross_community_envelope),token,400)
    forged_tenant=dict(payload3, tenantId=str(uuid.uuid4()))
    forged_tenant_message=DOMAIN.encode()+b'\x00'+canonical(forged_tenant)
    webauthn_sign_count[0]+=1
    cross_tenant_envelope=webauthn_assertion(webauthn_seat_key,webauthn_sign_count[0],forged_tenant_message)
    request(api+'/proposals/'+p3['id']+'/signatures','POST',
        dict(seatOrdinal=7,credentialId=credentials[6],credentialEnvelope=cross_tenant_envelope),token,400)
    print('PASS: cross-community and cross-tenant replay of a WebAuthn assertion rejected')

    # --- 8. A revoked/suspended WebAuthn credential cannot sign ---
    sequence=request(api+'/'+community,token=token)['nextSequence']
    from datetime import datetime,timezone
    declared=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    suspension=dict(format='STIR-KEY-SUSPENSION-1',tenantId=tenant,communityId=community,authorityId=authority,
        seat=7,credentialId=credentials[6],reasonCode='KEY_LOST',evidenceRefs=['incident-1'],
        sequence=sequence,declaredAt=declared)
    suspend_message=GUARDIAN.encode()+b'\x00'+canonical(suspension)
    guardian_sign_count=[1] # Guardian's authenticator counter (already advanced once at bootstrap)
    guardian_sign_count[0]+=1
    suspend_body=dict(seatOrdinal=7,credentialId=credentials[6],expectedSequence=sequence,declaredAt=declared,
        reasonCode='KEY_LOST',evidenceRefs=['incident-1'],
        guardianEnvelope=webauthn_assertion(guardian_key,guardian_sign_count[0],suspend_message))
    request(api+'/'+community+'/emergency-suspensions','POST',suspend_body,token)
    request(api+'/proposals/'+p3['id']+'/signatures','POST',
        dict(seatOrdinal=7,credentialId=credentials[6],credentialEnvelope=webauthn_assertion(webauthn_seat_key,99,DOMAIN.encode()+b'\x00'+canonical(payload3))),
        token,409)
    print('PASS: Guardian suspended the WebAuthn seat (Guardian itself signing via WebAuthn); the suspended credential can no longer sign')

    # --- 10/11/13. Same-controller recovery: SOFTWARE_ED25519 seat 1 -> WEBAUTHN, and separately
    # WEBAUTHN seat 7 -> a brand NEW WebAuthn credential, both preserving their controller ---
    seq2=request(api+'/'+community,token=token)['nextSequence']
    declared2=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    suspension1=dict(format='STIR-KEY-SUSPENSION-1',tenantId=tenant,communityId=community,authorityId=authority,
        seat=1,credentialId=credentials[0],reasonCode='MOVE_TO_HARDWARE',evidenceRefs=['migration-1'],
        sequence=seq2,declaredAt=declared2)
    guardian_sign_count[0]+=1
    request(api+'/'+community+'/emergency-suspensions','POST',dict(seatOrdinal=1,credentialId=credentials[0],
        expectedSequence=seq2,declaredAt=declared2,reasonCode='MOVE_TO_HARDWARE',evidenceRefs=['migration-1'],
        guardianEnvelope=webauthn_assertion(guardian_key,guardian_sign_count[0],GUARDIAN.encode()+b'\x00'+canonical(suspension1))),token)

    new_seat1_key=ec.generate_private_key(ec.SECP256R1()); new_seat1_cred_id=b'seat1-new-hardware'
    new_seat1_credential=str(uuid.uuid4())
    new_seat1_public_key=register('seat-1-incoming',new_seat1_credential,new_seat1_key,new_seat1_cred_id)
    rotate1=propose('ROTATE_CREDENTIAL',dict(affectedSeat=1,oldCredentialId=credentials[0],newCredentialId=new_seat1_credential,
        newPublicKey=new_seat1_public_key,controllerId=controllers[0],continuityEvidenceRefs=['continuity-1'],
        reason='Software to hardware migration',credentialType='WEBAUTHN',algorithm='ES256'),
        ['affectedSeat','oldCredentialId','newCredentialId','newPublicKey','controllerId','continuityEvidenceRefs','reason'])
    for seat in [2,3,4,5,6]: sign_seat_ed(rotate1,seat)
    sign_seat7_webauthn(rotate1)
    payload_r1=json.loads(request(api+'/proposals/'+rotate1['id'],token=token)['payloadJson'])
    r1_message=DOMAIN.encode()+b'\x00'+canonical(payload_r1)
    guardian_sign_count[0]+=1
    exec1=dict(guardianEnvelope=webauthn_assertion(guardian_key,guardian_sign_count[0],GUARDIAN.encode()+b'\x00'+canonical(payload_r1)),
        newKeyPossessionEnvelope=webauthn_assertion(new_seat1_key,0,POSSESSION.encode()+b'\x00'+canonical(payload_r1)))
    activated1=request(api+'/proposals/'+rotate1['id']+'/activate','POST',exec1,token)
    assert activated1['state']=='ACTIVATED'
    seats_after=request(api+'/'+community,token=token)['seats']
    assert seats_after[0]['credential_id']==new_seat1_credential and seats_after[0]['credential_type']=='WEBAUTHN'
    assert seats_after[0]['controller_id']==controllers[0], 'same controller - only the credential changed'
    print('PASS: same-controller rotation SOFTWARE_ED25519 -> WEBAUTHN activated, controller preserved')

    # WebAuthn seat 7 -> a new WebAuthn credential (WEBAUTHN -> WEBAUTHN rotation)
    seq3=request(api+'/'+community,token=token)['nextSequence']
    declared3=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    suspension7=dict(format='STIR-KEY-SUSPENSION-1',tenantId=tenant,communityId=community,authorityId=authority,
        seat=7,credentialId=credentials[6],reasonCode='ROTATE_HARDWARE_KEY',evidenceRefs=['migration-2'],
        sequence=seq3,declaredAt=declared3)
    guardian_sign_count[0]+=1
    request(api+'/'+community+'/emergency-suspensions','POST',dict(seatOrdinal=7,credentialId=credentials[6],
        expectedSequence=seq3,declaredAt=declared3,reasonCode='ROTATE_HARDWARE_KEY',evidenceRefs=['migration-2'],
        guardianEnvelope=webauthn_assertion(guardian_key,guardian_sign_count[0],GUARDIAN.encode()+b'\x00'+canonical(suspension7))),token)
    new_seat7_key=ec.generate_private_key(ec.SECP256R1()); new_seat7_cred_id=b'seat7-new-hardware'
    new_seat7_credential=str(uuid.uuid4())
    new_seat7_public_key=register('seat-7-incoming',new_seat7_credential,new_seat7_key,new_seat7_cred_id)
    rotate7=propose('ROTATE_CREDENTIAL',dict(affectedSeat=7,oldCredentialId=credentials[6],newCredentialId=new_seat7_credential,
        newPublicKey=new_seat7_public_key,controllerId=controllers[6],continuityEvidenceRefs=['continuity-2'],
        reason='Hardware key rotation',credentialType='WEBAUTHN',algorithm='ES256'),
        ['affectedSeat','oldCredentialId','newCredentialId','newPublicKey','controllerId','continuityEvidenceRefs','reason'])
    for seat in [2,3,4,5,6]: sign_seat_ed(rotate7,seat)
    new_seat1_sign_count=[0]
    payload_r1b=json.loads(request(api+'/proposals/'+rotate7['id'],token=token)['payloadJson'])
    new_seat1_sign_count[0]+=1
    request(api+'/proposals/'+rotate7['id']+'/signatures','POST',dict(seatOrdinal=1,credentialId=new_seat1_credential,
        credentialEnvelope=webauthn_assertion(new_seat1_key,new_seat1_sign_count[0],DOMAIN.encode()+b'\x00'+canonical(payload_r1b))),token)
    payload_r7=json.loads(request(api+'/proposals/'+rotate7['id'],token=token)['payloadJson'])
    guardian_sign_count[0]+=1
    exec7=dict(guardianEnvelope=webauthn_assertion(guardian_key,guardian_sign_count[0],GUARDIAN.encode()+b'\x00'+canonical(payload_r7)),
        newKeyPossessionEnvelope=webauthn_assertion(new_seat7_key,0,POSSESSION.encode()+b'\x00'+canonical(payload_r7)))
    activated7=request(api+'/proposals/'+rotate7['id']+'/activate','POST',exec7,token)
    assert activated7['state']=='ACTIVATED'
    seats_final=request(api+'/'+community,token=token)['seats']
    assert seats_final[6]['credential_id']==new_seat7_credential and seats_final[6]['controller_id']==controllers[6]
    print('PASS: WEBAUTHN -> WEBAUTHN rotation activated, controller preserved')

    # --- 9. The prior (now-revoked) credential remains verifiable in history ---
    history=request(api+'/'+community+'/credentials',token=token)
    assert any(h['status']=='REVOKED' and h['credential_type']=='SOFTWARE_ED25519' and h['credential_id']==credentials[0] for h in history)
    assert any(h['status']=='REVOKED' and h['credential_type']=='WEBAUTHN' and h['credential_id']==credentials[6] for h in history)
    assert any(h['status']=='ACTIVE' and h['credential_type']=='WEBAUTHN' and h['credential_id']==new_seat1_credential for h in history)
    print('PASS: revoked credentials (both SOFTWARE_ED25519 and WEBAUTHN) remain in history with their real type')

    # --- 15. Still 6-of-7 cannot execute a constitutional action (unaffected by credential type) ---
    frozen_check=propose('AMEND_CONSTITUTION',{**changed,'minimumParticipantFloor':7},['minimumParticipantFloor'])
    for seat in [2,3,4,5,6]: sign_seat_ed(frozen_check,seat)
    request(api+'/proposals/'+frozen_check['id']+'/signatures','POST',dict(seatOrdinal=1,credentialId=new_seat1_credential,
        credentialEnvelope=webauthn_assertion(new_seat1_key,new_seat1_sign_count[0]+1,DOMAIN.encode()+b'\x00'+canonical(
            json.loads(request(api+'/proposals/'+frozen_check['id'],token=token)['payloadJson'])))),token)
    request(api+'/proposals/'+frozen_check['id']+'/activate','POST',{},token,409)
    print('PASS: 6-of-7 still cannot activate a constitutional action, regardless of credential type mix')

    other_base=f"/api/stir/tenants/{bea['tenants'][0]['id']}/references/governance"
    request(other_base+'/'+community,token=bea['accessToken'],expected=404)
    print('PASS: tenant isolation holds for WebAuthn credentials exactly as for Ed25519 ones')

    print('PASS HTTP WEBAUTHN HARDWARE CUSTODY: registration, proof-of-possession, valid constitutional '
          'signature, replay/cross-proposal/cross-community/cross-tenant rejection, suspended credential '
          'cannot sign, prior credential remains historically verifiable, software->WebAuthn and '
          'WebAuthn->WebAuthn same-controller rotation both preserve the controller, 6-of-7 still cannot '
          'activate, tenant isolation holds - the existing Ed25519 path kept working unchanged throughout.')

if __name__=='__main__':main()
