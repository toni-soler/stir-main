#!/usr/bin/env python3
"""One-time bootstrap of an IDAX private XRPL network (Linux equivalent of vendor/idax-ledger/xrpl/scripts/Initialize-PrivateNetwork.ps1).

Creates, per node, a persistent runtime directory outside the repository:
  validator-NN/secrets/validator-keys.json   master key material (validators only; never committed)
  validator-NN/config/xrpld.cfg              materialised from the vendor template
  validator-NN/config/validators.txt         the trusted validator public keys (the UNL)
  validator-NN/data, validator-NN/log        NuDB/SQLite and logs (created empty)
  api-01 has the same config without [validator_token]: it follows the validators and does not vote.

History: validators keep only the last --validator-history-ledgers ledgers (online_delete); api-01 keeps full history
and is the node IDAX Ledger reads, because verifying an old proof needs the old transaction and ledger. An idle network
still closes about 17 ledgers a minute at about 9 KB each, so an unpruned node grows about 81 GB a year.

Idempotent for existing identities: keys and tokens that already exist are kept, never regenerated. Prints only public
identities and the network id. Usage:
  IDAX_LEDGER_RUNTIME_PATH=~/idax-ledger-dev/runtime XRPLD_IMAGE=idax/xrpld:3.3.0-source \
      python3 deploy/idax-ledger/init_xrpl_network.py --network-id 2181844733
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

# Five validators: xrpld needs 80% of the trusted validators, so three give a quorum of three (no validator may fail)
# and five give a quorum of four (one may fail or be rebuilt while the network keeps validating).
VALIDATORS = ["validator-01", "validator-02", "validator-03", "validator-04", "validator-05"]
API_NODE = "api-01"
# Fixed addresses on the XRPL subnet (deploy/idax-ledger/compose.xrpl.yml). Peers are configured by address so that startup
# order and DNS resolution timing cannot leave a node without its fixed peers.
NODE_IPS = {"validator-01": "172.27.0.11", "validator-02": "172.27.0.12", "validator-03": "172.27.0.13",
            "api-01": "172.27.0.14", "validator-04": "172.27.0.15", "validator-05": "172.27.0.16"}


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def validator_keys(image, secrets_dir, command):
    uid, gid = os.getuid(), os.getgid()
    return run(["docker", "run", "--rm", "--user", f"{uid}:{gid}", "-v", f"{secrets_dir}:/keys",
                "--entrypoint", "validator-keys", image, command, "--keyfile", "/keys/validator-keys.json"]).stdout


def token_block(output):
    lines = output.splitlines()
    if "[validator_token]" not in lines:
        raise SystemExit("validator-keys produced no [validator_token] block")
    start = lines.index("[validator_token]") + 1
    block = []
    for line in lines[start:]:
        if not line.strip():
            break
        block.append(line.strip())
    return "\n".join(block)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--network-id", type=int, default=2181844733)
    parser.add_argument("--validator-history-ledgers", type=int, default=50000,
                        help="ledgers each validator keeps (about two days at 17 a minute); 0 keeps full history")
    parser.add_argument("--image", default=os.environ.get("XRPLD_IMAGE"))
    parser.add_argument("--runtime", default=os.environ.get("IDAX_LEDGER_RUNTIME_PATH"))
    parser.add_argument("--template", default=str(pathlib.Path(__file__).resolve().parents[2] /
                        "vendor/idax-ledger/xrpl/config/xrpld.cfg.template"))
    args = parser.parse_args()
    if not args.image or not args.runtime:
        raise SystemExit("XRPLD_IMAGE and IDAX_LEDGER_RUNTIME_PATH are required")
    runtime = pathlib.Path(args.runtime).expanduser().resolve()
    template = pathlib.Path(args.template).read_text()

    keys = {}
    for node in VALIDATORS + [API_NODE]:
        root = runtime / node
        for sub in ("secrets", "config", "data", "log"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        os.chmod(root / "secrets", 0o700)
        keyfile = root / "secrets" / "validator-keys.json"
        if node in VALIDATORS and not keyfile.exists():
            validator_keys(args.image, root / "secrets", "create_keys")
        if node in VALIDATORS:
            keys[node] = json.loads(keyfile.read_text())["public_key"]

    validators_txt = "[validators]\n" + "\n".join(keys[n] for n in VALIDATORS) + "\n"
    for node in VALIDATORS + [API_NODE]:
        root = runtime / node
        config_dir = root / "config"
        if node in VALIDATORS:
            keyfile = root / "secrets" / "validator-keys.json"
            token = token_block(validator_keys(args.image, root / "secrets", "create_token"))
        else:
            token = None
        config = template.replace("__NETWORK_ID__", str(args.network_id))
        # peer_private=1: a node accepts inbound peers only from its own fixed list, so every node lists every other.
        extra = "".join(f"{name} 51235\n" for name in VALIDATORS[3:] + [API_NODE])
        if "validator-03 51235\n" not in config:
            raise SystemExit("template [ips_fixed] changed; update init_xrpl_network.py")
        config = config.replace("validator-03 51235\n", "validator-03 51235\n" + extra, 1)
        keep = args.validator_history_ledgers
        if node in VALIDATORS and keep:
            if keep < 256:
                raise SystemExit("--validator-history-ledgers must be 0 or at least 256")
            if "fast_load=1\n" not in config or "[ledger_history]\nfull" not in config:
                raise SystemExit("template [node_db]/[ledger_history] changed; update init_xrpl_network.py")
            config = config.replace("fast_load=1\n", f"fast_load=1\nonline_delete={keep}\nadvisory_delete=0\n", 1)
            config = config.replace("[ledger_history]\nfull", f"[ledger_history]\n{keep}", 1)
        if token is None:
            config = re.sub(r"\[validator_token\]\s*__VALIDATOR_TOKEN__\s*", "", config)
        else:
            config = config.replace("__VALIDATOR_TOKEN__", token)
        for name, address in NODE_IPS.items():
            config = config.replace(f"{name} 51235", f"{address} 51235")
        if "__" in config:
            raise SystemExit(f"unresolved template placeholder for {node}")
        (config_dir / "xrpld.cfg").write_text(config)
        (config_dir / "validators.txt").write_text(validators_txt)
        os.chmod(config_dir / "xrpld.cfg", 0o600)
        if node in VALIDATORS:
            os.chmod(keyfile, 0o600)

    print(f"XRPL network id: {args.network_id}")
    for node in VALIDATORS:
        print(f"{node} validator public key: {keys[node]}")
    print(f"{API_NODE}: non-validating API node (no validator token)")
    print(f"runtime root: {runtime}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
