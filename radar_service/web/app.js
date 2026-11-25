'use strict';
const $ = s => document.querySelector(s);
const clone = v => JSON.parse(JSON.stringify(v));
const defaults = clone(window.RADAR_DEFAULTS);
const I18N = window.RADAR_I18N;
const query = new URLSearchParams(location.search);   // ?lang=en  ?theme=dark

// ---------------------------------------------------------------- language
const LANG_KEY = 'radarsense-lang';
let lang = 'zh';
try { const saved = localStorage.getItem(LANG_KEY); if (saved === 'zh' || saved === 'en') lang = saved; } catch (_) {}
if (query.get('lang') === 'zh' || query.get('lang') === 'en') lang = query.get('lang');
function t(key, vars) {
  let s = I18N[lang][key];
  if (s === undefined) s = I18N.zh[key];
  if (s === undefined) return key;
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => vars[k] === undefined ? m : String(vars[k])) : s;
}
const locale = () => lang === 'zh' ? 'zh-CN' : 'en-GB';

// ---------------------------------------------------------------- theme (light / dark)
const THEME_KEY = 'radarsense-theme';
let theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
try { const saved = localStorage.getItem(THEME_KEY); if (saved === 'light' || saved === 'dark') theme = saved; } catch (_) {}
if (query.get('theme') === 'light' || query.get('theme') === 'dark') theme = query.get('theme');
// Map colours per theme (the canvas cannot read CSS variables cheaply every frame).
const PALETTES = {
  light: {stage:'#ffffff',grid:'#e3e8ef',major:'#c5cdd8',edge:'#8f9bab',label:'#5f6b7a',text:'#182433',ok:'#1a7a45',warn:'#b26a00',alert:'#c4302b',accent:'#1f62c9',
          excluded:'rgba(95,107,122,.14)',safe:'rgba(26,122,69,.08)',cloudL:45},
  dark:  {stage:'#1f242b',grid:'#2f363f',major:'#434c57',edge:'#6e7781',label:'#9198a1',text:'#d1d7e0',ok:'#57ab5a',warn:'#c69026',alert:'#e5534b',accent:'#478be6',
          excluded:'rgba(145,152,161,.14)',safe:'rgba(87,171,90,.10)',cloudL:62}
};
let PALETTE = PALETTES[theme];
function applyTheme() {
  document.documentElement.setAttribute('data-theme', theme);
  PALETTE = PALETTES[theme];
}
$('#themeToggle').addEventListener('click', () => {
  theme = theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(THEME_KEY, theme); } catch (_) {}
  applyTheme();
});

// ---------------------------------------------------------------- appearance
let settings = clone(defaults);
try { Object.assign(settings, JSON.parse(localStorage.getItem('radarsense-appearance-v2') || '{}')); } catch (_) {}
const scenes = ['activity','bedroom','shared','lounge','restroom','corridor'];
if (!scenes.includes(settings.scene)) settings.scene = defaults.scene;
const validBounds = b => Array.isArray(b) && b.length===4 && b.every(Number.isFinite) && b[0]<b[1] && b[2]<b[3];
if (!validBounds(settings.bounds)) settings.bounds = defaults.bounds;
// Accent defaults saved by earlier versions count as "not chosen", so the theme's own accent is used.
const OLD_ACCENTS = ['#4cb8a5','#65dcc4','#2f5bd3'];
const customAccent = () => /^#[0-9a-f]{6}$/i.test(settings.accent) && settings.accent.toLowerCase() !== defaults.accent.toLowerCase()
  && !OLD_ACCENTS.includes(settings.accent.toLowerCase());
// The default room name follows the language; a name the user typed is kept as typed.
const isDefaultName = n => !n || n === defaults.roomName || Object.values(I18N).some(d => d['room.default'] === n);
const roomName = () => isDefaultName(settings.roomName) ? t('room.default') : settings.roomName;

let current = {status:'starting',tracks:[],points:[]}, events = [], cloudMode = false;
let eventFault = '', info = {}, infoState = 'loading', zones = null, lastEventsPaint = '', receivedAt = 0, evFilter = 'all';
const canvas = $('#radarCanvas'), ctx = canvas.getContext('2d');
const num = (v,n=2) => typeof v==='number' && Number.isFinite(v) ? v.toFixed(n) : '—';
const isWhole = v => Math.abs(v-Math.round(v))<1e-9;
const ALERT_KINDS = ['fall','prolonged_low'];
const kindOf = e => e.kind || 'fall';
const isAlert = e => ALERT_KINDS.includes(kindOf(e));
const kindLabel = e => I18N.zh['kind.'+kindOf(e)]!==undefined ? t('kind.'+kindOf(e)) : kindOf(e);
const floorMax = () => (info.fall && info.fall.floor_alert_s) || 30;
const fmtTime = ts => new Date(ts*1000).toLocaleString(locale(),{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
function usable() { return current.status==='streaming' && Date.now()-receivedAt<3000; }
function tracks() { return usable() ? (current.tracks || []) : []; }
function node(tag,text,cls) { const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e; }

function applyAppearance() {
  $('#title').textContent = roomName();
  const root = document.documentElement.style;
  root.setProperty('--accent', customAccent() ? settings.accent : '');   // '' removes the override
  root.setProperty('--bg', '');                                           // background now comes from the theme
  $('#roomScene').src = 'assets/scene-'+settings.scene+'.png';
  $('#roomName').value = roomName();
  $('#accent').value = customAccent() ? settings.accent : PALETTE.accent;
  $('#scene').value = settings.scene;
  ['mapXmin','mapXmax','mapYmin','mapYmax'].forEach((id,i)=>$('#'+id).value=settings.bounds[i]);
}
$('#appearanceForm').addEventListener('submit', e => {
  e.preventDefault();
  const b=['mapXmin','mapXmax','mapYmin','mapYmax'].map(id=>Number($('#'+id).value));
  if (!validBounds(b)) { $('#appearanceMessage').textContent=t('app.boundsErr');return; }
  const name=$('#roomName').value.trim(), accent=$('#accent').value;
  const themeAccents=Object.values(PALETTES).map(p=>p.accent);
  settings={roomName:isDefaultName(name)?defaults.roomName:name,accent:themeAccents.includes(accent)?defaults.accent:accent,scene:$('#scene').value,bounds:b};
  try { localStorage.setItem('radarsense-appearance-v2',JSON.stringify(settings));$('#appearanceMessage').textContent=t('app.saved'); }
  catch (_) { $('#appearanceMessage').textContent=t('app.savedNoStore'); }
  applyAppearance();
});
$('#resetAppearance').addEventListener('click',()=>{settings=clone(defaults);try{localStorage.removeItem('radarsense-appearance-v2');}catch(_){}applyAppearance();});

// ---------------------------------------------------------------- tabs and toggles
function setPressed(el,on,attr='aria-pressed'){ el.classList.toggle('active',on); el.setAttribute(attr,String(on)); }
function setMode(value) { cloudMode=value;setPressed($('#sceneTab'),!value,'aria-selected');setPressed($('#cloudTab'),value,'aria-selected');$('#roomScene').hidden=true; }
$('#sceneTab').addEventListener('click',()=>setMode(false));
$('#cloudTab').addEventListener('click',()=>setMode(true));
function setEventFilter(value) {
  evFilter=value;
  for (const [id,v] of [['#evAll','all'],['#evAlerts','alerts'],['#evInfo','info']]) setPressed($(id),v===value);
  paintEvents(true);
}
$('#evAll').addEventListener('click',()=>setEventFilter('all'));
$('#evAlerts').addEventListener('click',()=>setEventFilter('alerts'));
$('#evInfo').addEventListener('click',()=>setEventFilter('info'));
function setLang(value) {
  lang=value;
  try { localStorage.setItem(LANG_KEY,value); } catch (_) {}
  applyLang();
}
$('#langZh').addEventListener('click',()=>setLang('zh'));
$('#langEn').addEventListener('click',()=>setLang('en'));
function applyLang() {
  document.documentElement.lang = lang==='zh' ? 'zh-CN' : 'en';
  document.title = 'RadarSense · ' + t('doc.title');
  for (const el of document.querySelectorAll('[data-i18n]')) el.textContent = t(el.getAttribute('data-i18n'));
  for (const [attr,target] of [['data-i18n-aria','aria-label'],['data-i18n-alt','alt'],['data-i18n-placeholder','placeholder'],['data-i18n-title','title']])
    for (const el of document.querySelectorAll('['+attr+']')) el.setAttribute(target, t(el.getAttribute(attr)));
  setPressed($('#langZh'),lang==='zh'); setPressed($('#langEn'),lang==='en');
  applyAppearance(); paintStatus(); paintTargets(); paintEvents(true); paintRule(); paintMotion(); paintClock();
}

// ---------------------------------------------------------------- data
async function api(path, options={}) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),2500);
  try { const r=await fetch(path,{cache:'no-store',...options,signal:controller.signal});if(!r.ok)throw Error('HTTP '+r.status);return await r.json(); }
  finally { clearTimeout(timer); }
}
function dropText(d) {
  const parts=[];
  if(d.drop_m!==undefined) parts.push(t('drop.drop',{v:num(d.drop_m)}));
  if(d.duration_s!==undefined) parts.push(t('drop.dur',{v:num(d.duration_s,1)}));
  if(d.peak_speed_mps!==undefined) parts.push(t('drop.peak',{v:num(d.peak_speed_mps,1)}));
  if(d.low_h_m!==undefined) parts.push(t('drop.low',{v:num(d.low_h_m)})+(d.reference_m!==undefined?t('drop.ref',{v:num(d.reference_m)}):''));
  return parts.join(t('sep.list'));
}

// ---------------------------------------------------------------- painting
function statusLabel(status) { return I18N.zh['status.'+status]!==undefined?t('status.'+status):status; }
function paintStatus() {
  const status=current.status==='streaming'&&!usable()?'stale':current.status;
  const c=$('#connection');
  c.textContent=statusLabel(status);
  c.className='status-pill '+({streaming:'is-ok',starting:'is-wait',configuring:'is-wait'}[status]||'is-bad');
  $('#sourceNote').textContent=usable()?t('source.live'):t('source.none');
  $('#targetCount').textContent=tracks().length;
  $('#frame').textContent=current.frame_number??'—';
  $('#age').textContent=num(current.frame_age_seconds,2)+' s';
  const n=v=>v??'—';
  $('#diagnostics').textContent=[t('diag.raw',{n:n(current.raw_track_count)}),t('diag.region',{n:n(current.region_filtered_count)}),
    t('diag.static',{n:n(current.motion_filtered_count)}),t('diag.points',{n:n(current.point_count)}),
    current.error?t('diag.error',{msg:current.error}):'',eventFault?t(eventFault):''].filter(Boolean).join(' · ');
  paintBanner();
}
// One sentence about the room, most urgent first: unreviewed alerts, no data, a fall in progress, someone low, all clear.
function paintBanner() {
  const pending=events.filter(e=>isAlert(e)&&!e.acknowledged), ts=tracks();
  const suspected=ts.find(tr=>targetState(tr)==='suspected'), low=ts.find(tr=>targetState(tr)==='low');
  let state, title, sub='';
  if(pending.length) {
    state='alert'; title=t(pending.length===1?'banner.alert1':'banner.alertN',{n:pending.length});
    sub=t('banner.alertSub',{title:kindLabel(pending[0]),time:fmtTime(pending[0].timestamp)});
  } else if(!usable()) {
    const waiting=current.status==='starting'||current.status==='configuring';
    state=waiting?'wait':'off'; title=waiting?statusLabel(current.status):t('banner.offline'); sub=waiting?'':t('banner.offlineSub');
  } else if(suspected) {
    state='alert'; title=t('banner.suspected',{id:suspected.track_id}); sub=t('banner.suspectedSub');
  } else if(low) {
    state='low'; title=t('banner.low',{id:low.track_id,s:num(low.low_seconds,0)}); sub=t('banner.lowSub',{max:floorMax()});
  } else {
    state='ok'; title=t('banner.clear'); sub=ts.length?t('banner.clearSub',{n:ts.length}):t('banner.clearEmpty');
  }
  $('#roomStatus').className='banner is-'+state;
  $('#bannerTitle').textContent=title; $('#bannerSub').textContent=sub;
  $('#bannerAction').hidden=!pending.length;
}
function targetState(tr) {
  return {suspected_fall:'suspected',low:'low',height_unavailable:'unavailable'}[tr.fall_state]||'tracking';
}
function paintTargets() {
  const list=$('#targets');list.replaceChildren();
  const ts=tracks();
  if(!ts.length) { list.append(node('p',usable()?t('targets.none'):t('targets.waiting'),'empty'));return; }
  for(const tr of ts) {
    const state=targetState(tr);
    const card=node('article',undefined,'target'+(state==='suspected'?' warning':''));
    const head=node('div',undefined,'target-head');
    head.append(node('span',t('target.id',{id:tr.track_id}),'target-id'),node('span',t('state.'+state),'tag tag-'+state));
    card.append(head);
    const kv=node('dl',undefined,'kv');
    const pair=(k,v)=>kv.append(node('dt',t(k)),node('dd',v));
    pair('k.position',`X ${num(tr.x_m)} · Y ${num(tr.y_m)} · Z ${num(tr.z_m)} m`);
    pair('k.speed',num(Math.hypot(tr.vx_mps,tr.vy_mps,tr.vz_mps))+' m/s');
    pair('k.height',num(tr.height_max_m)+' m');
    if(tr.height_filtered_m!==undefined) pair('k.filtered',num(tr.height_filtered_m)+' m');
    if(tr.reference_m!==undefined) pair('k.reference',num(tr.reference_m)+' m');
    card.append(kv);
    if(state==='low') {
      card.append(node('p',t('low.progress',{s:num(tr.low_seconds,0),max:floorMax()}),'target-line'));
      const bar=node('div',undefined,'progress'),fill=node('i');
      fill.style.width=Math.min(100,Math.max(0,(tr.low_seconds||0)/floorMax()*100))+'%';bar.append(fill);card.append(bar);
    }
    if(tr.fall_check) card.append(node('p',t('check.prefix')+dropText(tr.fall_check),'target-line'));
    const motion=I18N.zh['motion.'+tr.motion_state]!==undefined?t('motion.'+tr.motion_state):t('motion.none');
    card.append(node('p',motion,'target-line muted'));
    list.append(card);
  }
  list.append(node('p',t('targets.vitals'),'target-foot'));
}
function eventTitle(e) {
  const d=e.details||{},kind=kindOf(e),label=kindLabel(e);
  if(isAlert(e)) return t(e.acknowledged?'ev.reviewed':'ev.pending',{label});
  if(kind==='lie_down_rejected') return t('ev.lie',{label,safe:d.reason==='safe_zone'?t('ev.safe'):'',d:num(d.duration_s,1),v:num(d.peak_speed_mps,1)});
  if(kind==='crouch_rejected') return t('ev.crouch',{label,d:num(d.duration_s,1)});
  return t('ev.info',{label});
}
function paintEvents(force=false) {
  const signature=lang+evFilter+JSON.stringify(events);if(!force&&signature===lastEventsPaint)return;lastEventsPaint=signature;
  const pending=events.filter(e=>isAlert(e)&&!e.acknowledged).length;
  $('#alertCount').textContent=pending;$('#alertCount').className='count'+(pending?' is-alert':'');
  paintBanner();
  const list=$('#events');list.replaceChildren();
  const shown=events.filter(e=>evFilter==='all'||(evFilter==='alerts')===isAlert(e));
  if(!events.length||!shown.length) { list.append(node('p',t(events.length?'events.emptyFilter':'events.empty'),'empty'));return; }
  for(const e of shown) {
    const alert=isAlert(e),d=e.details||{},kind=kindOf(e);
    const row=node('div',undefined,'live-event kind-'+kind+(alert?(e.acknowledged?' ack':' pending'):' info')),body=node('div','','ev-body');
    const metrics=node('div',undefined,'ev-metrics');
    if(kind==='prolonged_low') metrics.append(node('span',t('ev.prolonged',{s:num(d.low_duration_s,0),h:num(d.low_h_m),r:num(d.reference_m)})));
    else if(d.drop_m!==undefined) metrics.append(node('span',dropText(d)));
    metrics.append(node('small',[t('ev.where',{x:num(d.x_m),y:num(d.y_m)})].concat(alert?[]:[t('ev.infoOnly')]).join(' · ')));
    body.append(node('span',fmtTime(e.timestamp),'ev-time'),node('b',eventTitle(e),'ev-title'),node('span',t('ev.track',{id:e.track_id}),'ev-target'),metrics);
    row.append(body);
    if(alert&&!e.acknowledged) { const b=node('button',t('btn.ack'),'btn btn-primary');b.addEventListener('click',async()=>{
      b.disabled=true;try {await api('/api/events/'+e.id+'/ack',{method:'POST',headers:{'X-Radar-Action':'ack'}});e.acknowledged=true;paintEvents(true);}catch(err){b.disabled=false;b.textContent=t('btn.ackRetry');}
    });row.append(b); }
    list.append(row);
  }
}
function paintRule() {
  const box=$('#fallRule');box.replaceChildren();
  const f=info.fall;
  if(infoState!=='ok'||!f) { box.append(node('p',t(infoState==='fail'?'rule.loadFail':infoState==='loading'?'rule.loading':'rule.unavailable'),'note'));return; }
  const column=(title,items,tag)=>{const c=node('div');c.append(node('h3',title));const l=node(tag);for(const s of items)l.append(node('li',s));c.append(l);return c;};
  box.append(
    column(t('rule.intro'),[t('rule.c1',{a:f.min_drop_m??0.6,b:f.max_low_height_m??0.7}),t('rule.c2',{a:f.fall_max_duration_s??1.2,b:f.fall_min_speed_mps??1}),
      t('rule.c3',{a:f.confirm_seconds}),t('rule.c4',{n:(f.safe_zones||[]).length})],'ol'),
    column(t('rule.outcomes'),[t('rule.o1'),t('rule.o2',{a:f.confirm_seconds}),t('rule.o3',{a:f.floor_alert_s??30}),
      t('rule.o4',{r:Math.round((f.recovery_ratio??.85)*100),s:f.recovery_seconds??2,c:f.cooldown_seconds}),t('targets.note')],'ul'));
}
function paintMotion() { $('#motionNote').textContent=infoState==='ok'?t(info.motion_filter_enabled?'motion.on':'motion.off'):''; }
function paintClock() { $('#clock').textContent=new Date().toLocaleTimeString(locale()); }

// ---------------------------------------------------------------- polling
async function pollState() {
  try { const s=await api('/api/state');if(!Array.isArray(s.tracks))throw Error('Invalid state');current=s;receivedAt=Date.now(); }
  catch(err) { current={status:'unreachable',tracks:[],points:[],error:err&&err.message?err.message:String(err)}; }
  paintStatus();paintTargets();setTimeout(pollState,200);
}
async function pollEvents() {
  try {const list=await api('/api/events');if(!Array.isArray(list))throw Error('Invalid event list');events=list;eventFault='';paintEvents();}
  catch(err) {eventFault='diag.eventFault';$('#alertCount').textContent='—';}
  setTimeout(pollEvents,1000);
}

// ---------------------------------------------------------------- canvas
const FONT='16px "IBM Plex Sans", "Microsoft YaHei UI", sans-serif', FONT_BOLD='600 16px "IBM Plex Sans", "Microsoft YaHei UI", sans-serif';
function mapPoint(x,y,w,h) {
  const [a,b,c,d]=settings.bounds,scale=Math.min((w-100)/(b-a),(h-84)/(d-c));
  const left=(w-(b-a)*scale)/2+10,top=(h-(d-c)*scale)/2;
  return [left+(x-a)*scale,top+(d-y)*scale];
}
// Text with a halo in the background colour, so grid lines never cut through labels.
function label(text,x,y,color){ctx.lineWidth=5;ctx.lineJoin='round';ctx.strokeStyle=PALETTE.stage;ctx.strokeText(text,x,y);ctx.lineWidth=1;ctx.fillStyle=color;ctx.fillText(text,x,y);}
function zoneRect(z,w,h) { const [x0,y0]=mapPoint(z[0],z[3],w,h),[x1,y1]=mapPoint(z[1],z[2],w,h);return [x0,y0,x1-x0,y1-y0]; }
function draw() {
  const rect=canvas.getBoundingClientRect(),w=Math.max(rect.width,1),h=Math.max(rect.height,1),dpr=window.devicePixelRatio||1;
  if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.font=FONT;ctx.lineWidth=1;
  ctx.fillStyle=PALETTE.stage;ctx.fillRect(0,0,w,h);
  const accent=customAccent()?settings.accent:PALETTE.accent;
  if(cloudMode){
    const scale=Math.min(w/5,h/4),project=(x,y,z)=>[w/2+(x*.8+y*.35)*scale,h*.75+(x*.15-y*.25-z*.6)*scale];
    ctx.strokeStyle=PALETTE.major;
    for(let i=-2;i<=2;i++){const a=project(i,0,0),b=project(i,3,0);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
    for(let i=0;i<=3;i++){const a=project(-2,i,0),b=project(2,i,0);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
    const points=usable()?(current.points||[]):[];
    for(const p of points){const [x,y]=project(p[0],p[1],p[2]);ctx.fillStyle=`hsl(${215-Math.max(-2,Math.min(2,p[2]))*45} 75% ${PALETTE.cloudL}%)`;ctx.fillRect(x,y,3,3);}
    label(t('canvas.sensor'),16,26,PALETTE.label);
    if(!points.length) label(usable()?t('canvas.noPoints'):t('canvas.waiting'),16,48,PALETTE.text);
    $('#plotNote').textContent=t('plot.cloud',{shown:points.length,total:current.point_count??0});
  }else{
    const [a,b,c,d]=settings.bounds;
    const [left,bottom]=mapPoint(a,c,w,h),[right,top]=mapPoint(b,d,w,h);
    // Equal scales on both axes; adaptive grid keeps large ranges bounded. Whole metres are drawn darker.
    const step=Math.max(.5,Math.ceil(Math.max(b-a,d-c)/30)*.5);
    for(let x=Math.ceil(a/step)*step;x<=b+1e-9;x+=step){
      const px=Math.round(mapPoint(x,c,w,h)[0])+.5;ctx.strokeStyle=Math.abs(x)<1e-9?PALETTE.edge:isWhole(x)?PALETTE.major:PALETTE.grid;ctx.beginPath();ctx.moveTo(px,top);ctx.lineTo(px,bottom);ctx.stroke();
      ctx.fillStyle=PALETTE.label;ctx.textAlign='center';ctx.fillText(num(x,1),px,bottom+20);
    }
    for(let y=Math.ceil(c/step)*step;y<=d+1e-9;y+=step){
      const py=Math.round(mapPoint(a,y,w,h)[1])+.5;ctx.strokeStyle=isWhole(y)?PALETTE.major:PALETTE.grid;ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(right,py);ctx.stroke();
      ctx.fillStyle=PALETTE.label;ctx.textAlign='right';ctx.fillText(num(y,1),left-10,py+5);
    }
    ctx.textAlign='left';ctx.strokeStyle=PALETTE.edge;ctx.strokeRect(left+.5,top+.5,right-left,bottom-top);
    if(zones&&zones.enabled){
      ctx.setLineDash([3,3]);ctx.strokeStyle=PALETTE.label;ctx.strokeRect(...zoneRect(zones.roi,w,h));ctx.setLineDash([]);
      for(const z of zones.exclusions){const r=zoneRect(z,w,h);ctx.fillStyle=PALETTE.excluded;ctx.fillRect(...r);label(t('canvas.excluded'),r[0]+6,r[1]+18,PALETTE.label);}
    }
    for(const z of (info.fall&&info.fall.safe_zones)||[]){
      const r=zoneRect(z,w,h);ctx.fillStyle=PALETTE.safe;ctx.fillRect(...r);
      ctx.setLineDash([6,4]);ctx.lineWidth=1.5;ctx.strokeStyle=PALETTE.ok;ctx.strokeRect(...r);ctx.setLineDash([]);ctx.lineWidth=1;
      ctx.font=FONT_BOLD;label(t('canvas.safe'),r[0]+8,r[1]+20,PALETTE.ok);ctx.font=FONT;
    }
    if(a<=0&&b>=0&&c<=0&&d>=0){
      const [rx,ry]=mapPoint(0,0,w,h);ctx.fillStyle=PALETTE.text;ctx.beginPath();ctx.moveTo(rx,ry-10);ctx.lineTo(rx-7,ry);ctx.lineTo(rx+7,ry);ctx.closePath();ctx.fill();
      label(t('canvas.radar'),rx+12,ry-3,PALETTE.label);
    }
    for(const tr of tracks()){
      if(tr.x_m<a||tr.x_m>b||tr.y_m<c||tr.y_m>d)continue;
      const state=targetState(tr),color=state==='suspected'?PALETTE.alert:state==='low'?PALETTE.warn:accent;
      const [x,y]=mapPoint(tr.x_m,tr.y_m,w,h);
      if(state==='suspected'){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,15,0,Math.PI*2);ctx.stroke();ctx.lineWidth=1;}
      ctx.fillStyle=color;ctx.strokeStyle=PALETTE.stage;ctx.lineWidth=2.5;ctx.beginPath();ctx.arc(x,y,9,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.lineWidth=1;
      ctx.font=FONT_BOLD;label('ID '+tr.track_id,x+16,y-5,PALETTE.text);ctx.font=FONT;
      label(num(tr.x_m,1)+', '+num(tr.y_m,1)+' m',x+16,y+13,PALETTE.label);
    }
    label(t('canvas.axes'),16,26,PALETTE.label);
    $('#plotNote').textContent=t('plot.map');
  }
  requestAnimationFrame(draw);
}

// ---------------------------------------------------------------- backend settings
async function loadFilters(){try{const f=await api('/api/filters');zones=f;$('#filterEnabled').checked=f.enabled;['fx0','fx1','fy0','fy1'].forEach((id,i)=>$('#'+id).value=f.roi[i]);$('#exclusions').value=f.exclusions.map(b=>b.join(',')).join('\n');$('#saveFilters').disabled=false;$('#filterMessage').textContent=t('filters.loaded');}catch(_){$('#filterMessage').textContent=t('filters.loadFail');}}
$('#filterForm').addEventListener('submit',async e=>{e.preventDefault();try{
  const text=$('#exclusions').value.trim(),roi=['fx0','fx1','fy0','fy1'].map(id=>Number($('#'+id).value));
  const exclusions=text?text.split('\n').filter(x=>x.trim()).map(x=>x.split(',').map(v=>v.trim()===''?NaN:Number(v))):[];
  if(!validBounds(roi)||!exclusions.every(validBounds))throw Error(t('filters.invalid'));
  const body={enabled:$('#filterEnabled').checked,roi,exclusions};
  await api('/api/filters',{method:'POST',headers:{'Content-Type':'application/json','X-Radar-Action':'configure'},body:JSON.stringify(body)});zones=body;$('#filterMessage').textContent=t('filters.saved');
}catch(err){$('#filterMessage').textContent=t('filters.saveFail',{msg:err.message});}});
async function loadInfo(){
  try{info=await api('/api/info');infoState='ok';}catch(_){infoState='fail';}
  paintRule();paintMotion();paintBanner();
}

applyTheme();setMode(false);applyLang();loadFilters();loadInfo();pollState();pollEvents();draw();
$('#bootError').hidden=true;   // started: hide the 'script did not load' warning
setInterval(()=>{paintClock();if(!usable()){paintStatus();paintTargets();}},1000);
