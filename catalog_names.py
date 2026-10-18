"""Evidence-backed display names; archive paths and original search tokens never change."""
import argparse
from collections import Counter,defaultdict
from html import unescape
from html.parser import HTMLParser
import json,math,re,struct,time
from pathlib import Path,PurePosixPath
from urllib.parse import unquote,urljoin,urlsplit

import library as lib

# Reviewed against the current alphabet indexes. These are changed URLs and
# historical archive spellings, not approximate matches between different bands.
ARTIST_REDIRECTS={
    '30/vinitskiy-aleksandr':'28/aleksandr-vinitskiy',
    '40/narodnyie-pesni':'40/narodnye',
    '43/rozembaum-aleksandr':'43/rozenbaum-aleksandr',
    '44/stryikalo-valentin':'30/stryikalo-valentin',
}

def clean_name(value):
    if not isinstance(value,str):return None
    value=' '.join(unescape(value).split())
    if not value or len(value)>400 or '\ufffd' in value or any(ord(c)<32 for c in value):return None
    return value

def source_path(url):
    if not isinstance(url,str):return None
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='www.gtp-tabs.ru' or parsed.query or parsed.fragment:return None
    return unquote(parsed.path)

def artist_key(artist_id):
    key=artist_id.replace('_','-').casefold()
    return ARTIST_REDIRECTS.get(key,key)

def display_artist_name(slug,source_name):
    # The source sometimes uses a filing convention rather than the band's name.
    # Keep established editorial labels and put explicit trailing (The) first.
    if slug in lib.ALIASES:return lib.ALIASES[slug]
    article=re.fullmatch(r'(.+?)\s*\(The\)',source_name,re.I)
    return 'The '+article[1].strip() if article else source_name

def row_belongs(row,artist_id):
    path=source_path(row.get('url',''))
    if not path:return False
    parts=path.split('/')
    return len(parts)==6 and parts[1:3]==['n','tabs'] and artist_key('/'.join(parts[3:5]))==artist_key(artist_id) and parts[5].endswith('.html')

class ArtistIndexParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchor=None;self.rows=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            href=dict(attrs).get('href','')
            url=urljoin('https://www.gtp-tabs.ru',href)
            path=source_path(url)
            match=re.fullmatch(r'/n/tabs/(\d+)/([^/]+)\.html',path or '')
            self.anchor={'id':match[1]+'/'+match[2],'url':url,'parts':[]} if match else None
    def handle_data(self,data):
        if self.anchor:self.anchor['parts'].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor:
            name=clean_name(''.join(self.anchor.pop('parts')))
            if name:self.rows.append({**self.anchor,'name':name})
            self.anchor=None

def parse_artist_index(html):
    parser=ArtistIndexParser();parser.feed(html)
    return parser.rows

def names_folder():
    folder=lib.DATA/'names';folder.mkdir(exist_ok=True)
    return folder

def write_json(target,value):
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(target)

def fetch_indexes(buckets):
    import ratings
    folder=names_folder()/'artist-index';folder.mkdir(exist_ok=True)
    results=[]
    for bucket in buckets:
        target=folder/(str(bucket)+'.json')
        if target.exists():
            try:
                cached=json.loads(target.read_text(encoding='utf-8'))
                if time.time()-cached.get('fetched_epoch',0)<7*86400 and cached.get('rows'):
                    results.append({'bucket':bucket,'cached':True,'rows':len(cached['rows'])});continue
            except (ValueError,OSError):pass
        url=f'https://www.gtp-tabs.ru/n/tabs/{bucket}/'
        try:
            rows=[r for r in parse_artist_index(ratings.fetch(url)) if r['id'].split('/')[0]==str(bucket)]
            if not rows:raise ValueError('No artist links returned')
            write_json(target,{'url':url,'fetched_epoch':time.time(),'rows':rows})
            result={'bucket':bucket,'rows':len(rows)}
        except Exception as ex:result={'bucket':bucket,'error':str(ex)}
        results.append(result);print(json.dumps(result,ensure_ascii=True),flush=True)
    return results

def source_matches(tabs,rows,artist_id):
    indexed=defaultdict(list)
    for row in rows:
        if not isinstance(row,dict) or not row_belongs(row,artist_id):continue
        title=clean_name(row.get('title'))
        kb=row.get('kb')
        if not title or isinstance(kb,bool) or not isinstance(kb,(int,float)) or not math.isfinite(kb) or kb<0:continue
        slug=PurePosixPath(source_path(row['url'])).stem.casefold()
        if not isinstance(row.get('slug'),str) or row['slug'].casefold()!=slug:continue
        indexed[slug].append(row)
    result=[]
    for tab in tabs:
        slug=PurePosixPath(tab['filename']).stem.replace('_','-').casefold()
        matches=[r for r in indexed.get(slug,[]) if abs(tab['bytes']/1024-r['kb'])<=0.011]
        # Duplicate rows with the same evidence are harmless; conflicting titles are not.
        unique={(clean_name(r['title']),r['url']) for r in matches}
        if len(unique)==1:
            name,url=next(iter(unique))
            result.append({'entity':'tab','id':tab['id'],'artist_id':artist_id,'name':name,'source_url':url,'method':'source_exact','bytes':tab['bytes'],'mtime':tab['mtime']})
    return result

def detail_name(html,url,tab,artist_id):
    """Validate the primary score heading and size, excluding related-song lists."""
    heading=re.search(r'<h1\b[^>]*>(.*?)</h1>',html,re.S|re.I)
    if not heading:return None
    text=clean_name(re.sub('<[^>]+>',' ',heading[1]))
    if not text or ' - ' not in text:return None
    title=clean_name(text.split(' - ',1)[1])
    # Related songs below this marker have their own sizes and must not be used.
    main=html[heading.end():].split('Другие табулатуры',1)[0]
    plain=' '.join(unescape(re.sub('<[^>]+>',' ',main)).split())
    size=re.search(r'Размер:\s*([\d.,]+)\s*Kb\b',plain,re.I)
    path=source_path(url)
    if not title or not size or not path:return None
    row={'url':url,'slug':PurePosixPath(path).stem.casefold(),'title':title,'kb':float(size[1].replace(',','.'))}
    matches=source_matches([tab],[row],artist_id)
    return matches[0] if matches else None

def read_gp_header(path,russian=False):
    """Read at most 12 KiB of GP3/4/5 score info, never notes or musical content."""
    with path.open('rb') as stream:
        header=stream.read(31)
        if len(header)!=31 or not 1<=header[0]<=30:return None
        version=header[1:1+header[0]]
        if not re.fullmatch(br'FICHIER GUITAR PRO v[345]\.\d{2}',version):return None
        info=[]
        for _ in range(3):
            raw_length=stream.read(4)
            if len(raw_length)!=4:return None
            length=struct.unpack('<i',raw_length)[0]
            if not 1<=length<=4096:return None
            raw=stream.read(length)
            if len(raw)!=length or raw[0]!=length-1:return None
            raw=raw[1:]
            try:value=raw.decode('utf-8')
            except UnicodeDecodeError:value=raw.decode('cp1251' if russian else 'cp1252',errors='replace')
            info.append(clean_name(value))
    return dict(zip(('title','subtitle','artist'),info))

def translit_keys(value):
    """Only documented spelling variants; no fuzzy similarity for automatic acceptance."""
    variants=[lib.normalized(value)]
    for replacements in ({'ж':'j','ы':'yi'},{'й':'j','ы':'y'},{'х':'kh','щ':'shch','ц':'c'}):
        table={**lib.TRANSLIT,**replacements}
        variants.append(''.join(table.get(c,c) for c in value.casefold()))
    return {key for v in variants if (key:=re.sub('[^a-z0-9]','',v.casefold()))}

def stem_and_version(filename,siblings):
    stem=PurePosixPath(filename).stem
    variant=re.fullmatch(r'(.+)_(\d+)',stem)
    if variant and variant[1] in siblings:return variant[1],variant[2]
    return stem,None

def build_plan(scope='all',include_headers=False):
    with lib.connection() as db:
        clause=" AND a.category='russian'" if scope=='russian' else ''
        artists=[dict(r) for r in db.execute('SELECT a.* FROM artists a WHERE a.count>0'+clause)]
        tabs=[dict(r) for r in db.execute('SELECT t.*,a.category,a.slug FROM tabs t JOIN artists a ON a.id=t.artist_id WHERE t.available=1'+clause)]
    grouped=defaultdict(list)
    for tab in tabs:grouped[tab['artist_id']].append(tab)
    artist_rows=defaultdict(set)
    for path in (names_folder()/'artist-index').glob('*.json'):
        try:cached=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError):continue
        for row in cached.get('rows',[]):
            name=clean_name(row.get('name'));key=row.get('id','')
            if name and source_path(row.get('url',''))==f'/n/tabs/{key}.html':artist_rows[artist_key(key)].add((name,row['url']))
    proposals=[];candidates=[];headers={};errors=[]
    for artist in artists:
        names=artist_rows.get(artist_key(artist['id']),set())
        if len(names)==1:
            name,url=next(iter(names))
            method='artist_alias' if artist['id'].replace('_','-').casefold() in ARTIST_REDIRECTS else 'artist_index'
            proposals.append({'entity':'artist','id':artist['id'],'name':display_artist_name(artist['slug'],name),'source_url':url,'method':method})
        elif len(names)>1:candidates.append({'entity':'artist','id':artist['id'],'reason':'conflicting_source_names','names':sorted(names)})
        target=lib.DATA/'ratings'/(artist['id'].replace('/','--')+'.json')
        try:cached=json.loads(target.read_text(encoding='utf-8'))
        except (ValueError,OSError):cached={}
        proposals.extend(source_matches(grouped[artist['id']],cached.get('rows',[]),artist['id']))
    # Small independently checked detail-page results augment artist lists whose
    # large pages sometimes fail at the upstream site.
    try:details=json.loads((names_folder()/'detail-names.json').read_text(encoding='utf-8'))
    except (ValueError,OSError):details=[]
    tabs_by_id={t['id']:t for t in tabs}
    for proposal in details:
        tab=tabs_by_id.get(proposal.get('id'))
        if not tab or proposal.get('method')!='source_exact':continue
        if (tab['bytes'],tab['mtime'])!=(proposal.get('bytes'),proposal.get('mtime')):continue
        if not row_belongs({'url':proposal.get('source_url','')},tab['artist_id']):continue
        proposals=[p for p in proposals if p['entity']!='tab' or p['id']!=tab['id']]
        proposals.append(proposal)
    known={p['id'] for p in proposals if p['entity']=='tab'}
    if include_headers:
        root=Path(lib.config()['tabs_root']).resolve(strict=True)
        sibling_stems=defaultdict(set)
        for tab in tabs:sibling_stems[(tab['artist_id'],str(PurePosixPath(tab['path']).parent))].add(PurePosixPath(tab['filename']).stem)
        for tab in tabs:
            if tab['id'] in known:continue
            if tab['format'] not in ('GP3','GP4','GP5'):continue
            try:
                path=(root/tab['path']).resolve(strict=True)
                if not path.is_relative_to(root):continue
                st=path.stat()
                if (st.st_size,st.st_mtime)!=(tab['bytes'],tab['mtime']):continue
                info=read_gp_header(path,russian=tab['category']=='russian')
            except OSError as ex:errors.append({'id':tab['id'],'error':str(ex)});continue
            if not info:continue
            stem,version=stem_and_version(tab['filename'],sibling_stems[(tab['artist_id'],str(PurePosixPath(tab['path']).parent))])
            choices=[value for value in (info['title'],info['subtitle']) if value and (tab['category']!='russian' or re.search('[А-Яа-яЁё]',value))]
            matches={value for value in choices if translit_keys(value)&translit_keys(stem)}
            if len(matches)==1:
                name=next(iter(matches))
                if version:name+=' · версия '+version
                proposals.append({'entity':'tab','id':tab['id'],'artist_id':tab['artist_id'],'name':name,'source_url':None,'method':'file_header','bytes':tab['bytes'],'mtime':tab['mtime']})
                known.add(tab['id'])
            elif choices:candidates.append({'entity':'tab','id':tab['id'],'reason':'header_does_not_match_filename','names':choices,'filename':tab['filename']})
    # Numeric variants are review candidates only: a digit can also be part of
    # a different composition's actual name. Qualifiers are never removed.
    bases={};tabs_by_id={t['id']:t for t in tabs}
    for proposal in proposals:
        if proposal['entity']!='tab':continue
        tab=tabs_by_id.get(proposal['id'])
        if tab:bases[(tab['artist_id'],str(PurePosixPath(tab['path']).parent),PurePosixPath(tab['filename']).stem)]=proposal
    for tab in tabs:
        if tab['id'] in known:continue
        stem=PurePosixPath(tab['filename']).stem
        variant=re.fullmatch(r'(.+)_(\d+)',stem)
        if not variant:continue
        base=bases.get((tab['artist_id'],str(PurePosixPath(tab['path']).parent),variant[1]))
        if not base:continue
        candidates.append({'entity':'tab','id':tab['id'],'artist_id':tab['artist_id'],'reason':'numeric_variant_requires_source','name':base['name']+' · версия '+variant[2],'base_id':base['id'],'filename':tab['filename']})
    # Findings from a live pilot can hold a specific file/method for review.
    # An independently confirmed source_exact match is still allowed later.
    try:holds=json.loads((names_folder()/'review-holds.json').read_text(encoding='utf-8'))
    except (ValueError,OSError):holds=[]
    held={(h.get('id'),h.get('method'),h.get('bytes'),h.get('mtime')) for h in holds}
    accepted=[]
    for proposal in proposals:
        if (proposal['id'],proposal['method'],proposal.get('bytes'),proposal.get('mtime')) in held:
            candidates.append({**proposal,'reason':'pilot_requires_review'});known.discard(proposal['id'])
        else:accepted.append(proposal)
    return {'scope':scope,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'artists_examined':len(artists),'files_examined':len(tabs),'methods':dict(Counter(p['method'] for p in accepted)),'proposals':accepted,'candidates':[c for c in candidates if c['id'] not in known],'errors':errors}

def write_tab_name(db,proposal,stamp):
    name=clean_name(proposal['name'])
    if not name or proposal['method'] not in ('source_exact','file_header','reviewed'):raise ValueError('Invalid tab name proposal')
    if proposal['method']=='source_exact' and not row_belongs({'url':proposal.get('source_url','')},proposal['artist_id']):raise ValueError('Invalid title source')
    return db.execute('''UPDATE tabs SET display_title=?,title_source_url=?,title_method=?,title_updated_at=?,name_search=? WHERE id=? AND available=1 AND bytes=? AND mtime=? AND (title_method IS NULL OR title_method!='source_exact' OR ?='source_exact')''',(name,proposal['source_url'],proposal['method'],stamp,lib.normalized(name),proposal['id'],proposal['bytes'],proposal['mtime'],proposal['method'])).rowcount

def apply_plan(plan):
    """Compare file fingerprints inside the transaction; stale plans cannot rename replacements."""
    counts=Counter();stamp=time.strftime('%Y-%m-%dT%H:%M:%S%z')
    with lib.WRITE_LOCK,lib.connection() as db:
        with db:
            for proposal in plan['proposals']:
                name=clean_name(proposal['name'])
                if not name:raise ValueError('Invalid display name')
                if proposal['entity']=='artist':
                    changed=db.execute('UPDATE artists SET display_name=?,name_source_url=?,name_method=?,name_updated_at=?,name_search=? WHERE id=? AND count>0',(name,proposal['source_url'],proposal['method'],stamp,lib.normalized(name),proposal['id'])).rowcount
                elif proposal['entity']=='tab':
                    changed=write_tab_name(db,proposal,stamp)
                else:raise ValueError('Unknown name entity')
                counts[proposal['entity']]+=changed
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('names_updated_at',stamp))
    return dict(counts)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-indexes',choices=('russian','all'))
    parser.add_argument('--scope',choices=('russian','all'),default='all')
    parser.add_argument('--headers',action='store_true',help='Read bounded Russian GP3/4/5 headers without writing files')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();lib.initialize()
    if args.fetch_indexes:fetch_indexes(range(28,54) if args.fetch_indexes=='russian' else range(1,57))
    plan=build_plan(args.scope,args.headers)
    write_json(names_folder()/('plan-'+args.scope+'.json'),plan)
    summary={k:v for k,v in plan.items() if k not in ('proposals','candidates','errors')}
    summary.update(candidates=len(plan['candidates']),errors=len(plan['errors']))
    if args.apply:summary['applied']=apply_plan(plan)
    print(json.dumps(summary,ensure_ascii=True),flush=True)

if __name__=='__main__':main()
