"""Reject private runtime files and music archives in the Git index, including -f additions."""
from pathlib import Path, PurePosixPath
import re, subprocess, sys

ROOT=Path(__file__).resolve().parents[1]
TAB_EXTENSIONS={'.gp','.gp3','.gp4','.gp5','.gpx','.gtp'}
PRIVATE_EXTENSIONS={'.db','.sqlite','.sqlite3','.lnk','.zip','.log','.bak'}
PRIVATE_NAMES={'config.json','config.tmp','readme.txt'}
PRIVATE_DIRS={'data','dist','node_modules','.venv','__pycache__'}

def main():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
    violations=[]
    for filename in filter(None,files):
        path=PurePosixPath(filename)
        name=path.name.lower()
        if (path.suffix.lower() in TAB_EXTENSIONS|PRIVATE_EXTENSIONS
            or name in PRIVATE_NAMES or '.sqlite3-' in name
            or (name.startswith('.env') and name!='.env.example')
            or any(part.lower() in PRIVATE_DIRS for part in path.parts)):
            violations.append(filename+': private file or tablature')
            continue
        content=subprocess.check_output(['git','show',':'+filename],cwd=ROOT)
        if path.suffix.lower() in {'.png','.jpg','.jpeg'}:
            signature=b'\x89PNG\r\n\x1a\n' if path.suffix.lower()=='.png' else b'\xff\xd8\xff'
            if not content.startswith(signature):violations.append(filename+': invalid image')
            continue
        try:source=content.decode('utf-8-sig')
        except UnicodeDecodeError:
            violations.append(filename+': unexpected binary file');continue
        patterns=[r'[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+',r'\bgh[pousr]_[A-Za-z0-9]{30,}',r'github_pat_[A-Za-z0-9_]{30,}',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
        if any(re.search(pattern,source,re.I) for pattern in patterns):violations.append(filename+': private path or credential pattern')
    if violations:
        print('\n'.join(violations),file=sys.stderr);return 1
    print(f'Publication check passed: {len([f for f in files if f])} tracked files; no tabs or private runtime data.')
    return 0

if __name__=='__main__':sys.exit(main())
