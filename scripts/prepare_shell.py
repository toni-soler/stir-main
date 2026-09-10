"""TEMPORARY PUBLIC-SOURCE COMPATIBILITY ADAPTER.
Applies reviewed public patches only; rejects unexpected tracked or untracked source.
"""
import subprocess

def prepare(root):
    patches={"idax-shell":"idax-shell-0.3-extension.patch", "idax-ledger":"idax-ledger-0.3-migration-switch.patch", "ostris":"ostris-0.3-migration-switch.patch"}
    for name,filename in patches.items():
        repo=root/"vendor"/name
        patch=root/"patches"/filename
        expected=patch.read_bytes().replace(b"\r\n",b"\n")
        current=subprocess.check_output(["git","-C",str(repo),"diff","HEAD","--binary"]).replace(b"\r\n",b"\n")
        untracked=subprocess.check_output(["git","-C",str(repo),"ls-files","--others","--exclude-standard"])
        if untracked.strip():raise RuntimeError(f"Unexpected untracked vendor source: {name}")
        if current==expected:continue
        if current:raise RuntimeError(f"Unexpected modified vendor source: {name}")
        subprocess.run(["git","-C",str(repo),"apply","--check","--index",str(patch)],check=True)
        subprocess.run(["git","-C",str(repo),"apply","--index",str(patch)],check=True)
