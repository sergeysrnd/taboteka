"""Portable offline regression suite; all archive files are generated in a temp folder."""
import contextlib,json,os,sqlite3,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
BOOT=tempfile.TemporaryDirectory()
BOOT_ROOT=Path(BOOT.name)
(BOOT_ROOT/'config.json').write_text(json.dumps({'tabs_root':str(BOOT_ROOT),'guitar_pro':''}),encoding='utf-8')
os.environ['TABOTEKA_DATA_DIR']=str(BOOT_ROOT/'data')
os.environ['TABOTEKA_CONFIG']=str(BOOT_ROOT/'config.json')
import library as lib
import ratings,server,windows_open

def source_row(slug='song',kb=1,stars=4,reverse=False):
    attrs='data-item="1" data-model="Tab"' if reverse else 'data-model="Tab" data-item="1"'
    return f'<li><h3><a href="/n/tabs/13/test_artist/{slug}.html">Song</a></h3><em class="info">({kb} Kb)</em><div class="rating ratingSmall" {attrs}>'+''.join('<a class="realActive active">1</a>' for _ in range(stars))+'</div></li>'

class ArchiveCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'archive';self.folder=self.root/'13'/'test_artist';self.folder.mkdir(parents=True)
        (self.folder/'song.gp4').write_bytes(b'a'*1024)
        (self.folder/'song_2.gp5').write_bytes(b'b'*2048)
        self.exe=self.base/'GuitarPro.exe';self.exe.write_bytes(b'test-only')
        config=self.base/'config.json';config.write_text(json.dumps({'tabs_root':str(self.root),'guitar_pro':str(self.exe)}),encoding='utf-8')
        data=self.base/'data';data.mkdir()
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        for key,value in {'DB':data/'library.sqlite3','DATA':data,'CONFIG':config}.items():self.stack.enter_context(patch.object(lib,key,value))
        self.stack.enter_context(patch.object(ratings,'RESUME',data/'ratings-sync.json'))
        self.stack.enter_context(patch.object(ratings,'PENDING',set()))
        self.stack.enter_context(patch.object(ratings,'BULK',{'running':False,'done':0,'total':0,'errors':0,'current':'','stop':False,'last_error':None}))
        lib.initialize();lib.scan()
        self.tab=next(t for t in lib.list_tabs({})['items'] if t['filename']=='song.gp4')

class IndexTests(ArchiveCase):
    def test_initial_index_and_independent_versions(self):
        self.assertEqual(lib.stats()['files'],2)
        self.assertEqual(lib.list_tabs({'format':'GP5'})['total'],1)
        self.assertEqual(lib.list_tabs({'q':'song'})['total'],2)
    def test_rescan_preserves_personal_data(self):
        lib.update_tab(self.tab['id'],{'favorite':True,'notes':'Keep this','practice':'learn'})
        lib.scan();self.assertEqual(lib.get_tab(self.tab['id'])['notes'],'Keep this')
        self.assertEqual(lib.stats()['favorites'],1)
    def test_missing_archive_does_not_erase_index(self):
        lib.CONFIG.write_text(json.dumps({'tabs_root':str(self.base/'missing')}),encoding='utf-8')
        with self.assertRaises(FileNotFoundError):lib.scan()
        self.assertEqual(lib.stats()['files'],2)
    def test_disappeared_file_is_hidden_but_personal_data_retained(self):
        lib.update_tab(self.tab['id'],{'favorite':True})
        (self.folder/'song.gp4').unlink();lib.scan();self.assertEqual(lib.stats()['files'],1)
        with lib.connection() as db:self.assertEqual(db.execute('SELECT favorite FROM tabs WHERE id=?',(self.tab['id'],)).fetchone()[0],1)
    def test_file_replacement_invalidates_rating(self):
        ratings.cache_source('13/test_artist',source_row())
        lib.update_tab(self.tab['id'],{'notes':'Keep'})
        (self.folder/'song.gp4').write_bytes(b'changed')
        lib.scan();row=lib.get_tab(self.tab['id'])
        self.assertIsNone(row['score']);self.assertIsNone(row['source_url']);self.assertEqual(row['notes'],'Keep')
    def test_unchanged_rescan_preserves_rating(self):
        ratings.cache_source('13/test_artist',source_row());lib.scan()
        self.assertEqual(lib.get_tab(self.tab['id'])['score'],4)
    def test_safe_file_is_confined_to_archive(self):
        self.assertEqual(lib.safe_file(self.tab['id']),self.folder/'song.gp4')
        with self.assertRaises(KeyError):lib.safe_file('../../outside')
    def test_symlinks_are_not_indexed(self):
        outside=self.base/'outside.gp5';outside.write_bytes(b'outside')
        try:(self.folder/'linked.gp5').symlink_to(outside)
        except OSError:self.skipTest('Symlink creation requires developer mode or elevated rights on this host')
        lib.scan();self.assertEqual(lib.stats()['files'],2)
    def test_transliteration_preserves_cyrillic_short_i(self):
        self.assertEqual(lib.normalized('Кино Группа крови'),'kino gruppa krovi')
        self.assertEqual(lib.normalized('Мой'),'moy')
        self.assertEqual(lib.normalized('Й Ё'),'y e')
    def test_user_updates_never_change_archive_content(self):
        before={p.name:p.read_bytes() for p in self.folder.iterdir()}
        lib.update_tab(self.tab['id'],{'notes':'Practice','favorite':True})
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.folder.iterdir()})
    def test_strict_personal_field_validation(self):
        for changes in ({'favorite':'false'},{'notes':{}},{'practice':'invalid'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):lib.update_tab(self.tab['id'],changes)
    def test_backup_round_trip(self):
        result=lib.restore_personal_data({'version':1,'items':[{'path':self.tab['path'],'favorite':1,'notes':'Practice','practice':'learn','opened_at':100}]})
        self.assertEqual(result['restored'],1);self.assertEqual(lib.get_tab(self.tab['id'])['notes'],'Practice')
    def test_backup_validation_is_atomic(self):
        with self.assertRaises(ValueError):lib.restore_personal_data({'version':1,'items':[{'path':self.tab['path'],'favorite':1},{'path':'../outside'}]})
        self.assertEqual(lib.get_tab(self.tab['id'])['favorite'],0)
    def test_backup_rejects_nonfinite_time_and_invalid_rows(self):
        for item in ({'path':self.tab['path'],'opened_at':float('nan')},[],{'path':self.tab['path'],'notes':[]},{'path':self.tab['path'],'favorite':'false'}):
            with self.subTest(item=item),self.assertRaises(ValueError):lib.restore_personal_data({'version':1,'items':[item]})

class RatingTests(ArchiveCase):
    def test_exact_version_and_size_are_required(self):
        result=ratings.cache_source('13/test_artist',source_row()+source_row('song-2',999,5))
        self.assertEqual(result['matched'],1);self.assertEqual(lib.get_tab(self.tab['id'])['score'],4)
    def test_partial_response_preserves_previous_confirmed_rating(self):
        ratings.cache_source('13/test_artist',source_row()+source_row('song-2',2,5))
        ratings.cache_source('13/test_artist',source_row(stars=3))
        versions=lib.list_tabs({})['items'];self.assertEqual(sorted(t['score'] for t in versions),[3,5])
    def test_no_votes_are_not_a_zero_rating(self):
        ratings.cache_source('13/test_artist',source_row(stars=0));self.assertIsNone(lib.get_tab(self.tab['id'])['score'])
    def test_empty_source_preserves_cache(self):
        ratings.cache_source('13/test_artist',source_row())
        with self.assertRaises(ValueError):ratings.cache_source('13/test_artist','<html>unavailable</html>')
        self.assertEqual(lib.get_tab(self.tab['id'])['score'],4)
    def test_attribute_order_does_not_change_rating(self):
        self.assertEqual(ratings.parse_artist(source_row(reverse=True))[0]['score'],4)
    def test_external_source_links_are_rejected(self):
        self.assertEqual(ratings.parse_artist(source_row().replace('/n/tabs/13/test_artist/song.html','https://example.org/n/tabs/song.html')),[])
    def test_redirect_is_blocked_before_contacting_other_host(self):
        req=Request('https://www.gtp-tabs.ru/n/tabs/13/test_artist.html')
        with self.assertRaises(ValueError):ratings.SourceRedirects().redirect_request(req,None,302,'Found',{},'https://example.org/n/tabs/test.html')
    def test_snapshot_failure_does_not_undo_database_import(self):
        with patch.object(Path,'write_text',side_effect=OSError('disk unavailable')):
            self.assertEqual(ratings.cache_source('13/test_artist',source_row())['matched'],1)
        self.assertEqual(lib.get_tab(self.tab['id'])['score'],4)
    def test_retryable_http_error_retries(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'page'
        opener=Mock();opener.open.side_effect=[HTTPError('url',503,'Unavailable',{},None),response]
        with patch.object(ratings,'build_opener',return_value=opener),patch.object(ratings.time,'sleep'):
            self.assertEqual(ratings.fetch('https://www.gtp-tabs.ru/n/tabs/13/test_artist.html'),'page')
        self.assertEqual(opener.open.call_count,2)
    def test_permanent_http_error_is_not_retried(self):
        opener=Mock();opener.open.side_effect=HTTPError('url',404,'Missing',{},None)
        with patch.object(ratings,'build_opener',return_value=opener),patch.object(ratings.time,'sleep'),self.assertRaises(HTTPError):ratings.fetch('https://www.gtp-tabs.ru/n/tabs/13/test_artist.html')
        self.assertEqual(opener.open.call_count,1)
    def test_failed_artist_can_retry_after_cooldown(self):
        with lib.connection() as db:
            with db:db.execute('UPDATE artists SET sync_error=?,last_attempt_at=?',('temporary error',time.time()-601))
        submitted=[]
        with patch.object(ratings,'POOL',SimpleNamespace(submit=submitted.append)):ratings.enqueue('13/test_artist')
        self.assertEqual(len(submitted),1)
    def test_failed_artist_is_not_retried_in_a_tight_loop(self):
        with lib.connection() as db:
            with db:db.execute('UPDATE artists SET sync_error=?,last_attempt_at=?',('temporary error',time.time()))
        submitted=[]
        with patch.object(ratings,'POOL',SimpleNamespace(submit=submitted.append)):ratings.enqueue('13/test_artist')
        self.assertEqual(submitted,[])
    def test_unexpected_bulk_failure_preserves_resume_marker(self):
        class InlineThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        with patch.object(ratings,'threading',SimpleNamespace(Thread=InlineThread)),patch.object(lib,'connection',side_effect=sqlite3.OperationalError('offline')):ratings.start_bulk()
        self.assertTrue(json.loads(ratings.RESUME.read_text())['resume_on_start'])
        self.assertFalse(ratings.status()['running']);self.assertEqual(ratings.status()['last_error'],'offline')
    def test_resume_write_failure_does_not_leave_bulk_running(self):
        with patch.object(ratings,'persist_sync',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):ratings.start_bulk()
        self.assertFalse(ratings.status()['running'])
        self.assertEqual(ratings.status()['last_error'],'disk full')

class WindowsOpenTests(ArchiveCase):
    def test_correct_association_uses_registered_open_verb(self):
        with patch.object(windows_open,'registered_command',return_value=f'"{self.exe}" --open "%1"'),patch.object(windows_open.os,'startfile',create=True) as opened:
            windows_open.open_tab(self.folder/'song.gp4',self.exe)
            opened.assert_called_once_with(str(self.folder/'song.gp4'),'open')
    def test_wrong_association_does_not_open_another_program(self):
        with patch.object(windows_open,'registered_command',return_value='"C:/Other.exe" "%1"'),patch.object(windows_open.os,'startfile',create=True) as opened:
            with self.assertRaises(ValueError):windows_open.open_tab(self.folder/'song.gp4',self.exe)
            opened.assert_not_called()

class HTTPTests(ArchiveCase):
    def setUp(self):
        super().setUp()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.host='127.0.0.1:'+str(self.http.server_address[1]);server.HOSTS.add(self.host)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
    def close_server(self):
        self.http.shutdown();self.http.server_close();server.HOSTS.discard(self.host)
    def request(self,path,body=None,token=None,origin='http://127.0.0.1:8765',method=None,host=None):
        headers={'Origin':origin}
        if token:headers['X-Taboteka-Token']=token
        if host:headers['Host']=host
        if body is not None:headers['Content-Type']='application/json'
        request=Request('http://'+self.host+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
        return urlopen(request,timeout=5)
    def test_bootstrap_and_catalog_are_real(self):
        with self.request('/api/bootstrap') as r:self.assertEqual(json.load(r)['stats']['files'],2);self.assertEqual(r.headers['Access-Control-Allow-Origin'],'http://127.0.0.1:8765')
        with self.request('/api/tabs') as r:self.assertEqual(json.load(r)['total'],2)
    def test_foreign_origin_is_denied(self):
        with self.assertRaises(HTTPError) as err:self.request('/api/bootstrap',origin='https://example.org')
        self.assertEqual(err.exception.code,403)
    def test_rebinding_host_is_denied(self):
        with self.assertRaises(HTTPError) as err:self.request('/api/bootstrap',host='example.org')
        self.assertEqual(err.exception.code,403)
    def test_mutation_without_token_is_denied(self):
        with self.assertRaises(HTTPError) as err:self.request('/api/scan',{})
        self.assertEqual(err.exception.code,403)
    def test_nonobject_json_returns_400(self):
        with self.assertRaises(HTTPError) as err:self.request('/api/tab',[],token=server.TOKEN)
        self.assertEqual(err.exception.code,400)
    def test_favorite_and_backup_flow(self):
        with self.request('/api/tab',{'id':self.tab['id'],'favorite':True,'notes':'Practice'},token=server.TOKEN) as r:self.assertEqual(json.load(r)['favorite'],1)
        with self.request('/api/export') as r:backup=json.load(r)
        self.assertEqual(backup['items'][0]['notes'],'Practice')
        with self.request('/api/import',backup,token=server.TOKEN) as r:self.assertEqual(json.load(r)['restored'],1)
    def test_open_route_passes_safe_file_to_launcher(self):
        with patch.object(server,'open_tab') as opened:
            with self.request('/api/open',{'id':self.tab['id']},token=server.TOKEN) as r:self.assertTrue(json.load(r)['ok'])
            opened.assert_called_once_with(lib.safe_file(self.tab['id']),str(self.exe))
    def test_concurrent_scan_reports_running(self):
        with lib.SCAN_LOCK:
            with self.request('/api/scan',{},token=server.TOKEN) as r:self.assertEqual(json.load(r),{'running':True})
    def test_static_route_does_not_expose_config(self):
        with self.assertRaises(HTTPError) as err:self.request('/../config.json')
        self.assertEqual(err.exception.code,404)
    def test_options_supports_only_allowed_origin(self):
        with self.request('/api/tab',method='OPTIONS') as r:self.assertEqual(r.status,204)
    def test_disconnect_while_writing_headers_is_harmless(self):
        handler=object.__new__(server.Handler)
        handler.send_response=Mock(side_effect=ConnectionAbortedError())
        handler.json({'ok':True})

if __name__=='__main__':unittest.main(verbosity=2)
