from pathlib import Path
from urllib.request import urlopen
import json,os,subprocess,sys,time,webbrowser

BASE=Path(__file__).resolve().parent
config=json.loads((BASE/'config.json').read_text(encoding='utf-8'))
url='http://127.0.0.1:'+str(config.get('port',8766))+'/api/bootstrap'
def alive():
    try:
        with urlopen(url,timeout=1) as r:return r.status==200
    except Exception:return False
if not alive():
    # A scheduled interactive task survives the launching shell and is supervised
    # by Windows; prefer it whenever the IIS deployment has registered it.
    if os.name=='nt':
        subprocess.run(['schtasks.exe','/Run','/TN','Taboteka API'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
        for _ in range(20):
            if alive():break
            time.sleep(.25)
if not alive():
    (BASE/'data').mkdir(exist_ok=True)
    log=(BASE/'data'/'startup.log').open('a',encoding='utf-8')
    python=Path(sys.executable)
    pythonw=python.with_name('pythonw.exe')
    subprocess.Popen([str(pythonw if pythonw.exists() else python),str(BASE/'server.py')],cwd=BASE,stdout=log,stderr=log,creationflags=0x08000000 if os.name=='nt' else 0)
    for _ in range(100):
        if alive():break
        time.sleep(.2)
if '--background' not in sys.argv:
    webbrowser.open(config.get('site_url','http://127.0.0.1:8765'))
