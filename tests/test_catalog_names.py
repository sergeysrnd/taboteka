"""Display names are separate metadata; paths, versions and legacy search are stable."""
import json,struct
from unittest.mock import patch
from test_library import ArchiveCase,source_row
import library as lib
import catalog_names as names
import ratings

class NameTests(ArchiveCase):
    def proposal(self,name='Песня',**changes):
        return {'entity':'tab','id':self.tab['id'],'artist_id':'13/test_artist','name':name,'source_url':'https://www.gtp-tabs.ru/n/tabs/13/test-artist/song.html','method':'source_exact','bytes':1024,'mtime':self.tab['mtime'],**changes}
    def test_display_title_preserves_filename_and_both_searches(self):
        names.apply_plan({'proposals':[self.proposal()]})
        tab=lib.get_tab(self.tab['id'])
        self.assertEqual(tab['title'],'Песня');self.assertEqual(tab['filename'],'song.gp4')
        self.assertEqual(tab['original_title'],'Song')
        for q in ('song','песня','pesnya'):self.assertEqual(lib.list_tabs({'q':q})['total'],1 if q!='song' else 2)
    def test_artist_display_and_old_slug_are_both_searchable(self):
        names.apply_plan({'proposals':[{'entity':'artist','id':'13/test_artist','name':'Тестовый исполнитель','source_url':'https://www.gtp-tabs.ru/n/tabs/13/test-artist.html','method':'artist_index'}]})
        for q in ('test artist','тестовый'):self.assertEqual(len(lib.list_artists({'aq':q})),1)
        self.assertEqual(lib.get_tab(self.tab['id'])['artist'],'Тестовый исполнитель')
    def test_unchanged_rescan_preserves_display_name(self):
        names.apply_plan({'proposals':[self.proposal()]});lib.scan()
        self.assertEqual(lib.get_tab(self.tab['id'])['title'],'Песня')
    def test_replacement_clears_display_name_and_search(self):
        names.apply_plan({'proposals':[self.proposal()]});(self.folder/'song.gp4').write_bytes(b'replacement');lib.scan()
        tab=lib.get_tab(self.tab['id']);self.assertIsNone(tab['display_title']);self.assertEqual(tab['name_search'],'')
    def test_stale_plan_cannot_name_a_replacement(self):
        proposal=self.proposal();(self.folder/'song.gp4').write_bytes(b'replacement');lib.scan()
        self.assertEqual(names.apply_plan({'proposals':[proposal]})['tab'],0)
    def test_wrong_artist_and_wrong_size_are_not_matches(self):
        row={'url':'https://www.gtp-tabs.ru/n/tabs/13/other/song.html','slug':'song','title':'Песня','kb':1}
        self.assertEqual(names.source_matches([self.tab],[row],'13/test_artist'),[])
        row['url']=self.proposal()['source_url'];row['kb']=2
        self.assertEqual(names.source_matches([self.tab],[row],'13/test_artist'),[])
    def test_conflicting_source_names_are_not_accepted(self):
        a={'url':self.proposal()['source_url'],'slug':'song','title':'Один','kb':1}
        self.assertEqual(names.source_matches([self.tab],[a,{**a,'title':'Другой'}],'13/test_artist'),[])
    def test_html_index_only_accepts_artist_links(self):
        html='<a href="/n/tabs/13/test-artist.html"><b>Тест</b> &amp; группа</a><a href="https://evil.test/n/tabs/13/other.html">Bad</a><a href="/n/tabs/13/test-artist/song.html">Song</a>'
        rows=names.parse_artist_index(html);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['name'],'Тест & группа')
    def test_bad_or_missing_text_is_not_accepted(self):
        for value in (None,[],{},'','  ','bad\ufffdname','x'*401):self.assertIsNone(names.clean_name(value))
    def test_rating_refresh_also_saves_verified_title(self):
        ratings.cache_source('13/test_artist',source_row())
        self.assertEqual(lib.get_tab(self.tab['id'])['title_method'],'source_exact')
    def test_lower_rank_cannot_overwrite_source_title(self):
        names.apply_plan({'proposals':[self.proposal()]})
        names.apply_plan({'proposals':[self.proposal(name='Other',method='file_header',source_url=None)]})
        self.assertEqual(lib.get_tab(self.tab['id'])['title'],'Песня')
    def test_safe_header_reader_is_read_only_and_bounded(self):
        path=self.folder/'metadata.gp4';version=b'FICHIER GUITAR PRO v4.06'
        data=bytes([len(version)])+version+bytes(30-len(version))
        for text in ('Название','','Исполнитель'):
            raw=text.encode('cp1251');data+=struct.pack('<i',len(raw)+1)+bytes([len(raw)])+raw
        path.write_bytes(data);before=path.read_bytes()
        self.assertEqual(names.read_gp_header(path,True)['title'],'Название');self.assertEqual(path.read_bytes(),before)
        path.write_bytes(data[:31]+struct.pack('<i',10000000))
        self.assertIsNone(names.read_gp_header(path,True))
    def test_numeric_versions_keep_their_identity(self):
        self.assertEqual(names.stem_and_version('song_2.gp5',{'song','song_2'}),('song','2'))
        self.assertEqual(names.stem_and_version('song_2.gp5',{'song_2'}),('song_2',None))
        self.assertEqual(names.stem_and_version('song_intro.gp5',{'song'}),('song_intro',None))
    def test_metadata_does_not_replace_notes_or_favorites(self):
        lib.update_tab(self.tab['id'],{'favorite':True,'notes':'Keep'})
        names.apply_plan({'proposals':[self.proposal()]})
        tab=lib.get_tab(self.tab['id']);self.assertEqual(tab['notes'],'Keep');self.assertEqual(tab['favorite'],1)
    def test_schema_upgrade_is_idempotent(self):
        lib.initialize();lib.initialize();self.assertIn('display_title',lib.get_tab(self.tab['id']))
    def test_numeric_suffix_without_own_evidence_is_rejected(self):
        with self.assertRaises(ValueError):names.apply_plan({'proposals':[self.proposal(method='version_family')]})
    def test_primary_detail_heading_and_size_confirm_the_exact_version(self):
        html='<h1>Исполнитель - Песня (2)</h1><dl><dt>Размер:</dt><dd>1.00 Kb</dd></dl>'
        result=names.detail_name(html,self.proposal()['source_url'],self.tab,'13/test_artist')
        self.assertEqual(result['name'],'Песня (2)');self.assertEqual(result['method'],'source_exact')
    def test_related_song_size_cannot_confirm_a_primary_score(self):
        html='<h1>Исполнитель - Песня</h1><h2>Другие табулатуры</h2><p>Размер: 1.00 Kb</p>'
        self.assertIsNone(names.detail_name(html,self.proposal()['source_url'],self.tab,'13/test_artist'))
    def test_detail_page_with_wrong_size_or_artist_is_rejected(self):
        html='<h1>Исполнитель - Песня</h1><p>Размер: 99.00 Kb</p>'
        self.assertIsNone(names.detail_name(html,self.proposal()['source_url'],self.tab,'13/test_artist'))
        html=html.replace('99.00','1.00')
        self.assertIsNone(names.detail_name(html,self.proposal()['source_url'],self.tab,'13/other'))
    def test_empty_transliterations_cannot_match_each_other(self):
        self.assertEqual(names.translit_keys('🎸'),set())
    def test_changed_artist_url_is_an_explicit_reviewed_alias(self):
        self.assertEqual(names.artist_key('44/stryikalo_valentin'),'30/stryikalo-valentin')
    def test_filing_article_is_displayed_as_part_of_the_band_name(self):
        self.assertEqual(names.display_artist_name('strokes_the','Strokes (The)'),'The Strokes')
        self.assertEqual(names.display_artist_name('beatles_the','Beatles (The)'),'The Beatles')
        self.assertEqual(names.display_artist_name('misc_computer_games','Misc Computer Games'),'Разное: компьютерные игры')
    def test_cyrillic_sort_ignores_case_and_orders_versions_naturally(self):
        (self.folder/'song_10.gp5').write_bytes(b'test');lib.scan()
        titles={'song.gp4':'песня','song_2.gp5':'Песня (2)','song_10.gp5':'песня (10)'}
        with lib.connection() as db:
            with db:db.executemany('UPDATE tabs SET display_title=? WHERE filename=?',[(title,filename) for filename,title in titles.items()])
        self.assertEqual([r['title'] for r in lib.list_tabs({'sort':'name'})['items']],['песня','Песня (2)','песня (10)'])
