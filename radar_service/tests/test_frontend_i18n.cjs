// Language toggle: every key exists in both languages, index.html has no untagged Chinese text,
// and in English mode nothing the page renders (DOM text or canvas text) contains Chinese.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const web=path.join(__dirname,'../web');
const CJK=/[　-〿㐀-鿿＀-￯]/;

// ---- 1. dictionaries have the same keys and no empty values
const dictContext={window:{}};vm.createContext(dictContext);
vm.runInContext(fs.readFileSync(path.join(web,'i18n.js'),'utf8'),dictContext);
const {zh,en}=dictContext.window.RADAR_I18N;
assert.deepStrictEqual(Object.keys(en).sort(),Object.keys(zh).sort(),'zh and en must have identical keys');
for(const [k,v] of Object.entries(en)){assert(v.length,'empty en '+k);assert(!CJK.test(v)||k==='canvas.axes','Chinese text in en '+k);}
for(const k of Object.keys(zh)){const vars=s=>(s.match(/\{\w+\}/g)||[]).sort().join();assert.equal(vars(en[k]),vars(zh[k]),'placeholders differ: '+k);}

// ---- 2. index.html: every key exists; Chinese only inside data-i18n elements or the language buttons
const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
for(const m of html.matchAll(/data-i18n(?:-aria|-alt|-placeholder|-title)?="([^"]+)"/g))assert(m[1] in zh,'unknown key in index.html: '+m[1]);
const stripped=html.replace(/<!--[\s\S]*?-->/g,'')
  .replace(/(<(\w+)[^>]*\sdata-i18n="[^"]+"[^>]*>)[^<]*(<\/\2>)/g,'$1$3')       // tagged text
  .replace(/\s(aria-label|alt|placeholder|title)="[^"]*"/g,'')                          // attributes are set from data-i18n-*
  .replace(/<button id="lang(Zh|En)"[^>]*>[^<]*<\/button>/g,'')                  // language names stay in their own language
  .replace(/<div id="bootError"[\s\S]*?<\/div>/,'');                                // start-up failure banner is bilingual on purpose
const left=stripped.split('\n').filter(l=>CJK.test(l));
assert.deepStrictEqual(left,[],'untranslatable Chinese text in index.html');
for(const m of html.matchAll(/\s(aria-label|alt|placeholder|title)="([^"]*)"/g))
  if(CJK.test(m[2]))assert(new RegExp(`data-i18n-(aria|alt|placeholder|title)="[^"]+"[^>]*\\s${m[1]}="${m[2]}"|${m[1]}="${m[2]}"[^>]*data-i18n-`).test(html),'attribute without key: '+m[0]);

// ---- 3. run the page in English and collect every rendered string
class Element{
 constructor(){this.children=[];this.value='';this.textContent='';this.className='';this.listeners={};this.attrs={};this.style={setProperty(){}};
  const self=this;this.classList={toggle(c,on){self.classes=self.classes||new Set();on?self.classes.add(c):self.classes.delete(c)}};}
 append(...xs){this.children.push(...xs)}
 replaceChildren(...xs){this.children=xs}
 addEventListener(name,fn){this.listeners[name]=fn}
 setAttribute(n,v){this.attrs[n]=v}
 getBoundingClientRect(){return {width:700,height:400}}
 getContext(){return new Proxy({}, {get:(_,p)=>p==='fillText'?(s)=>canvasText.push(String(s)):()=>{},set:()=>true})}
}
const canvasText=[],stored={'radarsense-lang':'en'};
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const fall={window_seconds:2.5,height_ratio:.6,confirm_seconds:1,cooldown_seconds:30,recovery_ratio:.85,recovery_seconds:2,
 min_drop_m:.6,max_low_height_m:.7,fall_max_duration_s:1.2,fall_min_speed_mps:1,floor_alert_s:30,safe_zones:[[-2,-1,2,4]]};
const state={status:'streaming',frame_number:3,frame_age_seconds:.02,point_count:1,points:[[0,1,0,0]],tracks:[
 {track_id:4,x_m:0,y_m:2,z_m:.3,vx_mps:0,vy_mps:0,vz_mps:0,height_max_m:.35,height_filtered_m:.34,reference_m:1.72,fall_state:'low',low_seconds:12,
  fall_check:{drop_m:1.38,duration_s:2.1,peak_speed_mps:.6,low_h_m:.34,reference_m:1.72},motion_state:'motion_confirmed'},
 {track_id:5,x_m:1,y_m:1,z_m:1,vx_mps:.3,vy_mps:0,vz_mps:0,height_max_m:1.7,fall_state:'suspected_fall',motion_state:'unconfirmed'},
 {track_id:6,x_m:-1,y_m:3,z_m:1,vx_mps:0,vy_mps:0,vz_mps:0,height_max_m:null,fall_state:'height_unavailable'}]};
const events=[
 {id:6,timestamp:1006,track_id:4,acknowledged:false,kind:'lie_down_rejected',details:{x_m:0,y_m:2,drop_m:1.38,duration_s:2.1,peak_speed_mps:.6,low_h_m:.34,reference_m:1.72,reason:'safe_zone'}},
 {id:5,timestamp:1005,track_id:4,acknowledged:false,kind:'crouch_rejected',details:{x_m:0,y_m:2,duration_s:.8,drop_m:.9,low_h_m:.8,reference_m:1.7}},
 {id:4,timestamp:1004,track_id:3,acknowledged:true,kind:'prolonged_low',details:{x_m:1,y_m:2,low_duration_s:30,low_h_m:.4,reference_m:1.7}},
 {id:3,timestamp:1003,track_id:2,acknowledged:false,kind:'fall',details:{x_m:1,y_m:1,drop_m:1.4,duration_s:.5,peak_speed_mps:2.7,low_h_m:.3,reference_m:1.7}}];
let online=true;
const context={console,Date,Math,JSON,Number,Array,Object,Error,AbortController,Promise,URLSearchParams,location:{search:''},matchMedia:()=>({matches:false}),
 document:{querySelector:get,querySelectorAll:()=>[],createElement:()=>new Element(),documentElement:new Element()},
 window:{devicePixelRatio:1},localStorage:{getItem:k=>stored[k]??null,setItem(k,v){stored[k]=v},removeItem(k){delete stored[k]}},
 requestAnimationFrame(){},setTimeout:()=>0,clearTimeout(){},setInterval(){},
 fetch:async url=>{
  if(!online)throw Error('offline');
  let body;if(url==='/api/state')body=state;else if(url==='/api/events')body=events;
  else if(url==='/api/filters')body={enabled:true,roi:[-2,2,0,4],exclusions:[[1.5,2,3.5,4]]};
  else body={fall,motion_filter_enabled:true};
  return {ok:true,json:async()=>body};
 }};
vm.createContext(context);
for(const f of ['data.js','i18n.js','app.js'])vm.runInContext(fs.readFileSync(path.join(web,f),'utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const texts=e=>[e.textContent,e.value,...Object.values(e.attrs||{}),...e.children.map(texts)].flat(Infinity).filter(x=>typeof x==='string').join(' ');
const allText=()=>[...elements.values()].map(texts).join(' ')+' '+context.document.title;
(async()=>{
 await flush();await flush();
 const draw=context.draw;draw();get('#cloudTab').listeners.click();draw();get('#sceneTab').listeners.click();
 for(const id of ['#evAlerts','#evInfo','#evAll'])get(id).listeners.click();
 get('#appearanceForm').listeners.submit({preventDefault(){}});              // appearance message
 await get('#filterForm').listeners.submit({preventDefault(){}});            // filter message
 const en=allText();
 {const i=en.search(CJK);assert(i<0,'Chinese text in English mode: '+JSON.stringify(en.slice(Math.max(0,i-60),i+40)));}
 assert(!canvasText.some(s=>CJK.test(s)),'Chinese canvas text in English mode: '+canvasText.filter(s=>CJK.test(s)));
 for(const s of ['Lying down · in safe zone, excluded (drop took 2.1 s, 0.6 m/s)','Crouch or bend · excluded','Reviewed · Prolonged low',
   'Suspected fall · needs review','Low (no alert)','Height unavailable','Fast drop','Online · receiving','RadarSense live monitor'])
   assert(en.includes(s),'missing English text: '+s);
 for(const s of ['Safe zone','Excluded','Radar','X: left/right   Y: forward (m)','Radar coordinates (height is not measured from the floor)'])
   assert(canvasText.includes(s),'missing canvas text: '+s);
 assert.equal(context.document.documentElement.lang,'en');
 // status line (English): one pending fall alert outranks the person who is low
 assert(en.includes('1 alert is waiting to be checked'),'banner title');assert.equal(get('#roomStatus').className,'banner is-alert');
 // theme toggle: flips data-theme, persists, and switches the map palette
 const root=context.document.documentElement,before=root.attrs['data-theme'];
 get('#themeToggle').listeners.click();
 assert.notEqual(root.attrs['data-theme'],before);assert.equal(stored['radarsense-theme'],root.attrs['data-theme']);
 get('#themeToggle').listeners.click();assert.equal(root.attrs['data-theme'],before);
 assert.equal(get('#langEn').attrs['aria-pressed'],'true');

 // ---- 4. toggle back to Chinese: persisted, and the same views now render Chinese
 get('#langZh').listeners.click();
 assert.equal(stored['radarsense-lang'],'zh');assert.equal(context.document.documentElement.lang,'zh-CN');
 const zhText=allText();
 for(const s of ['主动躺下 · 安全区内，已排除（下降 2.1 s，0.6 m/s）','疑似跌倒 · 待人工查看','下降大','在线 · 接收中','RadarSense 实时监测'])
   assert(zhText.includes(s),'missing Chinese text: '+s);
 assert.equal(get('#langZh').attrs['aria-pressed'],'true');

 // ---- 5. offline in English
 get('#langEn').listeners.click();online=false;await context.pollState();
 const off=allText();assert(off.includes('Backend unreachable'));assert(!CJK.test(off));
 console.log('PASS frontend i18n: same keys in zh/en, no untagged Chinese in HTML, English mode renders no Chinese (DOM + canvas), toggle persists');
})().catch(e=>{console.error(e);process.exitCode=1});
