// Real Pantry source in a VM. Native focus/history are verified in browsers.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
function element(tag='div',text='') {
  const el={tagName:tag.toUpperCase(),ownText:text,children:[],style:{},attributes:{},listeners:{},dataset:{},open:false,hidden:false,removed:false,
    get textContent(){return this.ownText+this.children.map(x=>x.textContent).join(' ');},set textContent(v){this.ownText=String(v);this.children=[];},
    append(...xs){this.children.push(...xs);},appendChild(el){this.children.push(el);},remove(){this.removed=true;},replaceChildren(...xs){this.ownText='';this.children=xs;},
    setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,f){(this.listeners[k]??=[]).push(f);},
    event(k){(this.listeners[k]||[]).forEach(f=>f());},click(){this.event('click');},
    showModal(){this.open=true;},close(){this.open=false;this.event('close');},
  };return el;
}
const payload=(targets={logging_days:4,practice_seconds:3600},seconds=1200,days=1)=>({
  context:{week_start:'2026-10-19'},credits:75,targets,progress:{logging_days:days,practice_seconds:seconds},wallet:{pickled_carrot:2,white_clover:1}});
function harness({data=payload(),deferred=false,error=false,enabled=true,reduced=true}={}) {
  const ids=Object.fromEntries(['open','content','status','refresh','close'].map(k=>['weekly-pantry-'+k,element(k==='content'?'div':'button')]));
  const dialog=ids['weekly-pantry']=element('dialog'),events={},requests=[],pending=[],timers=new Map();let current=true,generation=0,timer=0,effects=0;
  const opener=ids['dandelion-object']=element('button');if(enabled)opener.dataset.weeklyPantry='true';
  ids['credits-value']=element('span','75');const body=element('body');
  const setTimeout=(fn,delay)=>{timers.set(++timer,{fn,delay});return timer;},clearTimeout=id=>timers.delete(id);
  const document={body,createElement:tag=>element(tag),getElementById:id=>ids[id],querySelector:()=>body.children.findLast(x=>!x.removed)||null};
  const window={WWSessionBoundary:{isCurrent:()=>current},WWState:{accountRequest:()=>({generation}),stateForResponse:r=>current&&r.generation===generation?{}:null,
    applyEconomy(){throw Error('No wallet writer');},saveState(){throw Error('No state writer');}},
    setTimeout,clearTimeout,matchMedia:()=>({matches:reduced}),dispatchEvent:()=>effects++,
    WWSurfaces:{register(){},current:()=>dialog.open?dialog:null},addEventListener(k,f){(events[k]??=[]).push(f);}};
  const context=vm.createContext({window,document,console,setTimeout,clearTimeout,CustomEvent:class{constructor(type){this.type=type;}},fetch:async(url,options)=>{requests.push({url,options});if(deferred)await new Promise(r=>pending.push(r));if(error)throw Error('Synthetic read failed');return {ok:true,json:async()=>data};}});
  for(const f of ['practice-duration.js','weekly-pantry.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/js',f),'utf8'),context);
  const app=fs.readFileSync(path.join(__dirname,'../static/js/app.js'),'utf8');
  vm.runInContext(app.slice(app.indexOf('  function wireShopDandelionBalance()'),app.indexOf('  function wireShopPolish()'))+'\nwireShopDandelionBalance();',context);
  return {ids,dialog,window,requests,pending,context,timers,opener,body,effects:()=>effects,setError:v=>{error=v;},content:ids['weekly-pantry-content'],status:ids['weekly-pantry-status'],
    async open(){opener.click();await flush();},async close(){ids['weekly-pantry-close'].click();await flush();},
    stale(){current=false;generation++;(events['ww:session-changed']||[]).forEach(f=>f());},
    render(value){window.WWWeeklyPantry.render(ids['weekly-pantry-content'],value);}};
}
function jars(h){return h.content.children.find(x=>x.className==='weekly-pantry-jars').children;}
test('two approved statistics jars and actual signed balance; GET only and no automatic popup',async()=>{
  const h=harness();assert.equal(h.dialog.open,false);assert.equal(h.requests.length,0);await h.open();
  assert.match(h.content.textContent,/Chart-logging days.*1 \/ 4/);assert.match(h.content.textContent,/Practice Minutes.*20m \/ 1h/);
  assert.match(h.content.textContent,/2 Pickled Carrot jars available/);assert.match(h.content.textContent,/statistics, not spendable currency/i);
  assert.equal(jars(h).length,2);assert.deepEqual(JSON.parse(JSON.stringify(h.requests)),[{url:'/weekly/pantry',options:{credentials:'same-origin',cache:'no-store'}}]);
});
test('exact seconds and over-target text remain uncapped; only visual fill caps',()=>{
  const h=harness();h.render(payload(undefined,4501,7));assert.match(h.content.textContent,/7 \/ 4/);assert.match(h.content.textContent,/1h 15m 1s \/ 1h/);
  assert.deepEqual(jars(h).map(x=>x.children[0].style.height),['100%','100%']);
  h.render(payload({logging_days:3,practice_seconds:1800},118));assert.match(h.content.textContent,/1m 58s \/ 30m/);
  assert.equal(jars(h)[1].children[0].style.height,`${118/1800*100}%`);
});
test('absent targets preserve actual progress and honest zero visual fill',()=>{
  const h=harness();for(const t of [null,undefined]){const d=payload();d.targets=t;h.render(d);assert.match(h.content.textContent,/goal not set/);assert.deepEqual(jars(h).map(x=>x.children[0].style.height),['0%','0%']);}
});
test('repeated refresh and dismissal only GET; spent jars remain actual available balance',async()=>{
  const d=payload();d.wallet.pickled_carrot=0;const h=harness({data:d});await h.open();await h.close();await h.open();await h.close();await h.open();
  assert.equal(h.requests.length,3);assert.ok(h.requests.every(r=>!r.options.method));assert.match(h.content.textContent,/0 Pickled Carrot jars available/);
});
test('failed reads offer readable retry without inferring targets or initiating a claim',async()=>{
  const h=harness({error:true});await h.open();assert.match(h.status.textContent,/Synthetic read failed.*tap Dandelions to retry/);assert.equal(h.content.textContent,'');assert.equal(h.requests.length,1);await h.close();h.setError(false);await h.open();assert.match(h.content.textContent,/Practice Minutes/);assert.equal(h.requests.length,2);
});
test('account change rejects delayed data and closes the prior account surface',async()=>{
  const h=harness({deferred:true});await h.open();h.stale();h.pending.shift()();await flush();assert.equal(h.dialog.open,false);assert.equal(h.content.textContent,'');assert.equal(h.status.textContent,'');
});
test('closed/reopened surface rejects the earlier response',async()=>{
  const h=harness({deferred:true});await h.open();await h.close();await h.open();h.pending.shift()();await flush();assert.equal(h.content.textContent,'');h.pending.shift()();await flush();assert.match(h.content.textContent,/Dandelions/);
});
test('returned text cannot become executable markup',()=>{
  const h=harness();const d=payload();d.context.week_start='<img src=x onerror=alert(1)>';h.render(d);assert.match(h.content.textContent,/<img/);assert.equal(h.content.children.some(x=>x.tagName==='IMG'),false);
});


test('one existing SHOP listener dispatches Pantry once; repeat wiring is idempotent',async()=>{
  const h=harness();vm.runInContext('wireShopDandelionBalance();',h.context);
  assert.equal(h.opener.listeners.click.length,1);await h.open();assert.equal(h.requests.length,1);
  assert.equal(h.body.children.length,0);assert.equal(h.effects(),0);
});
test('tapping the open display immediately dismisses and cancels all timers without another action',async()=>{
  const h=harness();await h.open();assert.equal(h.timers.size,2);await h.close();
  assert.equal(h.dialog.open,false);assert.equal(h.timers.size,0);assert.equal(h.requests.length,1);assert.equal(h.effects(),0);
  assert.equal(h.ids['weekly-pantry-close'].attributes['aria-label'],'Pantry. Tap to dismiss.');
});
for(const reduced of [true,false])test(`brief1400/4500ms presentation and reduced-motion=${reduced}`,async()=>{
  const h=harness({reduced});await h.open();
  [...h.timers.values()].find(t=>t.delay===1400).fn();assert.equal(h.content.children[0].hidden,true);assert.equal(h.dialog.open,true);
  [...h.timers.values()].find(t=>t.delay===4500).fn();assert.equal(h.dialog.open,false);assert.equal(h.timers.size,0);
  assert.equal(h.effects(),reduced?0:1);assert.equal(h.requests.length,1);
});
test('an old dismissed timer cannot close a newer opening',async()=>{
  const h=harness();await h.open();const old=[...h.timers.values()].find(t=>t.delay===4500).fn;
  await h.close();await h.open();old();assert.equal(h.dialog.open,true);assert.equal(h.requests.length,2);assert.equal(h.effects(),0);
});
test('gate OFF keeps the exact original Dandelion reaction with no Pantry read',async()=>{
  const h=harness({enabled:false});await h.open();assert.equal(h.opener.listeners.click.length,1);
  assert.equal(h.dialog.open,false);assert.equal(h.requests.length,0);assert.equal(h.body.children.length,1);
  assert.equal(h.body.children[0].textContent,'75');assert.equal(h.effects(),1);
});
test('templates keep SHOP R4 geometry and gate defaults; no separate SHED entry or hold-open action',()=>{
  const root=path.join(__dirname,'..');const read=p=>fs.readFileSync(path.join(root,p),'utf8');
  const store=read('templates/store.html'),home=read('templates/home.html'),partial=read('templates/_weekly_pantry.html');
  assert.match(store,/id="dandelion-object" class="scene-hotspot" data-scene-cell="R4"/);
  assert.match(store,/data-weekly-pantry="true"/);assert.doesNotMatch(home,/weekly.pantry/);
  assert.equal((partial.match(/<button /g)||[]).length,1);assert.doesNotMatch(partial,/Refresh|details|hold/i);
  assert.match(read('app/feature_access.py'),/"weekly_pantry": Feature\(False, "open"\)/);
  assert.doesNotMatch(read('static/css/weekly-pantry.css'),/weekly-pantry-open/);
  assert.doesNotMatch(read('static/js/weekly-pantry.js'),/Verified|applyEconomy|saveState|method:\s*['"]POST/);
});
test('a queued old native close event cannot erase a newer explicit opening',async()=>{
  const h=harness();await h.open();await h.close();await h.open();
  h.dialog.event('close');assert.equal(h.dialog.open,true);assert.match(h.content.textContent,/Practice Minutes/);assert.equal(h.timers.size,2);
});
