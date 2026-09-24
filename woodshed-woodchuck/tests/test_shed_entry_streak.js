const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/shed-entry-streak.js','utf8');
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function boot({storage=new Map(),account='opaque-a',day=1,path='/home',authenticated=true,blocked=false}={}) {
  const listeners={}, classes=new Set(), panel={hidden:true};
  let arrival,verify,clicks=0,current=true,hidden=false,focused=false;
  const control={focus(){focused=true;},click(){clicks++;panel.hidden=false;listeners['woodshed:xp-opened']?.();}};
  const document={body:{dataset:{authenticated:String(authenticated),worldEntryAccount:account},classList:{contains:c=>classes.has(c)}},
    get hidden(){return hidden;},getElementById:id=>id==='xp-panel'?panel:control,querySelector:()=>null,
    addEventListener:(event,fn)=>listeners[event]=fn};
  const window={WWSessionBoundary:{ready:new Promise(r=>verify=r),isCurrent:()=>current},
    WWWorldEntry:{arrivalReady:new Promise(r=>arrival=r)},WWSurfaces:{current:()=>panel.hidden?null:panel},
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>{if(blocked)throw Error('blocked');storage.set(k,v);},removeItem:k=>storage.delete(k)}};
  class LocalDate extends Date {constructor(){super(2026,8,day,12,0,0);}}
  vm.runInNewContext(source,{window,document,navigator:{},location:{pathname:path},Date:LocalDate});
  return {storage,control,panel,classes,verify:()=>verify(true),arrival:()=>arrival(),clicks:()=>clicks,
    focused:()=>focused,retire:()=>{current=false;},show:()=>{hidden=false;listeners.visibilitychange?.();},hide:()=>{hidden=true;},listeners};
}
test('waits for verified session and arrival, then uses L3 and stores only a date',async()=>{
 const h=boot();h.verify();await flush();assert.equal(h.clicks(),0);
 h.arrival();await flush();assert.equal(h.clicks(),1);assert.equal(h.focused(),true);
 assert.deepEqual([...h.storage.values()],['2026-09-01']);
});
test('same account/day does not reopen; another account and next local day do',async()=>{
 const storage=new Map();
 for(const [account,day,expected] of [['a',1,1],['a',1,0],['b',1,1],['a',2,1]]) {
   const h=boot({storage,account,day});h.verify();h.arrival();await flush();assert.equal(h.clicks(),expected);
 }
});
test('SHOP and anonymous pages never open or write a presentation marker',async()=>{
 for(const settings of [{path:'/store'},{authenticated:false}]){
   const h=boot(settings);h.verify();h.arrival();await flush();assert.equal(h.clicks(),0);assert.equal(h.storage.size,0);
 }
});
test('reduced-motion/direct entry with an immediately complete arrival opens without another timer',async()=>{
 const h=boot();h.arrival();h.verify();await flush();assert.equal(h.clicks(),1);
 assert.equal(source.includes('setTimeout'),false);
});
test('hidden and retired sessions cannot show a popup',async()=>{
 const h=boot();h.hide();h.verify();h.arrival();await flush();assert.equal(h.clicks(),0);
 h.show();await flush();assert.equal(h.clicks(),1);
 const stale=boot();stale.retire();stale.verify();stale.arrival();await flush();assert.equal(stale.clicks(),0);
});
test('manual opening remains available after the daily auto popup',async()=>{
 const h=boot();h.verify();h.arrival();await flush();h.panel.hidden=true;h.control.click();assert.equal(h.clicks(),2);
});
test('blocked presentation storage keeps manual L3 usable without repeated automatic popups',async()=>{
 const h=boot({blocked:true});h.verify();h.arrival();await flush();assert.equal(h.clicks(),0);
 h.control.click();assert.equal(h.panel.hidden,false);
});
test('an already open feature or Stickerbook is not displaced',async()=>{
 for(const feature of ['panel','stickerbook']){
  const h=boot();if(feature==='panel')h.panel.hidden=false;else h.classes.add('stickerbook-open');
  h.verify();h.arrival();await flush();assert.equal(h.clicks(),0);assert.equal(h.storage.size,0);
 }
});
