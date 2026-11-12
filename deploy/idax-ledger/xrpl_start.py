#!/usr/bin/env python3
"""Starts the IDAX private XRPL nodes so that validation resumes, whatever state they stopped in.

Why this exists: nodes that stop at different moments persist different last validated ledgers. Started together, the
network builds from the lowest of those and the nodes holding a later validated ledger refuse to validate it ("Not
validating incompatible with valid ledger"); nothing validates again (DEV, 2026-11-11, twice). Starting the most advanced
node first and the others after it avoids that. This script does it without an operator choosing the order:

  1. if the network is already healthy (xrpl_health.py), do nothing;
  2. create or update the containers from compose.xrpl.yml without starting them, and stop any that run;
  3. read each node's last persisted ledger from a copy of its ledger.db (the node's own files are not opened);
  4. start the most advanced node, wait for its RPC, then start the rest in descending order, lagging and empty nodes last;
  5. wait for xrpl_health.py: validated ledger advancing on every node and one chain.

  IDAX_LEDGER_RUNTIME_PATH=... XRPLD_IMAGE=... python3 deploy/idax-ledger/xrpl_start.py [--force]

--force skips step 1 (used by the restart test). Exit status 0 only when step 5 passes.
"""
import glob
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

import xrpl_health

DIR = pathlib.Path(__file__).resolve().parent
PROJECT = os.environ.get("IDAX_LEDGER_PROJECT", "idax-ledger-dev")
RUNTIME = pathlib.Path(os.environ.get("IDAX_LEDGER_RUNTIME_PATH", "")).expanduser()
NODES = [name for name, _, _ in xrpl_health.NODES]
URLS = {name: url for name, url, _ in xrpl_health.NODES}
FIRST_NODE_SETTLE_SECONDS = 20
NEXT_NODE_GAP_SECONDS = 15
HEALTH_TIMEOUT_SECONDS = 600


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def container(node):
    return f"{PROJECT}-{node}"


def healthy():
    return subprocess.run([sys.executable, str(DIR / "xrpl_health.py")], capture_output=True).returncode == 0


def persisted_tip(node):
    """Highest ledger in the node's SQLite ledger index, read from a private copy; -1 when the node has no ledger yet."""
    source = RUNTIME / node / "data" / "db"
    files = glob.glob(str(source / "ledger.db*"))
    if not files:
        return -1
    scratch = tempfile.mkdtemp(prefix="xrpl-tip-")
    try:
        for path in files:
            shutil.copy(path, scratch)
        connection = sqlite3.connect(os.path.join(scratch, "ledger.db"))
        try:
            row = connection.execute("select max(LedgerSeq) from Ledgers").fetchone()
        finally:
            connection.close()
        return row[0] if row and row[0] is not None else -1
    except sqlite3.Error:
        return -1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def wait_for_rpc(node, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            xrpl_health.server_info(URLS[node])
            return True
        except Exception:
            time.sleep(3)
    return False


def main():
    if not str(RUNTIME) or not RUNTIME.is_dir():
        raise SystemExit("IDAX_LEDGER_RUNTIME_PATH must point at the node runtime directory")
    if "--force" not in sys.argv and healthy():
        print("XRPL network already healthy; nothing to do")
        return 0
    compose = ["docker", "compose", "-p", PROJECT, "-f", str(DIR / "compose.xrpl.yml")]
    run(*compose, "up", "--no-start", *NODES)
    run(*compose, "stop", "-t", "60", *NODES)
    tips = {node: persisted_tip(node) for node in NODES}
    order = sorted(NODES, key=lambda node: -tips[node])
    print("persisted ledger per node: " + ", ".join(f"{node}={tips[node]}" for node in order))
    for position, node in enumerate(order):
        run("docker", "start", container(node))
        if position == 0:
            if not wait_for_rpc(node):
                raise SystemExit(f"{node} did not answer RPC after start")
            time.sleep(FIRST_NODE_SETTLE_SECONDS)
        else:
            time.sleep(NEXT_NODE_GAP_SECONDS)
    deadline = time.time() + HEALTH_TIMEOUT_SECONDS
    while time.time() < deadline:
        if healthy():
            break
        time.sleep(5)
    result = subprocess.run([sys.executable, str(DIR / "xrpl_health.py")])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
