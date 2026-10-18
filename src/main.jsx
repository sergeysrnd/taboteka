import React,{useState,useEffect,useRef,useCallback} from 'react';
import {createRoot} from 'react-dom/client';
import {Home,ChevronRight,RefreshCw,Settings,Search,Users,X,CloudDownload,CheckCircle2,AlertCircle} from 'lucide-react';
import {api,setToken,count,plural} from './api';
import {Sidebar,ArtistBrowser,TabTable,DetailsDrawer,SettingsDialog} from './components';
import './styles.css';

const LABELS={all:'Библиотека',artists:'Исполнители',favorites:'Избранное',recent:'Недавние',learn:'Разучить',play:'Играю',soundtracks:'Саундтреки',lessons:'Упражнения',local:'Местные исполнители'};
const EMPTY={items:[],total:0,page:1,pages:1};
function useDebounced(value,delay=200){const [v,set]=useState(value);useEffect(()=>{const id=setTimeout(()=>set(value),delay);return()=>clearTimeout(id)},[value,delay]);return v}

function App(){
  const [boot,setBoot]=useState(null),[bootError,setBootError]=useState(''),[view,setViewRaw]=useState('all'),[artist,setArtistRaw]=useState(''),[q,setQ]=useState(''),[aq,setAq]=useState(''),[format,setFormatRaw]=useState(''),[sort,setSortRaw]=useState('name'),[page,setPage]=useState(1),[artists,setArtists]=useState([]),[data,setData]=useState(EMPTY),[loading,setLoading]=useState(true),[artistLoading,setArtistLoading]=useState(false),[revision,setRevision]=useState(0),[detail,setDetail]=useState(null),[settings,setSettings]=useState(false),[artistOpen,setArtistOpen]=useState(false),[toast,setToast]=useState(null),[scanning,setScanning]=useState(false);
  const query=useDebounced(q),artistQuery=useDebounced(aq),searchRef=useRef(null),toastTimer=useRef(null);
  const notify=useCallback((message,error=false)=>{setToast({message,error});clearTimeout(toastTimer.current);toastTimer.current=setTimeout(()=>setToast(null),error?9000:4000)},[]);
  const refresh=useCallback(()=>setRevision(v=>v+1),[]);
  const refreshStats=useCallback(async()=>{const r=await api('/api/stats');setBoot(b=>b?{...b,stats:r.stats,ratings:r.ratings}:b);return r},[]);
  useEffect(()=>{api('/api/bootstrap').then(b=>{setToken(b.token);setBoot(b)}).catch(e=>setBootError(e.message))},[]);
  useEffect(()=>{const handler=e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();searchRef.current?.focus()}};document.addEventListener('keydown',handler);return()=>document.removeEventListener('keydown',handler)},[]);
  useEffect(()=>{if(!boot)return;const timer=setInterval(()=>refreshStats().catch(()=>{}),4000);return()=>clearInterval(timer)},[Boolean(boot),refreshStats]);
  const stats=boot?.stats||{},syncState=boot?.ratings||{};
  useEffect(()=>{if(!boot)return;refresh()},[stats.matched,stats.rated,syncState.pending?.join(','),refresh]);
  useEffect(()=>{if(!boot)return;const controller=new AbortController();setLoading(true);const p=new URLSearchParams({view:view==='artists'?'all':view,artist,q:query,format,sort,page});api('/api/tabs?'+p,undefined,controller.signal).then(r=>{setData(r);setLoading(false)}).catch(e=>{if(e.name!=='AbortError'){notify(e.message,true);setLoading(false)}});return()=>controller.abort()},[Boolean(boot),view,artist,query,format,sort,page,revision,notify]);
  useEffect(()=>{if(!boot)return;const c=new AbortController();setArtistLoading(true);api('/api/artists?'+new URLSearchParams({view:view==='artists'?'all':view,q:query,aq:artistQuery}),undefined,c.signal).then(r=>{setArtists(r);setArtistLoading(false)}).catch(e=>{if(e.name!=='AbortError'){notify(e.message,true);setArtistLoading(false)}});return()=>c.abort()},[Boolean(boot),view,query,artistQuery,revision,notify]);
  useEffect(()=>{if(boot&&artist)api('/api/ratings',{artist}).then(()=>refreshStats()).catch(()=>{})},[Boolean(boot),artist,refreshStats]);
  useEffect(()=>{setPage(1)},[query]);
  const setView=useCallback(v=>{setViewRaw(v);setArtistRaw('');setQ('');setAq('');setPage(1);setFormatRaw('');setSortRaw(v==='recent'?'recent':'name');if(v==='artists')setArtistOpen(true)},[]);
  const setArtist=useCallback(a=>{setArtistRaw(a);setPage(1)},[]);
  const setFormat=f=>{setFormatRaw(f);setPage(1)},setSort=s=>{setSortRaw(s);setPage(1)};
  const save=useCallback(async(tab,changes)=>{const updated=await api('/api/tab',{id:tab.id,...changes});setData(d=>({...d,items:d.items.map(t=>t.id===tab.id?{...t,...updated}:t)}));refresh();await refreshStats().catch(()=>{});return updated},[refresh,refreshStats]);
  const favorite=useCallback(async tab=>{try{await save(tab,{favorite:!tab.favorite})}catch(e){notify(e.message,true)}},[save,notify]);
  const open=useCallback(async tab=>{try{await api('/api/open',{id:tab.id});notify('Открываем '+tab.filename+' в Guitar Pro');refreshStats().catch(()=>{})}catch(e){notify(e.message,true)}},[notify,refreshStats]);
  async function scan(){setScanning(true);try{const result=await api('/api/scan',{});notify(result.running?'Сканирование уже выполняется':`Каталог обновлён: ${count(result.files)} файлов`);refresh();await refreshStats().catch(()=>{})}catch(e){notify(e.message,true)}finally{setScanning(false)}}
  const selected=artists.find(a=>a.id===artist);
  const artistName=selected?.name||(artist?data.items[0]?.artist:'');
  const title=artist?(artistName||'Табулатуры'):query?'Результаты поиска':view==='all'?'Все табулатуры':LABELS[view];
  const isSyncing=artist&&syncState.pending?.includes(artist);
  if(bootError)return <main className="boot-screen"><AlertCircle size={36}/><h1>Локальная библиотека недоступна</h1><p>Запустите «Открыть Таботеку» из папки приложения.</p><p className="muted">{bootError}</p><button className="button" onClick={()=>location.reload()}>Повторить</button></main>;
  if(!boot)return <main className="boot-screen"><BookLoader/><p>Открываем библиотеку…</p></main>;
  return <div className="app-shell"><Sidebar view={view} setView={setView} stats={stats} settings={()=>setSettings(true)}/><div className="workspace">
    <header className="topbar"><div className="breadcrumb"><Home size={19}/><ChevronRight size={16}/><span>{LABELS[view]}</span></div><div className="topbar-actions"><button className="button refresh-button" disabled={scanning} onClick={scan}><RefreshCw size={18} className={scanning?'spin':''}/><span>{scanning?'Сканируем…':'Обновить каталог'}</span></button><button className="icon-button" aria-label="Настройки" onClick={()=>setSettings(true)}><Settings size={21}/></button></div></header>
    <main className="main-content"><div className="intro"><h1>Ваша музыка. Под рукой.</h1><p>{count(stats.files)} {plural(stats.files,['табулатура','табулатуры','табулатур'])} <span>·</span> {count(stats.artists)} {plural(stats.artists,['исполнитель','исполнителя','исполнителей'])}</p></div>
    <label className="global-search"><Search size={23}/><input ref={searchRef} value={q} onChange={e=>{setQ(e.target.value);setArtistRaw('');setPage(1)}} placeholder="Исполнитель, песня или название файла" aria-label="Поиск по библиотеке"/>{q?<button className="icon-button" aria-label="Очистить поиск" onClick={()=>setQ('')}><X size={17}/></button>:<kbd>Ctrl&nbsp; K</kbd>}</label>
    <button className="button mobile-artist-toggle" onClick={()=>setArtistOpen(v=>!v)}><Users size={17}/>{artistName||'Выбрать исполнителя'}</button>
    <div className="library-layout"><ArtistBrowser artists={artists} artist={artist} setArtist={setArtist} search={aq} setSearch={setAq} open={artistOpen} setOpen={setArtistOpen} loading={artistLoading}/><div className="songs-region"><TabTable data={data} loading={loading} title={title} subtitle={`${count(data.total)} ${plural(data.total,['файл','файла','файлов'])}${query?' по запросу «'+query+'»':''}`} format={format} setFormat={setFormat} sort={sort} setSort={setSort} onOpen={open} onFavorite={favorite} onDetails={setDetail} onPage={setPage}/>
    <div className="source-status"><div><CloudDownload size={15}/><span>{isSyncing?'Загружаем оценки с gtp-tabs.ru…':selected?.sync_error?'Источник временно недоступен. Сохранённые оценки доступны.':selected?.synced_at?'Оценки gtp-tabs.ru сохранены на этом ПК':'Оценки загружаются при выборе исполнителя'}</span></div>{artist&&<button onClick={()=>api('/api/ratings',{artist,force:true}).then(()=>{notify('Обновляем оценки исполнителя');return refreshStats()}).catch(e=>notify(e.message,true))} disabled={isSyncing}>Обновить оценки</button>}</div>
    </div></div></main>
  </div>{detail&&<DetailsDrawer key={detail.id} tab={detail} onClose={()=>setDetail(null)} onSave={save} onOpen={open}/>} {settings&&<SettingsDialog config={boot.config} stats={stats} ratings={syncState} onClose={()=>setSettings(false)} onNotify={notify} onConfig={changes=>setBoot(b=>({...b,config:{...b.config,...changes}}))} onRefresh={()=>{refresh();refreshStats().catch(()=>{})}}/>}{toast&&<div className={'toast '+(toast.error?'error':'')} role={toast.error?'alert':'status'}>{toast.error?<AlertCircle size={18}/>:<CheckCircle2 size={18}/>}<span>{toast.message}</span><button className="icon-button" aria-label="Закрыть уведомление" onClick={()=>setToast(null)}><X size={16}/></button></div>}</div>;
}
function BookLoader(){return <RefreshCw size={32} className="spin"/>}
createRoot(document.getElementById('root')).render(<App/>);
