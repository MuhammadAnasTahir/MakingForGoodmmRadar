const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const web=path.join(__dirname,'../web');
class Element{
 constructor(){this.children=[];this.value='';this.textContent='';this.listeners={};this.classList={toggle(){}};this.style={setProperty(){}};this.width=0;this.height=0;}
 append(...xs){this.children.push(...xs)}
 replaceChildren(...xs){this.children=xs}
 addEventListener(name,fn){this.listeners[name]=fn}
 setAttribute(name,value){this[name]=value}
 getBoundingClientRect(){return {width:700,height:400}}
 getContext(){return new Proxy({}, {get:()=>()=>{},set:()=>true})}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
let online=true,ack=false;const writes=[];
const data={status:'streaming',frame_number:10,last_frame_at:Date.now()/1000,frame_age_seconds:.02,tracks:[{track_id:7,x_m:.2,y_m:1,z_m:.1,vx_mps:0,vy_mps:0,vz_mps:0,height_max_m:1.6,fall_state:'tracking',motion_state:'motion_confirmed'}],points:[[0,1,0,0]],point_count:1};
const context={console,Date,Math,JSON,Number,Array,Object,Error,AbortController,Promise,URLSearchParams,location:{search:''},
 document:{querySelector:get,querySelectorAll:()=>[],createElement:()=>new Element(),documentElement:new Element()},
 window:{devicePixelRatio:1},localStorage:{getItem:()=>null,setItem(){},removeItem(){}},
 requestAnimationFrame(){},setTimeout:()=>0,clearTimeout(){},setInterval(){},
 fetch:async(url,opt={})=>{
  if(!online&&url==='/api/state')throw Error('offline');
  if(opt.method==='POST')writes.push({url,...opt});
  let body;if(url==='/api/state')body=data;
  else if(url==='/api/events')body=[{id:1,timestamp:1000,track_id:7,acknowledged:ack,details:{x_m:.1,y_m:1}}];
  else if(url.endsWith('/ack')){ack=true;body={acknowledged:true}}
  else if(url==='/api/filters')body={enabled:false,roi:[-1,1,0,2],exclusions:[]};
  else body={fall:{window_seconds:2.5,height_ratio:.6,confirm_seconds:1,cooldown_seconds:30},motion_filter_enabled:true};
  return {ok:true,json:async()=>body};
 }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(web,'data.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(web,'i18n.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(web,'app.js'),'utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await flush();await flush();
 assert.equal(get('#targetCount').textContent,1);assert.equal(get('#alertCount').textContent,1);
 const texts=e=>[e.textContent,...e.children.map(texts)].flat(Infinity).join(' ');
 assert(texts(get('#targets')).includes('心率：未接入'));
 assert(!texts(get('#targets')).includes('ECG'));
 await get('#events').children[0].children[1].listeners.click();
 assert(ack);assert.equal(get('#alertCount').textContent,0);
 await get('#filterForm').listeners.submit({preventDefault(){}});
 assert(writes.some(w=>w.url==='/api/filters'&&w.headers['X-Radar-Action']==='configure'));
 get('#roomName').value='Changed room';get('#accent').value='#123456';get('#background').value='#ffffff';get('#scene').value='bedroom';
 get('#appearanceForm').listeners.submit({preventDefault(){}});
 assert.equal(get('#title').textContent,'Changed room');
 get('#cloudTab').listeners.click();assert.equal(get('#roomScene').hidden,true);
 online=false;await context.pollState();assert.equal(get('#targetCount').textContent,0);assert(get('#connection').textContent.includes('不可达'));
 console.log('PASS frontend: real targets, no fake vitals, ack, filter save, appearance, cloud tab, offline clearing');
})().catch(e=>{console.error(e);process.exitCode=1});
