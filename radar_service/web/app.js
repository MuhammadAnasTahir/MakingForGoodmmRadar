'use strict';
const $ = s => document.querySelector(s);
const clone = v => JSON.parse(JSON.stringify(v));
const defaults = clone(window.RADAR_DEFAULTS);
let settings = clone(defaults);
try { Object.assign(settings, JSON.parse(localStorage.getItem('radarsense-appearance-v2') || '{}')); } catch (_) {}
const scenes = ['activity','bedroom','shared','lounge','restroom','corridor'];
if (!scenes.includes(settings.scene)) settings.scene = defaults.scene;
const validBounds = b => Array.isArray(b) && b.length===4 && b.every(Number.isFinite) && b[0]<b[1] && b[2]<b[3];
if (!validBounds(settings.bounds)) settings.bounds = defaults.bounds;
let current = {status:'starting',tracks:[],points:[]}, events = [], cloudMode = false;
let eventFault = '', info = {}, lastEventsPaint = '', receivedAt = 0;
const canvas = $('#radarCanvas'), ctx = canvas.getContext('2d');
const num = (v,n=2) => typeof v==='number' && Number.isFinite(v) ? v.toFixed(n) : '—';
const stateLabels = {streaming:'在线 · 接收中',starting:'连接中',configuring:'发送雷达配置中',disconnected:'雷达离线',stale:'数据已过期',unreachable:'后台不可达'};
function usable() { return current.status==='streaming' && Date.now()-receivedAt<3000; }
function tracks() { return usable() ? (current.tracks || []) : []; }
function applyAppearance() {
  $('#title').textContent = settings.roomName; $('#roomLabel').textContent = settings.roomName;
  for (const [key,css] of [['accent','--accent'],['background','--bg']]) {
    if (/^#[0-9a-f]{6}$/i.test(settings[key])) document.documentElement.style.setProperty(css,settings[key]);
  }
  $('#roomScene').src = 'assets/scene-'+settings.scene+'.png';
  for (const id of ['roomName','accent','background','scene']) $('#'+id).value=settings[id];
  ['mapXmin','mapXmax','mapYmin','mapYmax'].forEach((id,i)=>$('#'+id).value=settings.bounds[i]);
}
$('#appearanceForm').addEventListener('submit', e => {
  e.preventDefault();
  const b=['mapXmin','mapXmax','mapYmin','mapYmax'].map(id=>Number($('#'+id).value));
  if (!validBounds(b)) { $('#appearanceMessage').textContent='坐标最小值必须小于最大值';return; }
  settings={roomName:$('#roomName').value.trim() || defaults.roomName,accent:$('#accent').value,background:$('#background').value,scene:$('#scene').value,bounds:b};
  try { localStorage.setItem('radarsense-appearance-v2',JSON.stringify(settings));$('#appearanceMessage').textContent='已保存到当前浏览器'; }
  catch (_) { $('#appearanceMessage').textContent='已应用，但浏览器不允许保存'; }
  applyAppearance();
});
$('#resetAppearance').addEventListener('click',()=>{settings=clone(defaults);try{localStorage.removeItem('radarsense-appearance-v2');}catch(_){}applyAppearance();});
$('#sceneTab').addEventListener('click',()=>setMode(false));
$('#cloudTab').addEventListener('click',()=>setMode(true));
function setMode(value) { cloudMode=value;$('#sceneTab').classList.toggle('active',!value);$('#cloudTab').classList.toggle('active',value);$('#roomScene').hidden=true; }
async function api(path, options={}) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),2500);
  try { const r=await fetch(path,{cache:'no-store',...options,signal:controller.signal});if(!r.ok)throw Error('HTTP '+r.status);return await r.json(); }
  finally { clearTimeout(timer); }
}
function node(tag,text,cls) { const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e; }
const ALERT_KINDS=['fall','prolonged_low'];
const kindOf=e=>e.kind||'fall';
const isAlert=e=>ALERT_KINDS.includes(kindOf(e));
const kindLabels={fall:'疑似跌倒',prolonged_low:'长时间低位',lie_down_rejected:'主动躺下',crouch_rejected:'下蹲/弯腰'};
function dropText(d) {
  return `下降 ${num(d.drop_m)} m，用时 ${num(d.duration_s,1)} s，峰值速度 ${num(d.peak_speed_mps,1)} m/s，低位高度 ${num(d.low_h_m)} m（参考 ${num(d.reference_m)} m）`;
}
function eventTitle(e) {
  const d=e.details||{},kind=kindOf(e),label=kindLabels[kind]||kind;
  if(isAlert(e)) return e.acknowledged?`已查看 · ${label}记录`:`${label} · 待人工查看`;
  if(kind==='lie_down_rejected') return `${label} · ${d.reason==='safe_zone'?'安全区内，':''}已排除（下降 ${num(d.duration_s,1)} s，${num(d.peak_speed_mps,1)} m/s）`;
  if(kind==='crouch_rejected') return `${label} · 已排除（${num(d.duration_s,1)} s 内站起）`;
  return `${label} · 信息记录`;
}
function paintTargets() {
  const list=$('#targets');list.replaceChildren();
  const ts=tracks();
  if(!ts.length) { list.append(node('p',usable()?'当前没有保留的跟踪目标':'等待有效雷达数据','live-note'));return; }
  for(const t of ts) {
    const warning=t.fall_state==='suspected_fall',low=t.fall_state==='low';
    const card=node('article',undefined,'live-target'+(warning?' warning':''));
    card.append(node('b','目标 ID '+t.track_id+(warning?' · 疑似跌倒':low?' · 低位（未报警）':' · 跟踪中')));
    card.append(node('p',`X ${num(t.x_m)} m · Y ${num(t.y_m)} m · Z ${num(t.z_m)} m`));
    card.append(node('p',`速度 ${num(Math.hypot(t.vx_mps,t.vy_mps,t.vz_mps))} m/s · 最大高度估计 ${num(t.height_max_m)} m`));
    if(t.height_filtered_m!==undefined) card.append(node('p',`滤波高度 ${num(t.height_filtered_m)} m · 参考高度 ${num(t.reference_m)} m`));
    if(low) card.append(node('p',`低位已 ${num(t.low_seconds,0)} s；安全区外到 ${info.fall?.floor_alert_s??30} s 报“长时间低位”`));
    if(t.fall_check) card.append(node('p','本次下降：'+dropText(t.fall_check)));
    const motion={motion_confirmed:'已观察到位置变化',unconfirmed:'运动待确认'}[t.motion_state]||'运动状态未提供';
    card.append(node('p',motion+'；非人体身份确认。'));
    card.append(node('p','姿态：未分类 · 心率：未接入 · 呼吸：未接入'));
    list.append(card);
  }
}
function paintStatus() {
  const status=current.status==='streaming'&&!usable()?'stale':current.status;
  $('#connection').textContent=stateLabels[status]||status;
  $('#sidebarStatus').textContent=usable()?'本地真实数据 · 1 个节点':'没有有效实时数据';
  $('#targetCount').textContent=tracks().length;
  $('#frame').textContent=current.frame_number??'—';
  $('#age').textContent=num(current.frame_age_seconds,2)+' s';
  $('#diagnostics').textContent=[`原始目标 ${current.raw_track_count??'—'}`,`区域过滤 ${current.region_filtered_count??'—'}`,`静态过滤 ${current.motion_filtered_count??'—'}`,`原始点 ${current.point_count??'—'}`,current.error||'',eventFault].filter(Boolean).join(' · ');
}
function paintEvents(force=false) {
  const signature=JSON.stringify(events);if(!force&&signature===lastEventsPaint)return;lastEventsPaint=signature;
  $('#alertCount').textContent=events.filter(e=>isAlert(e)&&!e.acknowledged).length;
  const list=$('#events');list.replaceChildren();
  if(!events.length) { list.append(node('p','暂无记录（最多显示最近 100 条报警和 50 条已排除记录）','live-note'));return; }
  for(const e of events) {
    const alert=isAlert(e),d=e.details||{};
    const row=node('div',undefined,'live-event'+(alert?(e.acknowledged?' ack':''):' info')),body=node('div');
    body.append(node('b',eventTitle(e)));
    body.append(node('p',new Date(e.timestamp*1000).toLocaleString()+' · ID '+e.track_id));
    if(kindOf(e)==='prolonged_low') body.append(node('p',`低位已持续 ${num(d.low_duration_s,0)} s 未站起；低位高度 ${num(d.low_h_m)} m（参考 ${num(d.reference_m)} m）`));
    else if(d.drop_m!==undefined) body.append(node('p',dropText(d)));
    body.append(node('p',alert?`发生位置：X ${num(d.x_m)} m，Y ${num(d.y_m)} m；已查看不代表已确认实际跌倒。`:`位置：X ${num(d.x_m)} m，Y ${num(d.y_m)} m · 仅记录，不报警`));row.append(body);
    if(alert&&!e.acknowledged) { const b=node('button','确认已查看','live-button');b.addEventListener('click',async()=>{
      b.disabled=true;try {await api('/api/events/'+e.id+'/ack',{method:'POST',headers:{'X-Radar-Action':'ack'}});e.acknowledged=true;paintEvents(true);}catch(err){b.disabled=false;b.textContent='确认失败，点击重试';}
    });row.append(b); }
    list.append(row);
  }
}
async function pollState() {
  try { const s=await api('/api/state');if(!Array.isArray(s.tracks))throw Error('Invalid state');current=s;receivedAt=Date.now(); }
  catch(err) { current={status:'unreachable',tracks:[],points:[],error:String(err)}; }
  paintStatus();paintTargets();setTimeout(pollState,200);
}
async function pollEvents() {
  try {const list=await api('/api/events');if(!Array.isArray(list))throw Error('Invalid event list');events=list;eventFault='';paintEvents();}
  catch(err) {eventFault='报警记录读取失败，旧记录仅供参考';$('#alertCount').textContent='—';}
  setTimeout(pollEvents,1000);
}
function mapPoint(x,y,w,h) {
  const [a,b,c,d]=settings.bounds,scale=Math.min((w-90)/(b-a),(h-80)/(d-c));
  const left=(w-(b-a)*scale)/2,top=(h-(d-c)*scale)/2;
  return [left+(x-a)*scale,top+(d-y)*scale];
}
function draw() {
  const rect=canvas.getBoundingClientRect(),w=Math.max(rect.width,1),h=Math.max(rect.height,1),dpr=window.devicePixelRatio||1;
  if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.font='14px sans-serif';
  if(cloudMode){
    ctx.fillStyle='#101c2b';ctx.fillRect(0,0,w,h);
    const scale=Math.min(w/5,h/4),project=(x,y,z)=>[w/2+(x*.8+y*.35)*scale,h*.75+(x*.15-y*.25-z*.6)*scale];
    ctx.strokeStyle='#38516c';
    for(let i=-2;i<=2;i++){const a=project(i,0,0),b=project(i,3,0);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
    for(let i=0;i<=3;i++){const a=project(-2,i,0),b=project(2,i,0);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
    const points=usable()?(current.points||[]):[];
    for(const p of points){const [x,y]=project(p[0],p[1],p[2]);ctx.fillStyle=`hsl(${180-Math.max(-2,Math.min(2,p[2]))*50} 80% 65%)`;ctx.fillRect(x,y,3,3);}
    ctx.fillStyle='#cce0f5';ctx.fillText('传感器坐标 XYZ · 非离地高度',14,24);
    if(!points.length)ctx.fillText(usable()?'当前帧无点云输出':'等待雷达数据',14,48);
    $('#plotNote').textContent=`原始点云三维投影，显示 ${points.length} / ${current.point_count??0} 点；最多传输 2000 点。环境反射也会显示，目标过滤不删除原始点。`;
  }else{
    ctx.fillStyle='#101c2b';ctx.fillRect(0,0,w,h);
    const [a,b,c,d]=settings.bounds;
    const [left,bottom]=mapPoint(a,c,w,h),[right,top]=mapPoint(b,d,w,h);
    // Equal scales on both axes; adaptive grid keeps large ranges bounded.
    const step=Math.max(.5,Math.ceil(Math.max(b-a,d-c)/30)*.5);
    ctx.lineWidth=1;
    for(let x=Math.ceil(a/step)*step;x<=b;x+=step){
      const px=mapPoint(x,c,w,h)[0];ctx.strokeStyle='#33465b';ctx.beginPath();ctx.moveTo(px,top);ctx.lineTo(px,bottom);ctx.stroke();
      ctx.fillStyle='#c4d3e6';ctx.fillText(num(x,1),px-10,bottom+19);
    }
    for(let y=Math.ceil(c/step)*step;y<=d;y+=step){
      const py=mapPoint(a,y,w,h)[1];ctx.strokeStyle='#33465b';ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(right,py);ctx.stroke();
      ctx.fillStyle='#c4d3e6';ctx.fillText(num(y,1),Math.max(2,left-36),py+4);
    }
    if(a<=0&&b>=0&&c<=0&&d>=0){const [rx,ry]=mapPoint(0,0,w,h);ctx.fillStyle='#6cc9ff';ctx.fillRect(rx-5,ry-5,10,10);ctx.fillText('Radar',rx+10,ry-8);}
    for(const t of tracks()){
      if(t.x_m<a||t.x_m>b||t.y_m<c||t.y_m>d)continue;
      const [x,y]=mapPoint(t.x_m,t.y_m,w,h);ctx.fillStyle=t.fall_state==='suspected_fall'?'#ff7777':'#65dcc4';ctx.beginPath();ctx.arc(x,y,9,0,Math.PI*2);ctx.fill();ctx.fillText('ID '+t.track_id+'  '+num(t.x_m,1)+', '+num(t.y_m,1)+' m',x+13,y-9);
    }
    ctx.fillStyle='#c4d3e6';ctx.fillText('X: left / right   Y: forward (m)',14,22);
    $('#plotNote').textContent='雷达相对坐标，X/Y 等比例。不是已标定的房间平面图；历史报警仅在下方列表显示。';

  }
  requestAnimationFrame(draw);
}
async function loadFilters(){try{const f=await api('/api/filters');$('#filterEnabled').checked=f.enabled;['fx0','fx1','fy0','fy1'].forEach((id,i)=>$('#'+id).value=f.roi[i]);$('#exclusions').value=f.exclusions.map(b=>b.join(',')).join('\n');$('#saveFilters').disabled=false;$('#filterMessage').textContent='已读取当前后台设置';}catch(_){$('#filterMessage').textContent='读取失败，请刷新重试；未覆盖原设置';}}
$('#filterForm').addEventListener('submit',async e=>{e.preventDefault();try{
  const text=$('#exclusions').value.trim(),roi=['fx0','fx1','fy0','fy1'].map(id=>Number($('#'+id).value));
  const exclusions=text?text.split('\n').filter(x=>x.trim()).map(x=>x.split(',').map(v=>v.trim()===''?NaN:Number(v))):[];
  if(!validBounds(roi)||!exclusions.every(validBounds))throw Error('请填写有效矩形：xmin,xmax,ymin,ymax');
  await api('/api/filters',{method:'POST',headers:{'Content-Type':'application/json','X-Radar-Action':'configure'},body:JSON.stringify({enabled:$('#filterEnabled').checked,roi,exclusions})});$('#filterMessage').textContent='保存成功，下一帧生效';
}catch(err){$('#filterMessage').textContent='保存失败：'+err.message;}});
async function loadInfo(){try{
  info=await api('/api/info');const f=info.fall;
  const zones=(f&&f.safe_zones||[]).length;
  $('#fallRule').textContent=f?`“疑似跌倒”需同时满足：① 下降大：比平时站立高度低 ${f.min_drop_m??0.6} 米以上，且降到 ${f.max_low_height_m??0.7} 米以下；② 下降快：10%→90% 的下降在 ${f.fall_max_duration_s??1.2} 秒内完成，最快下降速度达到 ${f.fall_min_speed_mps??1} 米/秒；③ ${f.confirm_seconds} 秒后仍在低位；④ 不在安全区（已设 ${zones} 个，如床）。`+
    `慢慢躺下只记为灰色“主动躺下 · 已排除”，不报警；${f.confirm_seconds} 秒内站起记为“下蹲/弯腰 · 已排除”。慢慢滑倒后在安全区外低位超过 ${f.floor_alert_s??30} 秒，报“长时间低位”。`+
    `同一次低位只报警一次；恢复到参考高度 ${Math.round((f.recovery_ratio??.85)*100)}% 并稳定 ${f.recovery_seconds??2} 秒后重新允许报警；同一目标冷却 ${f.cooldown_seconds} 秒。高度是雷达目标估计，不是头部高度。`:'规则未提供';
  $('#motionNote').textContent=info.motion_filter_enabled?'运动历史过滤已启用：未移动目标在 30 秒后隐藏；移动过的目标保持确认。不能可靠区分人与家具，可能漏掉一直静坐的人。':'运动历史过滤已关闭。';
}catch(_){$('#fallRule').textContent='无法读取后台规则';}}

applyAppearance();setMode(false);loadFilters();loadInfo();pollState();pollEvents();draw();
setInterval(()=>{$('#clock').textContent=new Date().toLocaleTimeString();if(!usable()){paintStatus();paintTargets();}},1000);
