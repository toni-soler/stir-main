"""TEMPORARY PUBLIC-SOURCE COMPATIBILITY ADAPTER.
Applies reviewed public patches only; rejects unexpected tracked or untracked source.
Each repo lists an ORDERED sequence of patch files (not upstreamed there yet) - applied one at a
time, each checked independently for idempotency (already-applied patches are skipped via a
reverse-apply probe, since after several patches stack up the working tree no longer matches any
single patch's own "expected diff" snapshot byte-for-byte).
"""
import subprocess

def prepare(root):
    patches={"idax-shell":["idax-shell-0.3-extension.patch", "idax-shell-0.5-session-permissions.patch"],
             "idax-ledger":["idax-ledger-0.3-migration-switch.patch"],
             "ostris":["ostris-0.3-migration-switch.patch", "ostris-0.4-public-application-surface.patch"]}
    for name,filenames in patches.items():
        repo=root/"vendor"/name
        untracked=subprocess.check_output(["git","-C",str(repo),"ls-files","--others","--exclude-standard"])
        if untracked.strip():raise RuntimeError(f"Unexpected untracked vendor source: {name}")
        for filename in filenames:
            patch=root/"patches"/filename
            already_applied=subprocess.run(["git","-C",str(repo),"apply","--check","--reverse",str(patch)],capture_output=True).returncode==0
            if already_applied:continue
            check=subprocess.run(["git","-C",str(repo),"apply","--check","--index",str(patch)],capture_output=True)
            if check.returncode:raise RuntimeError(f"Unexpected modified vendor source: {name} (patch {filename} does not apply cleanly): {check.stderr.decode(errors='replace')}")
            subprocess.run(["git","-C",str(repo),"apply","--index",str(patch)],check=True)
