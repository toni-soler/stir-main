#!/usr/bin/env python3
"""Creates and funds the IDAX Ledger anchoring account on a private XRPL network (Linux equivalent of
vendor/idax-ledger/xrpl/scripts/Initialize-AnchoringAccount.ps1).

The anchor seed is written once to <runtime>/anchoring/secrets/anchor.seed (mode 600) and never printed or committed.
Funding comes from the network's root account. Its seed is read only from the environment variable
IDAX_LEDGER_FUNDING_SEED (never stored). On a DEV network with the default genesis account this is the well-known
standalone genesis seed; that is acceptable only for DEV. Production needs its own genesis decision first.

Usage:
  IDAX_LEDGER_RUNTIME_PATH=~/idax-ledger-dev/runtime IDAX_LEDGER_FUNDING_SEED=<root seed from env> \
      python3 deploy/idax-ledger/init_anchoring_account.py --rpc http://127.0.0.1:5006 --network-id 2181844733
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.request


def rpc(url, method, params):
    body = json.dumps({"method": method, "params": [params]}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read())["result"]
    if result.get("error") and method != "account_info":
        raise SystemExit(f"{method} failed: {result.get('error_message', result['error'])}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default="http://127.0.0.1:5006")
    parser.add_argument("--network-id", type=int, default=2181844733)
    parser.add_argument("--funding-drops", type=int, default=1000000000)
    parser.add_argument("--runtime", default=os.environ.get("IDAX_LEDGER_RUNTIME_PATH"))
    args = parser.parse_args()
    if not args.runtime:
        raise SystemExit("IDAX_LEDGER_RUNTIME_PATH is required")
    runtime = pathlib.Path(args.runtime).expanduser().resolve()
    secrets = runtime / "anchoring" / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    os.chmod(secrets, 0o700)
    seed_path = secrets / "anchor.seed"
    address_path = runtime / "anchoring" / "address.txt"

    if seed_path.exists():
        seed = seed_path.read_text().strip()
        wallet = rpc(args.rpc, "wallet_propose", {"seed": seed})
    else:
        wallet = rpc(args.rpc, "wallet_propose", {})
        seed = wallet["master_seed"]
        seed_path.write_text(seed)
        os.chmod(seed_path, 0o600)
    address = wallet["account_id"]
    address_path.write_text(address + "\n")

    existing = rpc(args.rpc, "account_info", {"account": address, "ledger_index": "validated"})
    if existing.get("account_data"):
        print(f"IDAX Ledger anchoring account: {address}")
        print("already funded; seed kept in the runtime secrets directory")
        return 0

    root_seed = os.environ.get("IDAX_LEDGER_FUNDING_SEED")
    if not root_seed:
        raise SystemExit("set IDAX_LEDGER_FUNDING_SEED in the process environment to fund the anchoring account")
    root = rpc(args.rpc, "wallet_propose", {"seed": root_seed})
    root_address = root["account_id"]
    root_info = rpc(args.rpc, "account_info", {"account": root_address, "ledger_index": "validated"})
    server = rpc(args.rpc, "server_info", {})
    last_ledger = int(server["info"]["validated_ledger"]["seq"]) + 20
    transaction = {
        "TransactionType": "Payment",
        "Account": root_address,
        "Destination": address,
        "Amount": str(args.funding_drops),
        "Fee": "10",
        "Sequence": int(root_info["account_data"]["Sequence"]),
        "LastLedgerSequence": last_ledger,
        "NetworkID": args.network_id,
        "Flags": 2147483648,
    }
    signed = rpc(args.rpc, "sign", {"secret": root_seed, "tx_json": transaction, "offline": True})
    submitted = rpc(args.rpc, "submit", {"tx_blob": signed["tx_blob"]})
    if submitted.get("engine_result") != "tesSUCCESS":
        raise SystemExit(f"funding submit failed: {submitted.get('engine_result')}")
    tx_hash = submitted["tx_json"]["hash"]
    deadline = time.time() + 90
    validated = None
    while time.time() < deadline:
        time.sleep(2)
        try:
            validated = rpc(args.rpc, "tx", {"transaction": tx_hash})
        except Exception:
            validated = None
        if validated and validated.get("validated"):
            break
    if not validated or not validated.get("validated") or validated["meta"]["TransactionResult"] != "tesSUCCESS":
        raise SystemExit("funding transaction was not validated successfully")
    print(f"IDAX Ledger anchoring account: {address}")
    print(f"funded with {args.funding_drops} drops; funding transaction {tx_hash} validated in ledger {validated['ledger_index']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
