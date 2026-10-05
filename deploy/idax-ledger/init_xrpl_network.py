#!/usr/bin/env python3
"""One-time bootstrap of an IDAX private XRPL network (Linux equivalent of vendor/idax-ledger/xrpl/scripts/Initialize-PrivateNetwork.ps1).

Creates, per node, a persistent runtime directory outside the repository:
  validator-NN/secrets/validator-keys.json   master key material (validators only; never committed)
  validator-NN/config/xrpld.cfg              materialised from the vendor template
  validator-NN/config/validators.txt         the trusted validator public keys (the UNL)
  validator-NN/data, validator-NN/log        NuDB/SQLite and logs (created empty)
  api-01 has the same config without [validator_token]: it follows the validators and does not vote.

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

VALIDATORS = ["validator-01", "validator-02", "validator-03"]
API_NODE = "api-01"
# Fixed addresses on the XRPL subnet (deploy/idax-ledger/compose.xrpl.yml). Peers are configured by address so that startup
# order and DNS resolution timing cannot leave a node without its fixed peers.
NODE_IPS = {"validator-01": "172.27.0.11", "validator-02": "172.27.0.12", "validator-03": "172.27.0.13", "api-01": "172.27.0.14"}


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
        if node in VALIDATORS:
            # peer_private=1: validators accept inbound peers only from their own fixed list, so the API node
            # must be listed here or it can never connect to the validators.
            config = config.replace("validator-03 51235\n", f"validator-03 51235\n{API_NODE} 51235\n", 1)
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
