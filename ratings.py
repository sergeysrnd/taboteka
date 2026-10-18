"""Conservative, cached importer. One request per artist, never downloads tabs."""
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import PurePosixPath
from urllib.parse import urlsplit,unquote,urljoin
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
import json,logging,re,threading,time
from datetime import datetime,timedelta
import library as lib
import catalog_names

POOL=ThreadPoolExecutor(max_workers=2,thread_name_prefix='ratings')
PENDING=set()
LOCK=threading.Lock()
REQUEST_LOCK=threading.Lock()
LAST_REQUEST=0.0
BULK={'running':False,'done':0,'total':0,'errors':0,'current':'','stop':False,'last_error':None}
RESUME=lib.DATA/'ratings-sync.json'

def persist_sync(enabled):
    with LOCK:
        temporary=RESUME.with_suffix('.tmp')
        temporary.write_text(json.dumps({'resume_on_start':enabled}),encoding='utf-8')
        temporary.replace(RESUME)

def allowed_url(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.netloc=='www.gtp-tabs.ru' and p.path.startswith('/n/tabs/') and not p.username and not p.password

class SourceRedirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not allowed_url(newurl):raise ValueError('Источник перенаправил на другой сайт')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url):
    global LAST_REQUEST
    if not allowed_url(url):
        raise ValueError('Недопустимый адрес источника')
    req=Request(url,headers={'User-Agent':'TabotekaLocal/1.0 (personal archive metadata cache)','Accept':'text/html'})
    for attempt in range(3):
        with REQUEST_LOCK:
            delay=0.8-(time.monotonic()-LAST_REQUEST)
            if delay>0:time.sleep(delay)
            LAST_REQUEST=time.monotonic()
        try:
            with build_opener(SourceRedirects()).open(req,timeout=35) as response:
                raw=response.read(12_000_001)
                if len(raw)>12_000_000:raise ValueError('Страница источника слишком большая')
                return raw.decode('utf-8',errors='replace')
        except HTTPError as ex:
            if ex.code not in (429,500,502,503,504) or attempt==2:raise
            retry=ex.headers.get('Retry-After','') if ex.headers else ''
            time.sleep(min(60,max(1,int(retry))) if retry.isdigit() else 2**(attempt+1))
        except (URLError,TimeoutError):
            if attempt==2:raise
            time.sleep(2**(attempt+1))

def parse_artist(source):
    result=[]
    for block in re.findall(r'<li\b[^>]*>(.*?)</li>',source,re.S|re.I):
        rating=re.search(r'<div\s+class="rating\b[^>]*>(.*?)</div>',block,re.S)
        if not rating or not re.search(r'data-model="Tab"',rating.group(0)):continue
        model=re.search(r'data-item="(\d+)"',rating.group(0))
        anchor=re.search(r'<h3\b[^>]*>\s*<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',block,re.S|re.I)
        size=re.search(r'class="info"[^>]*>\s*\(([\d.,]+)\s*Kb\)',block,re.I)
        if not(model and anchor and size):continue
        source_url=urljoin('https://www.gtp-tabs.ru',unescape(anchor.group(1)))
        if not allowed_url(source_url):continue
        path=urlsplit(source_url).path
        if '/paid/' in path:continue
        score=len(re.findall(r'class="[^"]*\brealActive\b[^"]*"',rating.group(1))) if rating else 0
        if score>5:continue
        result.append({'id':model.group(1),'url':source_url,'slug':unquote(PurePosixPath(path).stem).lower(),'title':unescape(re.sub('<[^>]+>','',anchor.group(2))).strip(),'kb':float(size.group(1).replace(',','.')),'score':score or None})
    return result

def cache_source(artist_id,source):
    rows=parse_artist(source)
    if not rows:raise ValueError('Сайт не вернул список бесплатных табулатур; прежние оценки сохранены')
    stamp=time.strftime('%Y-%m-%dT%H:%M:%S%z')
    with lib.WRITE_LOCK,lib.connection() as db:
        artist=db.execute('SELECT * FROM artists WHERE id=?',(artist_id,)).fetchone()
        if not artist:raise ValueError('Исполнитель не найден')
        indexed={}
        for row in rows:
            # Slug must match exactly, including the version number.
            indexed.setdefault(row['slug'],[]).append(row)
        tabs=db.execute('SELECT id,filename,bytes,mtime FROM tabs WHERE artist_id=? AND available=1',(artist_id,)).fetchall()
        matches=[]
        for tab in tabs:
            slug=PurePosixPath(tab['filename']).stem.replace('_','-').lower()
            candidates=[r for r in indexed.get(slug,[]) if abs(tab['bytes']/1024-r['kb'])<=0.011]
            if len(candidates)==1:
                row=candidates[0]
                matches.append((row['score'],row['url'],row['id'],stamp,tab['id']))
        with db:
            # The source can return a partial page. Keep earlier confirmed matches
            # for files absent from this response; scan() invalidates replaced files.
            db.executemany('UPDATE tabs SET score=?,source_url=?,source_id=?,score_synced_at=? WHERE id=?',matches)
            for proposal in catalog_names.source_matches(tabs,rows,artist_id):
                catalog_names.write_tab_name(db,proposal,stamp)
            db.execute('UPDATE artists SET synced_at=?,sync_error=NULL WHERE id=?',(stamp,artist_id))
    # Keep a compact metadata snapshot for reproducibility, not whole web pages.
    try:
        cache=lib.DATA/'ratings';cache.mkdir(exist_ok=True)
        target=cache/(artist_id.replace('/','--')+'.json')
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps({'url':artist['source_url'],'fetched_at':stamp,'rows':rows},ensure_ascii=False),encoding='utf-8')
        temporary.replace(target)
    except OSError:
        logging.warning('Could not save optional source snapshot; imported ratings remain in SQLite')
    return {'matched':len(matches),'rated':sum(m[0] is not None for m in matches),'source_rows':len(rows)}

def sync_artist(artist_id):
    try:
        with lib.WRITE_LOCK,lib.connection() as db:
            row=db.execute('SELECT coalesce(name_source_url,source_url) source_url FROM artists WHERE id=?',(artist_id,)).fetchone()
            with db:db.execute('UPDATE artists SET last_attempt_at=? WHERE id=?',(time.time(),artist_id))
        if not row:raise ValueError('Исполнитель не найден')
        return cache_source(artist_id,fetch(row['source_url']))
    except Exception as ex:
        with lib.WRITE_LOCK,lib.connection() as db:
            with db:db.execute('UPDATE artists SET sync_error=? WHERE id=?',(str(ex)[:300],artist_id))
        raise
    finally:
        with LOCK:PENDING.discard(artist_id)

def enqueue(artist_id,force=False):
    if not artist_id:return
    with lib.connection() as db:
        a=db.execute('SELECT synced_at,sync_error,last_attempt_at FROM artists WHERE id=?',(artist_id,)).fetchone()
    if not a:return
    # Fresh successes are cached seven days; errors cool down ten minutes.
    if not force:
        if a['sync_error']:
            if a['last_attempt_at'] and time.time()-a['last_attempt_at']<600:return
        elif a['synced_at'] and time.time()-datetime.fromisoformat(a['synced_at']).timestamp()<7*86400:return
    with LOCK:
        if artist_id in PENDING:return
        PENDING.add(artist_id)
    def work():
        try:sync_artist(artist_id)
        except Exception:pass
    POOL.submit(work)

def status():
    with LOCK:return {**BULK,'pending':list(PENDING)}

def start_bulk():
    with LOCK:
        if BULK['running']:return
        BULK.update(running=True,done=0,total=0,errors=0,current='',stop=False,last_error=None)
    try:persist_sync(True)
    except OSError as ex:
        with LOCK:BULK.update(running=False,last_error=str(ex))
        raise
    def work():
        completed=False
        try:
            with lib.connection() as db:
                artists=db.execute('SELECT id,name FROM artists WHERE count>0 AND (synced_at IS NULL OR synced_at < ?) ORDER BY count DESC',((datetime.now().astimezone()-timedelta(days=7)).isoformat(),)).fetchall()
            with LOCK:BULK['total']=len(artists)
            for a in artists:
                with LOCK:
                    if BULK['stop']:break
                    BULK['current']=a['name']
                    skip=a['id'] in PENDING
                    if not skip:PENDING.add(a['id'])
                if not skip:
                    try:sync_artist(a['id'])
                    except Exception:
                        with LOCK:BULK['errors']+=1
                with LOCK:BULK['done']+=1
            completed=True
        except Exception as ex:
            with LOCK:BULK['last_error']=str(ex)
        finally:
            # Preserve the resume marker when an unexpected failure interrupts a run.
            if completed or BULK['stop']:
                try:persist_sync(False)
                except OSError as ex:
                    with LOCK:BULK['last_error']=str(ex)
            with LOCK:BULK['running']=False;BULK['current']=''
    threading.Thread(target=work,daemon=True,name='rating-library-sync').start()

def stop_bulk():
    persist_sync(False)
    with LOCK:BULK['stop']=True

def resume_if_needed():
    try:
        if json.loads(RESUME.read_text(encoding='utf-8')).get('resume_on_start'):start_bulk()
    except (FileNotFoundError,ValueError):pass
