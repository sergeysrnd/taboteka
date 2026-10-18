"""Create machine-local settings without putting archive paths in Git."""
import argparse,json,os,sys
from pathlib import Path

BASE=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description='Configure Taboteka for a local archive')
    parser.add_argument('--tabs-root',required=True,type=Path)
    parser.add_argument('--guitar-pro',type=Path)
    parser.add_argument('--site-port',type=int,default=8765)
    parser.add_argument('--api-port',type=int,default=8766)
    parser.add_argument('--force',action='store_true',help='Replace existing local settings')
    args=parser.parse_args()
    target=BASE/'config.json'
    if target.exists() and not args.force:parser.error('config.json already exists; use --force to replace it')
    root=args.tabs_root.expanduser().resolve()
    if not root.is_dir():parser.error('--tabs-root must be an existing directory')
    if not all(1024<=p<=65535 for p in (args.site_port,args.api_port)) or args.site_port==args.api_port:
        parser.error('Use distinct ports between 1024 and 65535')
    exe=args.guitar_pro
    if exe is None:
        for folder in ('PROGRAMFILES','PROGRAMFILES(X86)'):
            candidate=Path(os.environ.get(folder,'C:/Program Files'))/'Arobas Music'/'Guitar Pro 8'/'GuitarPro.exe'
            if candidate.is_file():exe=candidate;break
    if exe is not None and (not exe.is_file() or exe.name.lower()!='guitarpro.exe'):
        parser.error('--guitar-pro must point to an existing GuitarPro.exe')
    value={'tabs_root':str(root),'guitar_pro':str(exe.resolve()) if exe else '', 'port':args.api_port,'site_port':args.site_port,'site_url':f'http://127.0.0.1:{args.site_port}'}
    temp=target.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(target)
    (BASE/'data').mkdir(exist_ok=True)
    (BASE/'data'/'python-path.txt').write_text(sys.executable,encoding='utf-8')
    (BASE/'.env.local').write_text(f'VITE_API_BASE=http://127.0.0.1:{args.api_port}\n',encoding='utf-8')
    print('Local configuration created. Build the frontend, then run launch.pyw or setup-iis.ps1.')

if __name__=='__main__':main()
