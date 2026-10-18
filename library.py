"""Read-only archive indexing. All application data lives beside this module."""
from pathlib import Path
import contextlib, hashlib, json, math, os, re, sqlite3, threading, time, unicodedata

def catalog_sort(value):
    value=unicodedata.normalize('NFKC',value or '').casefold().replace('ё','е')
    return re.sub(r'\d+',lambda m: str(int(m[0])).zfill(20),value)

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('TABOTEKA_DATA_DIR',str(BASE / 'data')))
DATA.mkdir(parents=True,exist_ok=True)
DB = DATA / 'library.sqlite3'
CONFIG = Path(os.environ.get('TABOTEKA_CONFIG',str(BASE / 'config.json')))
WRITE_LOCK = threading.RLock()
SCAN_LOCK = threading.Lock()
EXTENSIONS = {'.gp3', '.gp4', '.gp5', '.gpx', '.gtp', '.gp'}
TRANSLIT = dict(zip('абвгдеёзийклмнопрстуфхцчыэ', ['a','b','v','g','d','e','e','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','ts','ch','y','e']))
TRANSLIT.update({'ж':'zh','ш':'sh','щ':'sch','ю':'yu','я':'ya','ь':'','ъ':''})
ALIASES = {'kino':'Кино','ariya':'Ария','korol_i_shut':'Король и Шут','agata_kristi':'Агата Кристи','akvarium':'Аквариум','alisa2':'Алиса','kipelov':'Кипелов','spleen':'Сплин','splin':'Сплин','ddt':'ДДТ','chajf':'Чайф','nayk_borzov':'Найк Борзов','najk_borzov':'Найк Борзов','sektor_gaza':'Сектор Газа','yepidemiya':'Эпидемия','zemfira':'Земфира','lyapis_trubetskoy':'Ляпис Трубецкой','grazhdanskaya_oborona':'Гражданская оборона','bi_2':'Би-2','mumiy_troll':'Мумий Тролль','beatles_the':'The Beatles','acdc':'AC/DC','abba':'ABBA','u2':'U2','zz_top':'ZZ Top','misc_computer_games':'Разное: компьютерные игры'}

def config():
    try:
        value=json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    except FileNotFoundError as ex:
        raise FileNotFoundError('Нет config.json. Сначала выполните python configure.py --tabs-root <папка архива>.') from ex
    if not isinstance(value,dict) or not isinstance(value.get('tabs_root'),str):
        raise ValueError('config.json должен содержать строковый tabs_root')
    return {'port':8766,'site_port':8765,'site_url':'http://127.0.0.1:8765','guitar_pro':'',**value}

def normalized(s):
    # Transliterate before NFKD: decomposing й first incorrectly turns it into и.
    s = ''.join(TRANSLIT.get(c,c) for c in s.lower())
    s = unicodedata.normalize('NFKD', s)
    return re.sub(r'[^a-z0-9]+',' ',s).strip()

def nice_name(s):
    return ALIASES.get(s, s.replace('_',' ').strip().title())

def category(bucket):
    return {'54':'soundtracks','55':'local','56':'lessons'}.get(bucket, 'russian' if bucket.isdigit() and 28<=int(bucket)<=53 else 'international')

@contextlib.contextmanager
def connection():
    db=sqlite3.connect(DB,timeout=30)
    db.row_factory=sqlite3.Row
    db.create_function('catalog_sort',1,catalog_sort,deterministic=True)
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
    finally:
        db.close()

def initialize():
    with connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS artists(id TEXT PRIMARY KEY,bucket TEXT,slug TEXT,name TEXT,search TEXT,category TEXT,count INTEGER DEFAULT 0,source_url TEXT,synced_at TEXT,sync_error TEXT);
        CREATE TABLE IF NOT EXISTS tabs(id TEXT PRIMARY KEY,artist_id TEXT REFERENCES artists(id),path TEXT UNIQUE,filename TEXT,title TEXT,search TEXT,format TEXT,bytes INTEGER,mtime REAL,favorite INTEGER DEFAULT 0,practice TEXT DEFAULT '',notes TEXT DEFAULT '',opened_at REAL,score INTEGER,source_url TEXT,source_id TEXT,score_synced_at TEXT,available INTEGER DEFAULT 1);
        CREATE INDEX IF NOT EXISTS ix_tabs_artist ON tabs(artist_id,available);
        CREATE INDEX IF NOT EXISTS ix_tabs_favorite ON tabs(favorite,available);
        CREATE INDEX IF NOT EXISTS ix_tabs_practice ON tabs(practice,available);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        ''')
        columns={row['name'] for row in db.execute('PRAGMA table_info(artists)')}
        if 'last_attempt_at' not in columns:
            db.execute('ALTER TABLE artists ADD COLUMN last_attempt_at REAL')
        additions={
            'artists':{'display_name':'TEXT','name_source_url':'TEXT','name_method':'TEXT','name_updated_at':'TEXT','name_search':"TEXT NOT NULL DEFAULT ''"},
            'tabs':{'display_title':'TEXT','title_source_url':'TEXT','title_method':'TEXT','title_updated_at':'TEXT','name_search':"TEXT NOT NULL DEFAULT ''"},
        }
        for table,fields in additions.items():
            existing={row['name'] for row in db.execute('PRAGMA table_info('+table+')')}
            for column,declaration in fields.items():
                if column not in existing:db.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' '+declaration)
        db.commit()

def scan():
    if not SCAN_LOCK.acquire(blocking=False):
        return {'running':True}
    try:
        root=Path(config()['tabs_root']).resolve(strict=True)
        rows=[]
        artist_records={}
        errors=[]
        # Build the entire new index before committing; a partial traversal never removes old entries.
        def onerror(error):
            errors.append(str(error))
        for current,dirs,files in os.walk(root,onerror=onerror,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(current)/d).is_symlink() and not (getattr(Path(current)/d,'is_junction',lambda:False)())]
            for filename in files:
                path=Path(current)/filename
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    continue
                rel=path.relative_to(root)
                if len(rel.parts)<3:
                    continue
                bucket,slug=rel.parts[:2]
                artist_id=bucket+'/'+slug
                aid_name=nice_name(slug)
                artist_records[artist_id]=(artist_id,bucket,slug,aid_name,normalized(slug+' '+aid_name),category(bucket),f'https://www.gtp-tabs.ru/n/tabs/{bucket}/{slug}.html')
                try:
                    st=path.stat()
                except OSError as ex:
                    errors.append(str(ex));continue
                stem=path.stem
                variant=re.search(r'_(\d+)$',stem)
                title=nice_name(stem)
                # Keep every version. The untouched original filename is always visible.
                if variant and (path.parent/(stem[:variant.start()]+path.suffix)).exists():
                    title=nice_name(stem[:variant.start()])+' · версия '+variant.group(1)
                reltext=rel.as_posix()
                tab_id=hashlib.sha1(reltext.encode('utf-8')).hexdigest()[:20]
                rows.append((tab_id,artist_id,reltext,filename,title,normalized(slug+' '+aid_name+' '+stem),path.suffix[1:].upper(),st.st_size,st.st_mtime))
        if errors:
            raise OSError('Не удалось полностью прочитать архив: '+errors[0])
        with WRITE_LOCK,connection() as db:
            with db:
                previous={r['id']:(r['bytes'],r['mtime']) for r in db.execute('SELECT id,bytes,mtime FROM tabs')}
                changed_artists={r[1] for r in rows if previous.get(r[0])!=(r[7],r[8])}
                db.execute('UPDATE tabs SET available=0')
                db.executemany('''INSERT INTO artists(id,bucket,slug,name,search,category,source_url) VALUES(?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET bucket=excluded.bucket,slug=excluded.slug,name=excluded.name,search=excluded.search,category=excluded.category,source_url=excluded.source_url''',artist_records.values())
                db.executemany('''INSERT INTO tabs(id,artist_id,path,filename,title,search,format,bytes,mtime) VALUES(?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET available=1,title=excluded.title,search=excluded.search,
                  score=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.score END,
                  source_url=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.source_url END,
                  source_id=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.source_id END,
                  score_synced_at=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.score_synced_at END,
                  display_title=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.display_title END,
                  title_source_url=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.title_source_url END,
                  title_method=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.title_method END,
                  title_updated_at=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN NULL ELSE tabs.title_updated_at END,
                  name_search=CASE WHEN tabs.bytes!=excluded.bytes OR tabs.mtime!=excluded.mtime THEN '' ELSE tabs.name_search END,
                  bytes=excluded.bytes,mtime=excluded.mtime''',rows)
                # A replacement/new file needs source metadata even if its artist cache is fresh.
                db.executemany('UPDATE artists SET synced_at=NULL WHERE id=?',[(a,) for a in changed_artists])
                db.execute('UPDATE artists SET count=(SELECT count(*) FROM tabs WHERE artist_id=artists.id AND available=1)')
                db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('scanned_at',time.strftime('%Y-%m-%dT%H:%M:%S%z')))
        return {'files':len(rows),'artists':len(artist_records)}
    finally:
        SCAN_LOCK.release()

def stats():
    with connection() as db:
        r=dict(db.execute('''SELECT count(*) files, count(DISTINCT artist_id) artists,coalesce(sum(bytes),0) bytes,
          coalesce(sum(favorite),0) favorites,coalesce(sum(practice='learn'),0) learning,coalesce(sum(practice='play'),0) playing,
          coalesce(sum(opened_at IS NOT NULL),0) recent,coalesce(sum(score IS NOT NULL),0) rated,
          coalesce(sum(source_url IS NOT NULL),0) matched,
          coalesce(sum(display_title IS NOT NULL),0) named,
          coalesce(sum(title_method='source_exact'),0) source_named,
          coalesce(sum(title_method='file_header'),0) header_named FROM tabs WHERE available=1''').fetchone())
        r['synced_artists']=db.execute('SELECT count(*) FROM artists WHERE synced_at IS NOT NULL AND count>0').fetchone()[0]
        r['named_artists']=db.execute('SELECT count(*) FROM artists WHERE display_name IS NOT NULL AND count>0').fetchone()[0]
        r['scanned_at']=db.execute("SELECT value FROM meta WHERE key='scanned_at'").fetchone()
        r['scanned_at']=r['scanned_at'][0] if r['scanned_at'] else None
        return r

def _filters(args):
    clauses=['t.available=1']; values=[]
    artist=args.get('artist','')
    if artist: clauses.append('t.artist_id=?'); values.append(artist)
    view=args.get('view','all')
    if view=='favorites': clauses.append('t.favorite=1')
    elif view=='recent': clauses.append('t.opened_at IS NOT NULL')
    elif view in ('learn','play'): clauses.append('t.practice=?'); values.append(view)
    elif view in ('soundtracks','lessons','local','russian','international'): clauses.append('a.category=?');values.append(view)
    fmt=args.get('format','')
    if fmt: clauses.append('t.format=?');values.append(fmt)
    for token in normalized(args.get('q','')).split():
        clauses.append('(t.search LIKE ? OR a.search LIKE ? OR t.name_search LIKE ? OR a.name_search LIKE ?)'); values.extend(['%'+token+'%']*4)
    return ' AND '.join(clauses),values

def list_tabs(args):
    where,values=_filters(args)
    page=max(1,min(100000,int(args.get('page',1)))); size=40
    by_name='catalog_sort(coalesce(a.display_name,a.name)),catalog_sort(coalesce(t.display_title,t.title)),t.filename'
    order={'name':by_name,'rating':'t.score IS NULL,t.score DESC,'+by_name,'recent':'t.opened_at DESC','size':'t.bytes DESC'}.get(args.get('sort',''),'t.opened_at DESC' if args.get('view')=='recent' else by_name)
    with connection() as db:
        total=db.execute('SELECT count(*) FROM tabs t JOIN artists a ON a.id=t.artist_id WHERE '+where,values).fetchone()[0]
        page=min(page,max(1,(total+size-1)//size))
        rows=db.execute('''SELECT t.*,coalesce(a.display_name,a.name) artist,a.name original_artist,a.name_source_url artist_name_source,a.category FROM tabs t JOIN artists a ON a.id=t.artist_id WHERE '''+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',values+[size,(page-1)*size]).fetchall()
        return {'items':[_display_tab(r) for r in rows],'total':total,'page':page,'pages':max(1,(total+size-1)//size)}

def list_artists(args):
    # Count within the current collection/search so the artist column stays relevant.
    args=dict(args);args.pop('artist',None);args.pop('format',None)
    where,values=_filters(args)
    for token in normalized(args.get('aq','')).split():
        where+=' AND (a.search LIKE ? OR a.name_search LIKE ?)';values.extend(['%'+token+'%']*2)
    with connection() as db:
        rows=db.execute('''SELECT a.*,count(t.id) filtered_count FROM artists a JOIN tabs t ON t.artist_id=a.id WHERE '''+where+' GROUP BY a.id ORDER BY filtered_count DESC,catalog_sort(coalesce(a.display_name,a.name)) LIMIT 250',values).fetchall()
        return [{**dict(r),'original_name':r['name'],'name':r['display_name'] or r['name']} for r in rows]

def _display_tab(row):
    tab=dict(row)
    tab['original_title']=tab['title']
    tab['title']=tab['display_title'] or tab['title']
    return tab

def get_tab(tab_id):
    with connection() as db:
        row=db.execute('SELECT t.*,coalesce(a.display_name,a.name) artist,a.name original_artist,a.name_source_url artist_name_source FROM tabs t JOIN artists a ON a.id=t.artist_id WHERE t.id=? AND available=1',(tab_id,)).fetchone()
        if not row:raise KeyError('Файл не найден в каталоге')
        return _display_tab(row)

def safe_file(tab_id):
    tab=get_tab(tab_id)
    root=Path(config()['tabs_root']).resolve(strict=True)
    path=(root/tab['path']).resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() not in EXTENSIONS or not path.is_file():
        raise ValueError('Файл находится вне архива')
    return path

def update_tab(tab_id,body):
    get_tab(tab_id)
    allowed={k:v for k,v in body.items() if k in ('favorite','practice','notes')}
    if 'favorite' in allowed:
        if not isinstance(allowed['favorite'],(bool,int)) or allowed['favorite'] not in (0,1):raise ValueError('favorite должен быть true или false')
        allowed['favorite']=int(allowed['favorite'])
    if 'practice' in allowed and allowed['practice'] not in ('','learn','play'):raise ValueError('Неизвестный список')
    if 'notes' in allowed:
        if not isinstance(allowed['notes'],str) or len(allowed['notes'])>20000:raise ValueError('Заметка должна быть строкой до 20000 символов')
    if allowed:
        with WRITE_LOCK,connection() as db:
            with db:db.execute('UPDATE tabs SET '+','.join(k+'=?' for k in allowed)+' WHERE id=?',list(allowed.values())+[tab_id])
    return get_tab(tab_id)

def restore_personal_data(body):
    """Validate the entire backup before writing; a bad row never partly restores it."""
    if body.get('version')!=1 or not isinstance(body.get('items'),list):
        raise ValueError('Неизвестный формат резервной копии')
    if len(body['items'])>100000:raise ValueError('Слишком много записей в резервной копии')
    rows=[]
    for item in body['items']:
        if not isinstance(item,dict):raise ValueError('Некорректная запись резервной копии')
        path=item.get('path')
        if not isinstance(path,str) or not path or path.startswith(('/','\\')) or ':' in path or '..' in path.replace('\\','/').split('/'):
            raise ValueError('Некорректный относительный путь в резервной копии')
        favorite=item.get('favorite',False)
        if not isinstance(favorite,(bool,int)) or favorite not in (0,1):raise ValueError('Некорректное избранное в резервной копии')
        practice=item.get('practice','')
        if practice not in ('','learn','play'):raise ValueError('Некорректный список занятий')
        notes=item.get('notes','')
        if not isinstance(notes,str) or len(notes)>20000:raise ValueError('Некорректная заметка')
        opened=item.get('opened_at')
        if opened is not None:
            if isinstance(opened,bool) or not isinstance(opened,(float,int)) or not math.isfinite(opened) or opened<0:raise ValueError('Некорректное время открытия')
        rows.append((int(favorite),practice,notes,opened,path.replace('\\','/')))
    count=0
    with WRITE_LOCK,connection() as db:
        with db:
            for row in rows:
                count+=db.execute('UPDATE tabs SET favorite=?,practice=?,notes=?,opened_at=? WHERE path=?',row).rowcount
    return {'restored':count}
