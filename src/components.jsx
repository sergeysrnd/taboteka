import React, {useRef, useState} from 'react';
import {BookOpen, Star, Clock3, GraduationCap, Music2, Files, Users, Film, Dumbbell, Search, ExternalLink, ChevronLeft, ChevronRight, X, Settings, RefreshCw, Download, Upload, Check, FolderOpen, Guitar, Info} from 'lucide-react';
import {api, count, endpoint} from './api';
import {useDialog} from './useDialog';

const NAV = [['all','Библиотека',BookOpen],['favorites','Избранное',Star],['recent','Недавние',Clock3],['learn','Разучить',GraduationCap],['play','Играю',Music2]];
const COLLECTION = [['all','Все табулатуры',Files],['artists','Исполнители',Users],['soundtracks','Саундтреки',Film],['lessons','Упражнения',Dumbbell],['local','Местные исполнители',Music2]];
export function Sidebar({view, setView, stats, settings}) {
  return <aside className="sidebar">
    <a className="brand" href="#" onClick={e=>{e.preventDefault();setView('all')}}><Guitar size={38}/><span>Таботека</span></a>
    <nav aria-label="Основная навигация">{NAV.map(([id,label,Icon])=>{const total=stats[{favorites:'favorites',learn:'learning',play:'playing'}[id]];return <button key={id} className={'nav-item '+(view===id?'active':'')} onClick={()=>setView(id)}><Icon/><span>{label}</span>{total>0&&<small>{count(total)}</small>}</button>})}</nav>
    <div className="nav-separator"/><div className="rail-label">Коллекция</div>
    <nav aria-label="Разделы коллекции">{COLLECTION.map(([id,label,Icon])=><button key={id} className={'nav-item '+(id!=='all'&&id!=='artists'&&view===id?'active':'')} onClick={()=>setView(id)}><Icon/><span>{label}</span></button>)}</nav>
    <button className="local-state" onClick={settings}><i/> <span>Локальная библиотека</span><ChevronRight size={16}/></button>
  </aside>;
}

export function ArtistBrowser({artists, artist, setArtist, search, setSearch, open, setOpen, loading}) {
  return <aside className={'artist-browser '+(open?'mobile-open':'')}>
    <div className="artist-heading"><h2>Исполнители</h2><button className="icon-button mobile-only" title="Скрыть исполнителей" onClick={()=>setOpen(false)}><X/></button></div>
    <label className="artist-search"><Search size={18}/><input aria-label="Поиск исполнителя" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Поиск исполнителя…"/></label>
    <div className="artist-scroll" aria-busy={loading}>
      <button className={'artist-row all-artists '+(!artist?'selected':'')} onClick={()=>{setArtist('');setOpen(false)}}><span>Все исполнители</span></button>
      {artists.map(a=><button key={a.id} className={'artist-row '+(artist===a.id?'selected':'')} onClick={()=>{setArtist(a.id);setOpen(false)}} title={a.name}><span>{a.name}</span><small>{count(a.filtered_count)}</small></button>)}
      {!artists.length&&!loading&&<p className="muted list-empty">Исполнители не найдены</p>}
      {artists.length===250&&<p className="list-hint">Показаны 250 исполнителей. Уточните имя в поиске.</p>}
    </div>
  </aside>;
}

export function TabTable({data, loading, title, subtitle, format, setFormat, sort, setSort, onOpen, onFavorite, onPractice, onDetails, onPage}) {
  return <section className="songs" aria-label="Табулатуры">
    <div className="songs-toolbar"><div className="songs-title"><h2>{title}</h2><p>{subtitle}</p></div><div className="table-controls">
      <select aria-label="Формат файлов" value={format} onChange={e=>setFormat(e.target.value)}><option value="">Все форматы</option>{['GP3','GP4','GP5','GPX','GTP','GP'].map(f=><option key={f}>{f}</option>)}</select>
      <select aria-label="Сортировка" value={sort} onChange={e=>setSort(e.target.value)}><option value="name">По названию</option><option value="rating">По оценке сайта</option><option value="recent">Недавно открытые</option><option value="size">По размеру</option></select>
    </div></div>
    <div className={'table-scroll '+(loading?'loading':'')} aria-busy={loading}><table className="song-table"><thead><tr><th>Песня</th><th className="format-column">Формат</th><th className="rating-column">Оценка</th><th className="actions-column"><span className="sr-only">Действия</span></th></tr></thead><tbody>
      {data.items.map(tab=><TabRow key={tab.id} tab={tab} onOpen={onOpen} onFavorite={onFavorite} onPractice={onPractice} onDetails={onDetails}/>)}
    </tbody></table></div>
    {!data.items.length&&!loading&&<div className="empty-state"><BookOpen size={32}/><h3>Здесь пока нет табулатур</h3><p>{title==='Избранное'?'Нажмите на звезду рядом с любимым файлом.':['Играю','Разучить'].includes(title)?`Откройте библиотеку и нажмите «${title}» рядом с песней.`:'Попробуйте другое название или измените фильтры.'}</p></div>}
    <footer className="pagination"><span>{data.total?`${count((data.page-1)*40+1)}–${count(Math.min(data.page*40,data.total))} из ${count(data.total)}`:'Нет файлов'}</span><div><button className="button" disabled={data.page<=1||loading} onClick={()=>onPage(data.page-1)}><ChevronLeft size={16}/>Назад</button><span>{data.page} / {count(data.pages)}</span><button className="button" disabled={data.page>=data.pages||loading} onClick={()=>onPage(data.page+1)}>Далее<ChevronRight size={16}/></button></div></footer>
  </section>;
}

const TabRow=React.memo(function TabRow({tab,onOpen,onFavorite,onPractice,onDetails}) {
  const [busy,setBusy]=useState(false),pending=useRef(false);
  async function changePractice(value){if(pending.current)return;pending.current=true;setBusy(true);try{await onPractice(tab,tab.practice===value?'':value)}finally{pending.current=false;setBusy(false)}}
  return <tr>
    <td className="song-identity"><span className="song-name">{tab.title}</span><div className="filename" title={tab.artist+' · '+tab.path}>{tab.artist} · {tab.filename}</div></td>
    <td className="song-format"><span className="format-tag">{tab.format}</span></td>
    <td className="song-rating">{tab.score?<a className="rating-value" title={'Оценка gtp-tabs.ru: '+tab.score+' из 5. Совпали версия и размер файла.'} href={tab.source_url} target="_blank" rel="noreferrer"><Star size={13} fill="currentColor"/>{tab.score}<span>/5</span></a>:<span className="no-rating" title={tab.source_url?'На сайте нет оценки':'Оценка ещё не получена или совпадение не подтверждено'}>—</span>}</td>
    <td className="song-actions"><div className="row-actions">
      <div className="practice-actions" role="group" aria-label={'Список занятий: '+tab.filename} aria-busy={busy}>
        {[['play','Играю',Music2],['learn','Разучить',GraduationCap]].map(([value,label,Icon])=><button key={value} className={'practice-button '+(tab.practice===value?'selected':'')} aria-label={label+': '+tab.filename} aria-pressed={tab.practice===value} title={(tab.practice===value?'Убрать из списка «':'В список «')+label+'»'} disabled={busy} onClick={()=>changePractice(value)}><Icon size={14}/>{label}</button>)}
      </div>
      <div className="file-actions">
        <button className={'icon-button favorite '+(tab.favorite?'is-favorite':'')} aria-label={(tab.favorite?'Убрать из избранного: ':'В избранное: ')+tab.filename} title={tab.favorite?'Убрать из избранного':'В избранное'} aria-pressed={Boolean(tab.favorite)} onClick={()=>onFavorite(tab)}><Star size={19} fill={tab.favorite?'currentColor':'none'}/></button>
        <button className="icon-button song-details" onClick={()=>onDetails(tab)} aria-label={'Заметки и сведения: '+tab.filename} title="Заметки и сведения о файле"><Info size={17}/></button>
        <button className="open-button" aria-label={'Открыть в Guitar Pro: '+tab.filename} title="Открыть в Guitar Pro" onClick={()=>onOpen(tab)}><ExternalLink size={16}/><span>Открыть</span></button>
      </div>
    </div></td>
  </tr>;
});

export function DetailsDrawer({tab,onClose,onSave,onOpen}) {
  const dialogRef=useDialog(onClose);
  const [notes,setNotes]=useState(tab.notes),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function save(){setBusy(true);setError('');try{await onSave(tab,{notes});onClose()}catch(e){setError(e.message)}finally{setBusy(false)}}
  return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section ref={dialogRef} className="drawer" role="dialog" aria-modal="true" aria-labelledby="details-title"><div className="dialog-top"><span>Табулатура</span><button className="icon-button" aria-label="Закрыть сведения" onClick={onClose}><X/></button></div><h2 id="details-title">{tab.title}</h2><p className="muted">{tab.artist}</p><button className="open-button filled full" onClick={()=>onOpen(tab)}><ExternalLink size={18}/>Открыть в Guitar Pro</button>
    <dl className="file-info"><dt>Формат</dt><dd>{tab.format} · {(tab.bytes/1024).toFixed(2)} КБ</dd><dt>Файл в архиве</dt><dd className="file-path">{tab.path}</dd><dt>Оценка gtp-tabs.ru</dt><dd>{tab.score?`${tab.score} из 5`:tab.source_url?'На сайте нет оценки':'Нет подтверждённой оценки'}</dd>{tab.source_url&&<><dt>Источник</dt><dd><a href={tab.source_url} target="_blank" rel="noreferrer">Страница этой версии <ExternalLink size={13}/></a></dd></>}</dl>
    {tab.title_method&&<dl className="file-info"><dt>Название композиции</dt><dd>{tab.title_source_url?<a href={tab.title_source_url} target="_blank" rel="noreferrer">Подтверждено на gtp-tabs.ru <ExternalLink size={13}/></a>:'Из метаданных файла Guitar Pro'}</dd>{tab.artist_name_source&&<><dt>Имя исполнителя</dt><dd><a href={tab.artist_name_source} target="_blank" rel="noreferrer">Страница исполнителя <ExternalLink size={13}/></a></dd></>}<dt>Поиск</dt><dd>По названию и исходному имени файла</dd></dl>}
    <label className="field-label">Ваши заметки<textarea autoFocus value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Аппликатура, сложные такты, план занятий…" rows={6} maxLength={20000}/></label>{error&&<p className="error-message" role="alert">{error}</p>}<button className="button filled full" onClick={save} disabled={busy}><Check size={18}/>{busy?'Сохраняем…':'Сохранить'}</button>
  </section></div>;
}

export function SettingsDialog({config,stats,ratings,onClose,onNotify,onRefresh,onConfig}) {
  const dialogRef=useDialog(onClose);
  const [exe,setExe]=useState(config.guitar_pro),[busy,setBusy]=useState(false);
  async function run(action){setBusy(true);try{await action()}catch(e){onNotify(e.message,true)}finally{setBusy(false)}}
  return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section ref={dialogRef} className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="dialog-top"><h2 id="settings-title">Настройки библиотеки</h2><button className="icon-button" aria-label="Закрыть настройки" onClick={onClose}><X/></button></div>
    <label className="field-label">Папка с табулатурами<div className="readonly-path"><FolderOpen size={18}/>{config.tabs_root}</div><small>Исходные файлы остаются на месте.</small></label>
    <label className="field-label">Программа Guitar Pro<input value={exe} onChange={e=>setExe(e.target.value)}/><small>Расширения табов также должны быть назначены этой программе в Windows.</small></label><button className="button" disabled={busy} onClick={()=>run(async()=>{await api('/api/settings',{guitar_pro:exe});onConfig({guitar_pro:exe});onNotify('Путь к Guitar Pro сохранён')})}><Check size={16}/>Сохранить путь</button>
    <div className="settings-section"><h3>Оценки с gtp-tabs.ru</h3><p>Загружаются при выборе исполнителя и сохраняются на этом ПК. Сопоставляем точное имя версии и размер файла. Прочерк означает отсутствие подтверждённой оценки.</p><p className="sync-numbers">Сопоставлено {count(stats.matched)} файлов · с оценкой {count(stats.rated)}</p><p className="muted">{ratings.running?`${count(ratings.done)} / ${count(ratings.total)} исполнителей · ${ratings.current}`:'Можно загрузить оценки всей коллекции в фоне. Это займёт несколько часов; прерванную загрузку можно продолжить.'}</p><button className="button" disabled={busy} onClick={()=>run(async()=>{await api('/api/ratings',ratings.running?{action:'stop'}:{});onRefresh();onNotify(ratings.running?'Остановка после текущего запроса':'Фоновая загрузка оценок начата')})}><RefreshCw size={16}/>{ratings.running?'Остановить загрузку':'Загрузить оценки всей коллекции'}</button></div>
    <div className="settings-section"><h3>Названия в библиотеке</h3><p>Уточнены названия {count(stats.named)} файлов и имена {count(stats.named_artists)} исполнителей.</p><p className="muted">По страницам источника: {count(stats.source_named)} · по метаданным Guitar Pro: {count(stats.header_named)}. Исходные имена и поиск по транслитерации сохранены.</p></div>
    <div className="settings-section"><h3>Резервная копия</h3><p>Избранное, заметки, списки занятий и история открытий. Сами табулатуры в копию не входят.</p><div className="backup-actions"><a className="button" href={endpoint('/api/export')} download><Download size={16}/>Скачать копию</a><label className="button upload-button"><Upload size={16}/>Восстановить<input type="file" accept=".json" onChange={e=>{const file=e.target.files[0];if(file)run(async()=>{const result=await api('/api/import',JSON.parse(await file.text()));onRefresh();onNotify(`Восстановлено записей: ${count(result.restored)}`)})}}/></label></div></div>
  </section></div>;
}
