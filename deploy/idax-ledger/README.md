# IDAX Ledger and private XRPL network (DEV)

Deployment boundary for the IDAX Ledger and its XRPL private network. It is a separate Compose project (`idax-ledger-dev`)
from STIR, so `docker compose down` of STIR never touches the ledger history. Production uses the same files with its own
runtime, identities and project name (`idax-ledger-prod`); nothing is shared between the two.

## Topology

| Service | Role | Host ports (loopback only) |
|---|---|---|
| validator-01/02/03 | validators, `peer_private=1`, UNL = the three keys | RPC 5006/5007/5008, WS 6006/6007/6008 |
| api-01 | non-validating API node, follows the validated ledger | RPC 5010, WS 6010 |
| ledger-postgres | the ledger's own PostgreSQL (`idax_core`, `idax_ledger`) | none |
| ledger | IDAX Ledger API (proofs, anchoring, reconciliation) | 8094 |

Network id `2181844733` is the IDAX Private XRPL constant from `vendor/idax-ledger/docs/XRPL_PRIVATE_NETWORK.md`. The DEV
network reuses it with **new validator identities**; no key from any earlier workstation network was imported. Production
will choose its own network identity (see the open decisions below).

Software: source-built `xrpld 3.3.0` (`idax/xrpld:3.3.0-source`, commit `00a178fb92ca49521b937ae1a99d863765ea8a90`).

## Deviations from the vendor design (deliberate)

- Image: the verified source-built xrpld, not the upstream image (`XRPLD_IMAGE`).
- RPC/WebSocket bind to `127.0.0.1`. The vendor design binds them on every interface.
- Configuration is mounted at `/etc/xrpld`: the source-built entrypoint runs `xrpld --conf /etc/xrpld/xrpld.cfg` and copies nothing.
- A non-validating API node is added; validators list it as a fixed peer (required by `peer_private=1`).
- Node data (NuDB, SQLite, identities) are bind mounts under the runtime tree, as the vendor design requires. The ledger
  database uses a named volume (`idax-ledger-dev-postgres-data`). Named volumes for node data would change where the ledger
  history lives; that is a migration, not a silent change.
- Linux bootstrap and lifecycle scripts replace the PowerShell scripts of the vendor design, which cannot run on the VM.

## Runtime (never in Git)

`IDAX_LEDGER_RUNTIME_PATH` (DEV: `~/idax-ledger-dev/runtime`):
- `validator-NN/secrets/validator-keys.json` (master key material, not mounted into xrpld), `validator-NN/config/xrpld.cfg`
  (contains the validator token), `validators.txt` (public keys), `data/`, `log/`.
- `ledger/secrets/`: `postgres_password`, `runtime_password`, `jwt_public_key` (the STIR platform key, the trust anchor for
  service tokens), `anchor.seed`. Files are mode 0644 inside a 0755 directory because the containers run as non-root, the
  same policy STIR uses for its secrets; the runtime tree itself is 0711.
- `anchoring/secrets/anchor.seed` (mode 0600): anchoring account seed. Address in `anchoring/address.txt`.

## Bootstrap (once per network)

```
export IDAX_LEDGER_RUNTIME_PATH=~/idax-ledger-dev/runtime XRPLD_IMAGE=idax/xrpld:3.3.0-source
python3 deploy/idax-ledger/init_xrpl_network.py --network-id 2181844733
sh deploy/idax-ledger/start_xrpl.sh         # ordered start + peer recovery + ledger start
IDAX_LEDGER_FUNDING_SEED=<root seed> python3 deploy/idax-ledger/init_anchoring_account.py --rpc http://127.0.0.1:5006
python3 deploy/idax-ledger/xrpl_health.py    # HEALTHY when validators propose with >=2 peers and api-01 is full
```

The DEV funding root is the standard standalone genesis account of a private XRPL network (its seed is public by design).
That is acceptable only on DEV. Production requires its own genesis decision before any funding.

## Observed operational behaviour (DEV, 2026-10-05)

- Starting the nodes simultaneously after a full stop keeps the validated ledgers but does not re-establish peering. Restarting
  each disconnected node individually does. `start_xrpl.sh` encodes that procedure.
- A validator must list every peer that should reach it under `peer_private=1`; the API node is in the validators' fixed lists.

## Evidence (DEV)

- Network identity and validator public keys: `xrpl_health.py` output (public data only).
- Validated ledger progression: 56 to 271 during bring-up; ledger identity survived a full stop and start: historical ledger 245
  returned hash `72379BD7…9E153` before and after. Validator public keys unchanged.
- Anchoring account funded by a validated payment in ledger 135.

## Open decisions (stop before production, not before DEV)

1. Production genesis and network identity: the production network needs its own genesis account. The DEV genesis seed is
   public and must not fund production. Its design is not decided in the repository.
2. Production retention: the vendor design keeps full ledger history (`ledger_history=full`, no `online_delete`) and says the
   production retention and capacity must be defined before adopting it.
3. Validator topology: three validators under one operator on one host are not independent fault domains and are not
   pilot-grade decentralisation. This is acceptable for a technical MVP only.
