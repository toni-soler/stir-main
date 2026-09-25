"""Audit STIR-owned source for private dependencies and machine-local material."""
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]
PATTERN=re.compile(r'gitlab\.|(?:https?|ssh|git)://[^\s"\']*(?:\.internal|\.corp)(?:[/:\s"\']|$)|[A-Z]:[\\/]Users[\\/]|(?<![A-Za-z0-9/_-])/Users/|file://|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY',re.I)
bad=[]; count=0
EXPECTED_REMOTES={
    'stir-workspace':'https://github.com/toni-soler/stir-workspace.git',
    'stir-doc':'https://github.com/toni-soler/stir-doc.git',
    'stir-backend':'https://github.com/toni-soler/stir-backend.git',
    'stir-frontend':'https://github.com/toni-soler/stir-frontend.git',
    'stir-main':'https://github.com/toni-soler/stir-main.git',
}
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
                if relative == 'scripts/audit-public.py': continue
                bad.append(entry)
    remote_names=subprocess.check_output(['git','-C',str(repo),'remote'],text=True).split()
    if remote_names != ['origin']:
        bad.append(f'{name}: expected only the official origin remote')
    else:
        origin=subprocess.check_output(['git','-C',str(repo),'remote','get-url','origin'],text=True).strip()
        if origin != EXPECTED_REMOTES[name]: bad.append(f'{name}: unexpected origin URL')
parent_files=subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z']).decode().split('\0')
if any(p.startswith(('stir-doc/','stir-backend/','stir-frontend/','stir-main/')) for p in parent_files):bad.append('Workspace absorbs a child repository')
print(f'Scanned {count} STIR-owned text files.')
if bad: raise SystemExit('FAIL\n'+'\n'.join(bad))
print('PASS: no private URL, prohibited dependency marker, local path, private key or unexplained binary; official public STIR origins verified.')
