"""Audit STIR-owned source; explanatory prohibitions are allowed only in boundary doc."""
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]
PATTERN=re.compile(r'freefolk|comart|artonsoft|gitlab\.|Dynamics AX|[A-Z]:[\\/]Users[\\/]|(?<![A-Za-z0-9/_-])/Users/|file://|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY',re.I)
bad=[]; allowed=[]; count=0
for name in ['stir-workspace','stir-doc','stir-backend','stir-frontend','stir-main']:
    repo=ROOT if name=='stir-workspace' else ROOT/name
    paths=subprocess.check_output(['git','-C',str(repo),'ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
    for relative in sorted(set(paths)-{''}):
        p=repo/relative
        if not p.is_file(): continue
        count+=1
        try: content=p.read_text(encoding='utf-8')
        except UnicodeDecodeError: bad.append(f'{name}/{relative}: unexpected binary');continue
        for number,line in enumerate(content.splitlines(),1):
            if PATTERN.search(line):
                entry=f'{name}/{relative}:{number}'
                if relative in ['PUBLIC_SOFTWARE_BOUNDARY.md','scripts/audit-public.py']:allowed.append(entry)
                else:bad.append(entry)
    remotes=subprocess.check_output(['git','-C',str(repo),'remote','-v'],text=True)
    if remotes.strip(): bad.append(f'{name}: remote needs explicit STIR provenance review')
parent_files=subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z']).decode().split('\0')
if any(p.startswith(('stir-doc/','stir-backend/','stir-frontend/','stir-main/')) for p in parent_files):bad.append('Workspace absorbs a child repository')
print(f'Scanned {count} STIR-owned text files; {len(allowed)} explanatory/audit-pattern matches reviewed.')
if bad: raise SystemExit('FAIL\n'+'\n'.join(bad))
print('PASS: no private URL, prohibited dependency marker, local path, private key or unexplained binary; no STIR remotes.')
