"""Local per-user API. IIS serves the built frontend; archive files stay put."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
import hmac,json,mimetypes,os,secrets,threading,time,traceback
import library as lib
import ratings
from windows_open import open_tab

TOKEN=secrets.token_urlsafe(32)
CONF=lib.config()
PORT=int(CONF.get('port',8766))
ORIGINS={CONF.get('site_url','http://127.0.0.1:8765'),f'http://localhost:{CONF.get("site_port",8765)}'}
HOSTS={f'127.0.0.1:{PORT}',f'localhost:{PORT}'}
LOG_LOCK=threading.Lock()

class Handler(BaseHTTPRequestHandler):
    server_version='Taboteka/1.0'
    def log_message(self,fmt,*args):
        with LOG_LOCK:
            with (lib.DATA/'server.log').open('a',encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+(fmt%args)+'\n')
    def valid_request(self,mutation=False):
        if self.headers.get('Host') not in HOSTS:return False
        origin=self.headers.get('Origin')
        if origin and origin not in ORIGINS and origin not in {'http://'+h for h in HOSTS}:return False
        if mutation and not hmac.compare_digest(self.headers.get('X-Taboteka-Token',''),TOKEN):return False
        return True
    def headers_for(self,ctype='application/json; charset=utf-8'):
        self.send_header('Content-Type',ctype)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        origin=self.headers.get('Origin')
        if origin in ORIGINS:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
    def json(self,value,status=200):
        raw=json.dumps(value,ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(status);self.headers_for()
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_OPTIONS(self):
        if not self.valid_request():return self.json({'error':'Недопустимый источник запроса'},403)
        self.send_response(204);self.headers_for()
        self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type,X-Taboteka-Token')
        self.send_header('Access-Control-Max-Age','600');self.end_headers()
    def do_GET(self):
        if not self.valid_request():return self.json({'error':'Доступ запрещён'},403)
        u=urlsplit(self.path);args={k:v[-1] for k,v in parse_qs(u.query).items()}
        try:
            if u.path=='/api/bootstrap':
                return self.json({'token':TOKEN,'stats':lib.stats(),'config':{**lib.config(),'guitar_pro_found':Path(lib.config()['guitar_pro']).is_file()},'ratings':ratings.status()})
            if u.path=='/api/stats':return self.json({'stats':lib.stats(),'ratings':ratings.status(),'scanning':lib.SCAN_LOCK.locked()})
            if u.path=='/api/artists':return self.json(lib.list_artists(args))
            if u.path=='/api/tabs':
                result=lib.list_tabs(args)
                return self.json(result)
            if u.path=='/api/tab':return self.json(lib.get_tab(args['id']))
            if u.path=='/api/export':
                with lib.connection() as db:
                    rows=db.execute("SELECT path,favorite,practice,notes,opened_at FROM tabs WHERE favorite=1 OR practice!='' OR notes!='' OR opened_at IS NOT NULL").fetchall()
                self.send_response(200);self.headers_for();self.send_header('Content-Disposition','attachment; filename="taboteka-backup.json"');self.end_headers()
                self.wfile.write(json.dumps({'version':1,'saved_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'items':[dict(r) for r in rows]},ensure_ascii=False,indent=2).encode('utf-8'));return
            # Development/fallback access, IIS is the normal frontend entry.
            if not u.path.startswith('/api/'):
                base=(lib.BASE/'dist').resolve()
                path=(base/('index.html' if u.path=='/' else u.path.lstrip('/'))).resolve()
                if not path.is_relative_to(base) or not path.is_file():return self.json({'error':'Страница не найдена'},404)
                raw=path.read_bytes();self.send_response(200);self.headers_for(mimetypes.guess_type(path.name)[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
            self.json({'error':'Неизвестный запрос'},404)
        except (ValueError,KeyError,FileNotFoundError) as ex:self.json({'error':str(ex)},400)
        except Exception as ex:
            self.log_message('%s',traceback.format_exc());self.json({'error':'Ошибка библиотеки: '+str(ex)},500)
    def do_POST(self):
        if not self.valid_request(mutation=True):return self.json({'error':'Обновите страницу и повторите действие'},403)
        try:
            route=urlsplit(self.path).path
            length=int(self.headers.get('Content-Length',0))
            maximum=32_000_000 if route=='/api/import' else 1_000_000
            if not 0<=length<=maximum:raise ValueError('Запрос слишком большой')
            body=json.loads(self.rfile.read(length) or '{}')
            if not isinstance(body,dict):raise ValueError('Тело запроса должно быть JSON-объектом')
            if route=='/api/tab':return self.json(lib.update_tab(body['id'],body))
            if route=='/api/open':
                path=lib.safe_file(body['id'])
                # Use Windows' registered open verb, including Guitar Pro's DDE
                # handshake with an existing instance. Starting the EXE directly
                # can return success while the running instance ignores the file.
                open_tab(path,lib.config()['guitar_pro'])
                with lib.WRITE_LOCK,lib.connection() as db:
                    with db:db.execute('UPDATE tabs SET opened_at=? WHERE id=?',(time.time(),body['id']))
                return self.json({'ok':True,'filename':path.name})
            if route=='/api/scan':
                return self.json(lib.scan())
            if route=='/api/ratings':
                if body.get('action')=='stop':ratings.stop_bulk()
                elif body.get('artist'):ratings.enqueue(body['artist'],force=bool(body.get('force')))
                else:ratings.start_bulk()
                return self.json(ratings.status())
            if route=='/api/settings':
                exe=Path(str(body.get('guitar_pro','')))
                if exe.name.lower()!='guitarpro.exe' or not exe.is_file():raise ValueError('Выберите существующий GuitarPro.exe')
                with lib.WRITE_LOCK:
                    config=lib.config();config['guitar_pro']=str(exe)
                    temporary=lib.CONFIG.with_suffix('.tmp');temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temporary,lib.CONFIG)
                return self.json({'ok':True})
            if route=='/api/import':
                return self.json(lib.restore_personal_data(body))
            self.json({'error':'Неизвестное действие'},404)
        except (ValueError,KeyError,FileNotFoundError,OSError) as ex:self.json({'error':str(ex)},400)
        except Exception as ex:
            self.log_message('%s',traceback.format_exc());self.json({'error':str(ex)},500)

def main():
    lib.initialize()
    if lib.stats()['files']==0:lib.scan()
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    server.daemon_threads=True
    (lib.DATA/'api.pid').write_text(str(os.getpid()),encoding='ascii')
    print(f'Taboteka API http://127.0.0.1:{PORT}',flush=True)
    ratings.resume_if_needed()
    server.serve_forever()

if __name__=='__main__':main()
