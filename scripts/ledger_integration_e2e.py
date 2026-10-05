"""Controlled DEV transaction through the normal product path, for the IDAX Ledger integration proof.

STIR (Agreement) -> osTRIS (EXCHANGE commit, COMMITTED journal) -> protocol proof outbox -> IDAX Ledger -> XRPL.

Runs against a deployment's own demo tenant over the public HTTP API only (no database writes, so it is allowed on
stir-dev). Two real participants negotiate, both sign the trade with their own Ed25519 keys, and the owner commits it.
Prints only identifiers. The ledger side is read from the database by a separate evidence step.

Environment: STIR_TEST_URL (proxy base, e.g. http://127.0.0.1:28089), STIR_LEDGER_SECRETS (directory with login_password),
STIR_LEDGER_TENANT_CODE (optional - run against a specific platform tenant by its `code`, resolved through the
superuser platform tenant listing, instead of the admin login's own default tenant. Needed because the
ostris-ledger-delivery service-principal grant is scoped to one specific tenant: a transaction committed in any
other tenant can never be delivered, by design - ServiceTokenIssuer.issue() requires the grant's tenant_id to match
the claim's own tenant_id exactly).
"""
import os
import uuid
from pathlib import Path
from smoke import request
from marketplace_e2e import PERMISSIONS
from economic_exchange_e2e import make_user, activate_economic, open_and_accept, run_exchange
import secrets

SECRETS = Path(os.environ.get('STIR_LEDGER_SECRETS', Path(__file__).resolve().parents[1] / '.local/secrets'))


def main():
    admin_session = request('/api/shell/v1/auth/login', 'POST', {'email': 'admin@stir.test',
                            'password': (SECRETS / 'login_password').read_text().strip()})
    admin = admin_session['accessToken']
    tenant_code = os.environ.get('STIR_LEDGER_TENANT_CODE')
    if tenant_code:
        platform_tenants = request('/api/shell/v1/platform/tenants', token=admin)
        matches = [t for t in platform_tenants if t.get('code') == tenant_code]
        if not matches:
            raise SystemExit(f'No platform tenant with code {tenant_code!r} - it must already exist '
                              '(this script never creates the tenant itself, only an admin user inside it).')
        tenant = matches[0]['id']
    else:
        tenant = admin_session['tenants'][0]['id']
    base = f'/api/stir/tenants/{tenant}'
    suffix = uuid.uuid4().hex[:8]
    role = request(f'/api/shell/v1/tenants/{tenant}/roles', 'POST', {'key': 'ledger_e2e_' + suffix, 'name': 'Ledger integration E2E',
                   'description': 'Release fixture', 'enabled': True}, admin, expected=(200, 201))
    request(f'/api/shell/v1/tenants/{tenant}/roles/' + role['id'] + '/permissions', 'PUT', PERMISSIONS, admin)
    # Community bootstrap authority is tenant owner/admin (not the platform SuperAdmin, which has no DML under V17).
    # Ana is therefore created as a tenant administrator through the shell API, with the same permission role as Pedro.
    ana_email = f'ana-{suffix}@stir.test'
    ana_password = secrets.token_urlsafe(24)
    request(f'/api/shell/v1/tenants/{tenant}/users', 'POST', {'email': ana_email, 'displayName': 'ana', 'authProvider': 'local',
            'subject': ana_email, 'password': ana_password, 'role': 'admin', 'enabled': True}, admin, expected=(200, 201))
    ana_login = request('/api/shell/v1/auth/login', 'POST', {'email': ana_email, 'password': ana_password})
    request(f'/api/shell/v1/tenants/{tenant}/roles/users/' + ana_login['user']['id'], 'PUT', {'roleIds': [role['id']]}, admin, 204)
    ana = request('/api/shell/v1/auth/login', 'POST', {'email': ana_email, 'password': ana_password})
    # Economic activation is also gated by tenant owner/admin authority in this build (a plain member with the same permissions
    # receives 403), so both participants of this controlled transaction are tenant administrators.
    pedro_email = f'pedro-{suffix}@stir.test'
    pedro_password = secrets.token_urlsafe(24)
    request(f'/api/shell/v1/tenants/{tenant}/users', 'POST', {'email': pedro_email, 'displayName': 'pedro', 'authProvider': 'local',
            'subject': pedro_email, 'password': pedro_password, 'role': 'admin', 'enabled': True}, admin, expected=(200, 201))
    pedro_login = request('/api/shell/v1/auth/login', 'POST', {'email': pedro_email, 'password': pedro_password})
    request(f'/api/shell/v1/tenants/{tenant}/roles/users/' + pedro_login['user']['id'], 'PUT', {'roleIds': [role['id']]}, admin, 204)
    pedro = request('/api/shell/v1/auth/login', 'POST', {'email': pedro_email, 'password': pedro_password})
    request(base + '/participants/me', 'PUT', {'displayName': 'Ana ' + suffix, 'bio': 'Ledger E2E', 'location': 'Girona'}, ana['accessToken'])
    request(base + '/participants/me', 'PUT', {'displayName': 'Pedro ' + suffix, 'bio': 'Ledger E2E', 'location': 'Girona'}, pedro['accessToken'])
    request(base + '/economic/marketplace/bootstrap', 'POST', {'communityName': 'Ledger E2E ' + suffix, 'unitCode': 'LED', 'unitScale': 0},
            ana['accessToken'], expected=(200, 201, 409))

    ana_key = activate_economic(base, ana)
    pedro_key = activate_economic(base, pedro)
    agreement, amount = open_and_accept(base, ana, pedro, 'OFFER', 'Ledger E2E ' + suffix, 'home', 10, None)
    agreement_id = agreement['id']
    result = run_exchange(base, pedro, ana, agreement_id, pedro_key, ana_key)

    print('STIR_AGREEMENT_ID', agreement_id)
    print('AGREED_AMOUNT', amount)
    print('TENANT_ID', tenant)
    for key in ('transactionId', 'journalTransactionId', 'trade', 'executionState', 'status', 'communityId', 'communitySequence', 'protocolDigest', 'osTrisTransactionId'):
        if isinstance(result, dict) and key in result:
            print(key.upper(), result[key])
    if isinstance(result, dict) and 'trade' in result and isinstance(result['trade'], dict):
        for key, value in result['trade'].items():
            if 'id' in key.lower() or 'state' in key.lower() or 'status' in key.lower():
                print('TRADE_' + key.upper(), value)
    print('COMMIT_RESPONSE_KEYS', sorted(result.keys()) if isinstance(result, dict) else type(result).__name__)
    print('LEDGER_E2E_CONTROLLED_TRANSACTION: COMMITTED')


if __name__ == '__main__':
    main()
