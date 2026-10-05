#!/usr/bin/env python3
"""Read-only health and identity report for the IDAX private XRPL network. Calls server_info on every node over the
loopback admin RPC and prints public identities only (validator public keys, network id, state, peers, validated
ledger, complete ledger ranges). Never prints secrets.

  python3 deploy/idax-ledger/xrpl_health.py [--json]

Exit status 0 only when the three validators are `proposing` with at least two peers and a validated ledger, and the API
node is `full` and follows the validated ledger.
"""
import json
import sys
import urllib.request

NODES = [
    ("validator-01", "http://127.0.0.1:5006", "proposing"),
    ("validator-02", "http://127.0.0.1:5007", "proposing"),
    ("validator-03", "http://127.0.0.1:5008", "proposing"),
    ("api-01", "http://127.0.0.1:5010", "full"),
]


def server_info(url):
    body = json.dumps({"method": "server_info", "params": [{}]}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["result"]["info"]


def main():
    report, healthy = [], True
    for name, url, expected_state in NODES:
        info = server_info(url)
        validated = info.get("validated_ledger") or {}
        row = {
            "node": name,
            "networkId": info.get("network_id"),
            "serverState": info.get("server_state"),
            "peers": info.get("peers"),
            "validatedLedgerSeq": validated.get("seq"),
            "validatedLedgerHash": validated.get("hash"),
            "completeLedgers": info.get("complete_ledgers"),
            "validatorPublicKey": info.get("pubkey_validator", None),
            "buildVersion": info.get("build_version"),
        }
        report.append(row)
        ok = info.get("server_state") == expected_state and validated.get("seq") is not None
        if name.startswith("validator"):
            ok = ok and (info.get("peers") or 0) >= 2
        healthy = healthy and ok
    if "--json" in sys.argv:
        print(json.dumps({"healthy": healthy, "nodes": report}, indent=2))
    else:
        for row in report:
            print(f"{row['node']:13} net={row['networkId']} state={row['serverState']:9} peers={row['peers']} "
                  f"validated={row['validatedLedgerSeq']} complete={row['completeLedgers']} build={row['buildVersion']}")
        print("HEALTHY" if healthy else "NOT HEALTHY")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
