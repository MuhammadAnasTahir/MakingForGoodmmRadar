// Event kinds on the page: info events greyed and not counted, new rule text, low-state target card.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const web=path.join(__dirname,'../web');
class Element{
 constructor(){this.children=[];this.value='';this.textContent='';this.className='';this.listeners={};this.classList={toggle(){}};this.style={setProperty(){}};}
 append(...xs){this.children.push(...xs)}
 replaceChildren(...xs){this.children=xs}
 addEventListener(name,fn){this.listeners[name]=fn}
 getBoundingClientRect(){return {width:700,height:400}}
 getContext(){return new Proxy({}, {get:()=>()=>{},set:()=>true})}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const state={status:'streaming',frame_number:3,frame_age_seconds:.02,points:[],point_count:0,tracks:[
 {track_id:4,x_m:0,y_m:2,z_m:.3,vx_mps:0,vy_mps:0,vz_mps:0,height_max_m:.35,height_filtered_m:.34,reference_m:1.72,fall_state:'low',low_seconds:12,
  fall_check:{drop_m:1.38,duration_s:2.1,peak_speed_mps:.6,low_h_m:.34,reference_m:1.72},motion_state:'motion_confirmed'}]};
const events=[
 {id:6,timestamp:1006,track_id:4,acknowledged:false,kind:'lie_down_rejected',details:{x_m:0,y_m:2,drop_m:1.38,duration_s:2.1,peak_speed_mps:.6,low_h_m:.34,reference_m:1.72,reason:'slow'}},
 {id:5,timestamp:1005,track_id:4,acknowledged:false,kind:'crouch_rejected',details:{x_m:0,y_m:2,duration_s:.8,drop_m:.9,low_h_m:.8,reference_m:1.7}},
 {id:4,timestamp:1004,track_id:3,acknowledged:false,kind:'prolonged_low',details:{x_m:1,y_m:2,low_duration_s:30,low_h_m:.4,reference_m:1.7}},
 {id:3,timestamp:1003,track_id:2,acknowledged:false,kind:'fall',details:{x_m:1,y_m:1,drop_m:1.4,duration_s:.5,peak_speed_mps:2.7,low_h_m:.3,reference_m:1.7}},
 {id:1,timestamp:1001,track_id:1,acknowledged:false,details:{x_m:0,y_m:1}}   // old API row without kind -> fall
];
const fall={window_seconds:2.5,height_ratio:.6,confirm_seconds:1,cooldown_seconds:30,recovery_ratio:.85,recovery_seconds:2,
 min_drop_m:.6,max_low_height_m:.7,fall_max_duration_s:1.2,fall_min_speed_mps:1,floor_alert_s:30,safe_zones:[[-2,-1,2,4]]};
const context={console,Date,Math,JSON,Number,Array,Object,Error,AbortController,Promise,
 document:{querySelector:get,createElement:()=>new Element(),documentElement:new Element()},
 window:{devicePixelRatio:1},localStorage:{getItem:()=>null,setItem(){},removeItem(){}},
 requestAnimationFrame(){},setTimeout:()=>0,clearTimeout(){},setInterval(){},
 fetch:async url=>{
  let body;
  if(url==='/api/state')body=state;else if(url==='/api/events')body=events;
  else if(url==='/api/filters')body={enabled:false,roi:[-1,1,0,2],exclusions:[]};
  else body={fall,motion_filter_enabled:true};
  return {ok:true,json:async()=>body};
 }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(web,'data.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(web,'app.js'),'utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const texts=e=>[e.textContent,...e.children.map(texts)].flat(Infinity).join(' ');
(async()=>{
 await flush();await flush();
 assert.equal(get('#alertCount').textContent,3,'only fall + prolonged_low (incl. old rows) are counted');
 const rows=get('#events').children;
 assert.equal(rows.length,5);
 assert.equal(rows[0].className,'live-event info');
 assert.equal(rows[0].children.length,1,'info events have no acknowledge button');
 assert(texts(rows[0]).includes('主动躺下 · 已排除（下降 2.1 s，0.6 m/s）'),texts(rows[0]));
 assert(texts(rows[1]).includes('下蹲/弯腰 · 已排除'));
 assert(texts(rows[2]).includes('长时间低位 · 待人工查看'));assert.equal(rows[2].children.length,2);
 assert(texts(rows[3]).includes('疑似跌倒 · 待人工查看'));assert(texts(rows[3]).includes('峰值速度 2.7 m/s'));
 assert(texts(rows[4]).includes('疑似跌倒 · 待人工查看'),'missing kind is shown as fall');
 const rule=get('#fallRule').textContent;
 for(const s of ['下降大','下降快','1.2 秒','安全区','主动躺下','长时间低位','30 秒'])assert(rule.includes(s),s);
 const card=texts(get('#targets'));
 assert(card.includes('低位（未报警）'));assert(card.includes('参考高度 1.72 m'));assert(card.includes('用时 2.1 s'));
 console.log('PASS frontend kinds: info greyed and not counted, kind labels, rule text, low-state target card');
})().catch(e=>{console.error(e);process.exitCode=1});
