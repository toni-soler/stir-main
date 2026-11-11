#!/usr/bin/env python3
"""Read-only health and identity report for the IDAX private XRPL network. Calls server_info on every node over the
loopback admin RPC and prints public identities only (validator public keys, network id, state, peers, validated
ledger, complete ledger ranges). Never prints secrets.

  python3 deploy/idax-ledger/xrpl_health.py [--json] [--window SECONDS]

Exit status 0 only when the three validators are `proposing` with at least two peers, the API node is `full`, the
validated ledger ADVANCES on every node during the sampling window (default 15 s), and every node reports the same hash
for the same validated ledger. State alone is not health: after the 2026-10-28 disk exhaustion all four nodes reported
proposing/full with three peers while each sat on a different validated ledger and none advanced.
"""
import json
import sys
import time
import urllib.request

NODES = [
    ("validator-01", "http://127.0.0.1:5006", "proposing"),
    ("validator-02", "http://127.0.0.1:5007", "proposing"),
    ("validator-03", "http://127.0.0.1:5008", "proposing"),
    ("api-01", "http://127.0.0.1:5010", "full"),
]


def rpc(url, method, params):
    body = json.dumps({"method": method, "params": [params]}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["result"]


def server_info(url):
    return rpc(url, "server_info", {})["info"]


def validated_seq(url):
    return (server_info(url).get("validated_ledger") or {}).get("seq")


def main():
    window = int(sys.argv[sys.argv.index("--window") + 1]) if "--window" in sys.argv else 15
    report, healthy = [], True
    before = {name: validated_seq(url) for name, url, _ in NODES}
    time.sleep(window)
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
            "validatedLedgerAdvanced": (validated.get("seq") or 0) - (before[name] or 0),
            "completeLedgers": info.get("complete_ledgers"),
            "validatorPublicKey": info.get("pubkey_validator", None),
            "buildVersion": info.get("build_version"),
        }
        report.append(row)
        ok = info.get("server_state") == expected_state and validated.get("seq") is not None
        ok = ok and row["validatedLedgerAdvanced"] > 0
        if name.startswith("validator"):
            ok = ok and (info.get("peers") or 0) >= 2
        healthy = healthy and ok
    # Same chain: every node must hold the same validated hash at the lowest validated index just sampled.
    common = min((row["validatedLedgerSeq"] or 0) for row in report)
    hashes = set()
    for _, url, _ in NODES:
        ledger = rpc(url, "ledger", {"ledger_index": common}) if common else {}
        hashes.add(ledger.get("ledger_hash") if ledger.get("validated") else None)
    agreed = len(hashes) == 1 and None not in hashes
    healthy = healthy and agreed
    if "--json" in sys.argv:
        print(json.dumps({"healthy": healthy, "sameValidatedChain": agreed, "commonLedgerIndex": common,
                          "windowSeconds": window, "nodes": report}, indent=2))
    else:
        for row in report:
            print(f"{row['node']:13} net={row['networkId']} state={row['serverState']:9} peers={row['peers']} "
                  f"validated={row['validatedLedgerSeq']} (+{row['validatedLedgerAdvanced']} in {window}s) "
                  f"complete={row['completeLedgers']} build={row['buildVersion']}")
        print(f"same validated chain at ledger {common}: {'yes' if agreed else 'NO'}")
        print("HEALTHY" if healthy else "NOT HEALTHY")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
