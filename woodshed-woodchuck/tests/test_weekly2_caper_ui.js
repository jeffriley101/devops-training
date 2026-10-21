// Real player/controller source in a standalone VM; no browser or live transport.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = name => fs.readFileSync(path.join(__dirname, '../static/js', name), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const flush = async () => {for(let i=0;i<12;i++) await new Promise(resolve=>setImmediate(resolve));};
let uuid = 0;
function element(tag='div', text='') {
  const classes=new Set();
  const el={tagName:tag.toUpperCase(),ownText:text,children:[],attributes:{},dataset:{},style:{},listeners:{},hidden:false,disabled:false,open:false,value:'',
    classList:{add(...xs){xs.forEach(x=>classes.add(x));},remove(...xs){xs.forEach(x=>classes.delete(x));},contains(x){return classes.has(x);}},
    get className(){return [...classes].join(' ');},set className(s){classes.clear();s.split(' ').filter(Boolean).forEach(x=>classes.add(x));},
    get textContent(){return this.ownText+this.children.map(x=>x.textContent).join(' ');},set textContent(s){this.ownText=String(s);this.children=[];},
    append(...xs){xs.forEach(x=>{x.parent=this;this.children.push(x);});},appendChild(x){this.append(x);},
    replaceChildren(...xs){this.ownText='';this.children=[];this.append(...xs);},
    setAttribute(k,v){this.attributes[k]=String(v);},getAttribute(k){return this.attributes[k]??null;},
    addEventListener(k,f){(this.listeners[k]??=[]).push(f);},
    dispatchEvent(e){e.target??=this;(this.listeners[e.type]||[]).forEach(f=>f(e));},
    click(){if(!this.disabled)this.dispatchEvent({type:'click',target:this});},
    focus(){this.document.activeElement=this;},scrollIntoView(){},remove(){this.parent.children=this.parent.children.filter(x=>x!==this);},
    querySelectorAll(selector){const all=[];function walk(p){for(const c of p.children){all.push(c);walk(c);}}walk(this);return all.filter(x=>selector.split(',').some(s=>matches(x,s.trim())));},
    querySelector(s){return this.querySelectorAll(s)[0]||null;},
  };return el;
}
function matches(el,s) {
  if(s.startsWith('#'))return el.id===s.slice(1);
  if(s.startsWith('.'))return el.classList.contains(s.slice(1));
  const match=s.match(/^([a-z]*)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/);
  if(!match)return false;
  if(match[1]&&el.tagName.toLowerCase()!==match[1])return false;
  if(match[2]){const attr=match[2];const v=attr.startsWith('data-')?el.dataset[attr.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]:el.attributes[attr];return v!==undefined&&(match[3]===undefined||v===match[3]);}
  return !!match[1];
}
const foci=['Build daily consistency','Improve tone and control','Learn songs faster','Strengthen improvisation'];
function tasks(){return {
  logging:{progress:2,target:4,qualified:false,claimable:false,claimed:false,claimed_at:null},
  practice_minutes:{progress:2460,target:3600,qualified:false,claimable:false,claimed:false,claimed_at:null},
  homework:{target_seconds:900,qualified:false,claimable:false,claimed:false,claimed_at:null,assignments:[{slot_no:1,instrument:'trumpet',practice_seconds:540,target_seconds:900,qualified:false}]},
  board:{target:2,completed:1,qualified:false,claimable:false,claimed:false,claimed_at:null},
  thirds:{target:7,authoritative_score:6,qualified:false,claimable:false,claimed:false,claimed_at:null,completion_status:'completed_below_target'},
};}
function assignment(key='trumpet',slot=1){return {slot_no:slot,instrument:key,homework:{title:'Frozen Homework',instructions:['Practice the saved exercise.'],suggested_blocks:{count:3,minutes_each:5}}};}
function payload(){const t=tasks();return {
  me:{authenticated:true,profile:{woodchuck_id:'WC-UI',instrument:'Trumpet',game_mode:'Challenge'}},
  weekly:{plan:{id:31,week_start:'2026-10-12',focus:foci[0],issued_game_mode:'Challenge',instruments:[assignment()]},start_context:'server-context',tasks:t},
  claims:{current_plan:{plan_id:31,week_start:'2026-10-12',tasks:t,clue:{claimed:false,claimable:false}},historical_plans:[],next_before_plan_id:null,clue_count:3,clue_progress:3,clover_claimable:false,white_clover_balance:0},
};}
function harness({server=payload(),storage=new Map(),reduced=false,blockStorage=false,blockStorageRead=false,blockStorageWrite=false,blockStorageRemove=false,locksRequest}={}) {
  const body=element('body'), ids={},events={},requests=[],timers=new Map();let timer=0,epoch=0,current=true,postHandler,override;
  body.dataset.accountId='WC-UI';
  const document={body,activeElement:null,createElement(tag){const x=element(tag);x.document=document;return x;},
    getElementById:id=>ids[id]||null,querySelector:s=>body.querySelector(s),querySelectorAll:s=>body.querySelectorAll(s)};body.document=document;
  const add=(id,tag,parent=body,text='')=>{const x=document.createElement(tag);x.id=id;x.textContent=text;ids[id]=x;parent.append(x);return x;};
  add('mum-open-button','button');const panel=add('mum-panel','section');panel.classList.add('hidden');
  const heading=add('mum-title','h2',panel,'Take care of the musician');add('mum-close-button','button',panel);
  const care=add('mum-care-content','div',panel);add('weekly-caper-open','button',care,'Clover Caper');add('mum-message','p',care);
  const water=add('water','button',care,'Water check');water.dataset.mumChoice='water';
  add('mum-snack-chooser','section',care);add('mum-ready-button','button',care);
  const view=add('weekly-caper-view','div',panel);view.hidden=true;
  add('weekly-caper-back','button',view,'Back to Mum');add('weekly-caper-close','button',view,'Close');
  const status=add('weekly-caper-status','p',view),content=add('weekly-caper-content','div',view);
  const window={document,WWSessionBoundary:{isCurrent:()=>current},
    WWState:{accountRequest:()=>({epoch}),stateForResponse:r=>current&&r.epoch===epoch?{}:null,applyEconomy(){throw Error('No economy write expected');},saveState(){throw Error('No state write expected');}},
    WWInstruments:{definitions:[{key:'trumpet',label:'Trumpet'},{key:'flute',label:'Flute'},{key:'tuba',label:'Tuba'},{key:'piano-keyboard',label:'Piano / Keyboard'}]},
    addEventListener(k,f){(events[k]??=[]).push(f);},dispatchEvent(e){(events[e.type]||[]).forEach(f=>f(e));},
    matchMedia:()=>({matches:reduced}),setTimeout(f){timers.set(++timer,f);return timer;},clearTimeout(id){timers.delete(id);}};
  const localStorage={getItem:k=>{if(blockStorage||blockStorageRead)throw Error('Storage unavailable');return storage.get(k)||null;},
    setItem(k,v){if(blockStorage||blockStorageWrite)throw Error('Storage unavailable');storage.set(k,v);},removeItem:k=>{if(blockStorageRemove)throw Error('Storage unavailable');return storage.delete(k);}};
  const lockRejections=[];
  const context=vm.createContext({window,document,console,localStorage,crypto:{randomUUID:()=>`synthetic-request-${++uuid}`},
    navigator:{locks:{request:async(name,fn)=>{try{return await (locksRequest ? locksRequest(name,fn) : fn());}catch(error){lockRejections.push(error.message);throw error;}}}},
    CustomEvent:class{constructor(type){this.type=type;}},getDayIndex:()=>0,launchDigitalRain:()=>{},
    setTimeout:window.setTimeout,clearTimeout:window.clearTimeout,
    fetch:async(url,options)=>{
      const request={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null,keyAtRequest:[...storage.values()][0]};requests.push(request);
      let result=override?await override(request):undefined;
      if(result===undefined){if(request.method==='POST'){if(!postHandler)throw Error('Unexpected POST');result=await postHandler(request);}
        else result={data:url==='/weekly/plan'?server.weekly:url==='/account/me'?server.me:server.claims};}
      if(result instanceof Error)throw result;
      return {ok:result.ok!==false,json:async()=>clone(result.data)};
    }});
  vm.runInContext(source('practice-duration.js'),context);
  const app=source('app.js');vm.runInContext(app.slice(app.indexOf('  function wireMum(state)'),app.indexOf('  function wireMetronome()'))+'\nwireMum({profile:{woodchuckName:"Synthetic"}});',context);
  vm.runInContext(source('weekly-caper.js'),context);
  const click=async(el)=>{assert.ok(el,'control exists');el.click();await flush();};
  return {server,storage,requests,window,document,ids,content,status,panel,heading,care,view,timers,lockRejections,
    button:(text,root=content)=>root.querySelectorAll('button').find(x=>x.textContent===text),
    card:lane=>content.querySelector(`[data-lane="${lane}"]`),click,
    async open(){ids['mum-open-button'].click();await click(ids['weekly-caper-open']);},
    async reopen(){ids['weekly-caper-close'].click();await this.open();},
    onPost:f=>{postHandler=f;},override:f=>{override=f;},
    stale(){epoch++;current=false;window.dispatchEvent({type:'ww:session-changed'});},
  };
}
const posts=h=>h.requests.filter(r=>r.method==='POST');
const qualify=(s,lane)=>Object.assign(s.weekly.tasks[lane],{qualified:true,claimable:true});
const claim=(s,lane)=>Object.assign(s.weekly.tasks[lane],{qualified:true,claimable:false,claimed:true});
const pending=s=>Object.assign(s.claims,{clue_count:7,clue_progress:7,clover_claimable:true});
const receipt={claimed:true,reward:{type:'white_clover',amount:1},white_clover_balance:1,clover_claimable:false};

test('Mum keeps care, Back/Close/Escape restore the original panel and focus; Caper cancels the ready timer',async()=>{
  const h=harness();h.ids['mum-open-button'].click();h.ids['mum-ready-button'].click();assert.equal(h.timers.size,1);
  await h.click(h.ids['weekly-caper-open']);assert.equal(h.timers.size,0);assert.equal(h.care.hidden,true);assert.equal(h.document.activeElement,h.heading);
  await h.click(h.ids['weekly-caper-back']);assert.equal(h.care.hidden,false);assert.equal(h.document.activeElement,h.ids['weekly-caper-open']);
  await h.click(h.ids['water']);assert.match(h.ids['mum-message'].textContent,/steady sips/);
  await h.click(h.ids['weekly-caper-open']);await h.click(h.ids['weekly-caper-close']);assert.ok(h.panel.classList.contains('hidden'));assert.equal(h.document.activeElement,h.ids['mum-open-button']);
  await h.open();h.panel.dispatchEvent({type:'keydown',key:'Escape'});assert.ok(h.panel.classList.contains('hidden'));assert.equal(h.view.hidden,true);
});

test('five incomplete lanes show exact progress, independent Homework, saved instructions and three jar labels',async()=>{
  const h=harness();h.server.weekly.tasks.homework.assignments.push({slot_no:2,instrument:'flute',practice_seconds:420,target_seconds:900});
  h.server.weekly.plan.instruments.push(assignment('flute',2));await h.open();
  assert.equal(h.content.querySelectorAll('.weekly-caper-task').length,5);
  for(const [lane,pattern] of [['logging',/2 \/ 4/],['practice_minutes',/41 \/ 60 min/],['board',/1 \/ 2/],['thirds',/Best: 6.*Goal: 7 in one round/]])assert.match(h.card(lane).textContent,pattern);
  assert.match(h.card('homework').textContent,/Trumpet: 9 \/ 15 min.*Flute: 7 \/ 15 min/);
  assert.match(h.card('homework').textContent,/Frozen Homework/);assert.doesNotMatch(h.card('homework').textContent,/16 \/ 15/);
  assert.equal(h.content.textContent.match(/Reward: 1 Pickled Carrot jar/g).length,3);
  for(const lane of ['board','thirds'])assert.doesNotMatch(h.card(lane).textContent,/reward|Dandelion|jar/i);
  assert.equal(posts(h).length,0);
});

test('server claim flags replace progress directly; claimed cards have no action; numbers never qualify locally',async()=>{
  const h=harness();for(const lane of ['logging','homework','thirds'])qualify(h.server,lane);
  claim(h.server,'board');h.server.weekly.tasks.practice_minutes.progress=9000;await h.open();
  assert.match(h.card('logging').textContent,/CLAIM/);assert.doesNotMatch(h.card('logging').textContent,/2 \/ 4/);
  assert.match(h.card('homework').textContent,/CLAIM/);assert.doesNotMatch(h.card('homework').textContent,/9 \/ 15/);
  assert.doesNotMatch(h.card('thirds').textContent,/Best:|Goal:/);
  assert.match(h.card('board').textContent,/Claimed/);assert.equal(h.card('board').querySelectorAll('button').length,0);
  assert.match(h.card('practice_minutes').textContent,/150 \/ 60 min/);assert.equal(h.card('practice_minutes').querySelectorAll('button').length,0);
});

test('duration display preserves seconds without rounding Homework or minutes to the target',async()=>{
  const h=harness();h.server.weekly.tasks.practice_minutes.progress=3599;h.server.weekly.tasks.homework.assignments[0].practice_seconds=899;await h.open();
  assert.match(h.card('practice_minutes').textContent,/59m 59s \/ 1h/);assert.match(h.card('homework').textContent,/14m 59s \/ 15m/);assert.equal(posts(h).length,0);
});

test('no plan is read-only until Focus confirmation; issuance sends only focus and canonical mode is read-only',async()=>{
  const h=harness();h.server.weekly={plan:null,progress:{}};h.server.claims.current_plan=null;await h.open();
  assert.equal(posts(h).length,0);assert.match(h.content.textContent,/Current Game Mode: Challenge/);
  const select=h.content.querySelector('select');assert.deepEqual(select.children.slice(1).map(x=>x.value),foci);
  const start=h.button('Start This Week');assert.equal(start.disabled,true);
  select.value=foci[2];select.dispatchEvent({type:'change'});assert.equal(start.disabled,false);
  h.onPost(r=>{assert.equal(r.url,'/weekly/plan');assert.deepEqual(r.body,{focus:foci[2]});const saved=payload();saved.weekly.plan.focus=foci[2];Object.assign(h.server,saved);return {data:saved.weekly};});
  await h.click(start);assert.match(h.content.textContent,/Learn songs faster/);assert.equal(posts(h).length,1);
});

test('NULL canonical mode blocks issuance without another Game Mode selector',async()=>{
  const h=harness();h.server.weekly={plan:null,progress:{}};h.server.claims.current_plan=null;h.server.me.profile.game_mode=null;await h.open();
  const select=h.content.querySelector('select');select.value=foci[0];select.dispatchEvent({type:'change'});
  assert.equal(h.button('Start This Week').disabled,true);assert.equal(h.content.querySelectorAll('select').length,1);assert.match(h.content.textContent,/SHED Game Mode control/);assert.equal(posts(h).length,0);
});

test('explicit instrument start echoes only server context, saved assignment reappears, and canonical aliases match',async()=>{
  const h=harness();h.server.weekly.plan.instruments=[];h.server.weekly.tasks.homework.assignments=[];await h.open();
  assert.equal(posts(h).length,0);assert.equal(h.content.querySelectorAll('.weekly-caper-task').length,5);
  h.onPost(r=>{assert.equal(r.url,'/weekly/plan/instruments/current/start');assert.deepEqual(r.body,{start_context:'server-context'});h.server.weekly.plan.instruments=[assignment()];return {data:{assignment:assignment()}};});
  await h.click(h.button('Start this week’s work'));assert.match(h.content.textContent,/Your saved Homework is below/);
  h.server.me.profile.instrument='Keyboard';h.server.weekly.plan.instruments=[assignment('piano-keyboard')];await h.reopen();assert.equal(h.button('Start this week’s work'),undefined);
});

test('two occupied slots preserve exploration and missing context cannot offer a start',async()=>{
  const h=harness();h.server.weekly.plan.instruments.push(assignment('flute',2));h.server.me.profile.instrument='Tuba';await h.open();
  assert.equal(h.button('Start this week’s work'),undefined);assert.match(h.content.textContent,/still explore and practice/);
  h.server.weekly.plan.instruments=[];h.server.weekly.start_context=null;await h.reopen();assert.equal(h.button('Start this week’s work'),undefined);assert.match(h.content.textContent,/supported instrument/);assert.equal(posts(h).length,0);
});

test('a changed active instrument is refreshed before any start POST; server stale-context rejection is shown',async()=>{
  const h=harness();h.server.weekly.plan.instruments=[];await h.open();h.server.me.profile.instrument='Flute';
  await h.click(h.button('Start this week’s work'));assert.equal(posts(h).length,0);assert.match(h.status.textContent,/instrument changed/);assert.match(h.content.textContent,/Current instrument: Flute/);
  h.onPost(()=>({ok:false,data:{detail:'The active instrument or weekly plan changed.'}}));await h.click(h.button('Start this week’s work'));assert.match(h.status.textContent,/weekly plan changed/);assert.equal(posts(h).length,1);
});

test('task claim sends empty body, disables double tap and waits for durable reload rather than optimistic success',async()=>{
  const h=harness();qualify(h.server,'logging');await h.open();let release;
  h.onPost(()=>new Promise(resolve=>{release=()=>{claim(h.server,'logging');resolve({data:{claimed:true}});};}));
  const b=h.button('CLAIM',h.card('logging'));b.click();await flush();assert.equal(b.disabled,true);b.click();
  assert.equal(posts(h).length,1);assert.deepEqual(posts(h)[0].body,{});assert.equal(posts(h)[0].url,'/weekly/plans/31/tasks/logging/claim');assert.doesNotMatch(h.card('logging').textContent,/Claimed/);
  release();await flush();assert.match(h.card('logging').textContent,/Claimed/);assert.match(h.status.textContent,/1 Pickled Carrot jar/);assert.ok(h.requests.filter(r=>r.url==='/weekly/claims').length>=2);
});

test('uncertain task response rechecks durable state; unsuccessful reload exposes only a read retry',async()=>{
  const h=harness();qualify(h.server,'board');await h.open();h.onPost(()=>{claim(h.server,'board');throw Error('Response lost');});
  await h.click(h.button('CLAIM',h.card('board')));assert.match(h.card('board').textContent,/Claimed/);assert.equal(posts(h).length,1);
  qualify(h.server,'thirds');await h.reopen();let failed=false;
  h.onPost(()=>{failed=true;throw Error('Offline');});h.override(r=>{if(failed&&r.method==='GET')throw Error('Offline');});
  await h.click(h.button('CLAIM',h.card('thirds')));assert.ok(h.button('Refresh Clover Caper'));assert.equal(h.button('CLAIM'),undefined);assert.equal(posts(h).length,2);
});

test('five durable claims unlock CLAIM CLUE; clue below seven reloads progress without any Clover POST',async()=>{
  const h=harness();for(const lane of Object.keys(h.server.weekly.tasks))claim(h.server,lane);h.server.claims.current_plan.clue.claimable=true;await h.open();
  h.onPost(r=>{assert.equal(r.url,'/weekly/plans/31/clue/claim');assert.deepEqual(r.body,{});h.server.claims.current_plan.clue={claimed:true,claimable:false};h.server.claims.clue_progress=4;return {data:{clue:{claimed:true}}};});
  await h.click(h.button('CLAIM CLUE'));assert.match(h.content.textContent,/4 \/ 7 clues/);assert.equal(h.button('CLAIM CLOVER'),undefined);assert.equal(posts(h).length,1);
});

test('seventh clue requires separate explicit Clover click; persisted key precedes POST and celebration continues to SHED',async()=>{
  const h=harness();h.server.claims.current_plan.clue.claimable=true;await h.open();
  h.onPost(r=>{if(r.url.endsWith('/clue/claim')){pending(h.server);h.server.claims.current_plan.clue={claimed:true,claimable:false};return {data:{}};}
    assert.equal(r.url,'/weekly/clover/claim');assert.deepEqual(Object.keys(r.body),['request_key']);assert.equal(r.keyAtRequest,r.body.request_key);h.server.claims.clover_claimable=false;return {data:receipt};});
  await h.click(h.button('CLAIM CLUE'));assert.match(h.content.textContent,/7 \/ 7 clues/);assert.equal(posts(h).length,1);
  await h.click(h.button('CLAIM CLOVER'));assert.match(h.content.textContent,/Four-Leaf White Clover earned/);assert.equal(posts(h).length,2);assert.equal(h.storage.size,1);
  const clover=h.content.querySelector('[aria-label="Continue to SHED"]');assert.equal(clover.tagName,'BUTTON');assert.equal(h.document.activeElement,clover);
  await h.click(clover);assert.equal(h.storage.size,0);assert.ok(h.panel.classList.contains('hidden'));assert.equal(h.document.activeElement,h.ids['mum-open-button']);
});

test('uncertain Clover response and same-browser restart replay the saved key even when server no longer has a pending threshold',async()=>{
  const server=payload(),storage=new Map();pending(server);const h=harness({server,storage});await h.open();let firstKey;
  h.onPost(r=>{firstKey=r.body.request_key;server.claims.clover_claimable=false;server.claims.white_clover_balance=1;throw Error('Response lost after commit');});
  await h.click(h.button('CLAIM CLOVER'));assert.equal(storage.size,1);assert.ok(h.button('Resume Clover claim'));assert.equal(posts(h).length,1);
  h.ids['weekly-caper-close'].click();const resumed=harness({server,storage});resumed.onPost(r=>{assert.equal(r.body.request_key,firstKey);return {data:receipt};});await resumed.open();
  assert.equal(posts(resumed).length,1);assert.match(resumed.content.textContent,/Four-Leaf White Clover earned/);assert.equal(storage.size,1);
  await resumed.click(resumed.content.querySelector('[aria-label="Continue to SHED"]'));assert.equal(storage.size,0);
});

test('closing after confirmed Clover but before dismissal recovers the same result, not the next threshold',async()=>{
  const h=harness();pending(h.server);h.server.claims.clue_count=14;await h.open();h.onPost(()=>({data:{...receipt,clover_claimable:true}}));
  await h.click(h.button('CLAIM CLOVER'));const first=posts(h)[0].body.request_key;await h.reopen();
  assert.deepEqual(posts(h).map(r=>r.body.request_key),[first,first]);assert.match(h.content.textContent,/earned/);
  await h.click(h.content.querySelector('[aria-label="Continue to SHED"]'));await h.open();assert.equal(posts(h).length,2);
  await h.click(h.button('CLAIM CLOVER'));assert.notEqual(posts(h)[2].body.request_key,first);
});

test('blocked storage prevents Clover POST; another browser without a retained key shows balance without celebration',async()=>{
  const h=harness({blockStorage:true});pending(h.server);await h.open();assert.equal(h.button('CLAIM CLOVER'),undefined);assert.equal(posts(h).length,0);assert.match(h.status.textContent,/storage is unavailable/);
  const second=harness();second.server.claims.white_clover_balance=1;await second.open();assert.match(second.content.textContent,/1 Four-Leaf White Clovers/);assert.doesNotMatch(second.content.textContent,/earned/);assert.equal(posts(second).length,0);
});

test('an unreadable retained key cannot be replaced even when local storage writes still work',async()=>{
  const storage=new Map([['woodshed:clover-caper:request:WC-UI','prior-explicit-claim']]);
  const h=harness({storage,blockStorageRead:true});pending(h.server);await h.open();assert.equal(h.button('CLAIM CLOVER'),undefined);
  assert.equal(posts(h).length,0);assert.equal(storage.get('woodshed:clover-caper:request:WC-UI'),'prior-explicit-claim');
  assert.match(h.status.textContent,/has not been replaced/);
});

test('earlier weeks retain source plan IDs and explicit bounded pagination; task reload preserves opened week',async()=>{
  const h=harness();const old={plan_id:12,week_start:'2026-09-28',tasks:tasks(),clue:{claimed:false,claimable:false}};old.tasks.board.claimable=true;
  h.server.claims.historical_plans=[old];h.server.claims.next_before_plan_id=12;await h.open();
  const detail=h.content.querySelector('[data-source-plan="12"]');detail.open=true;
  h.onPost(r=>{assert.equal(r.url,'/weekly/plans/12/tasks/board/claim');assert.deepEqual(r.body,{});old.tasks.board.claimed=true;old.tasks.board.claimable=false;return {data:{claimed:true}};});
  await h.click(h.button('CLAIM',detail));assert.equal(h.content.querySelector('[data-source-plan="12"]').open,true);assert.equal(posts(h).length,1);
  h.override(r=>r.url==='/weekly/claims?before_plan_id=12'?{data:{historical_plans:[{...clone(old),plan_id:8,week_start:'2026-09-21'}],next_before_plan_id:null}}:undefined);
  await h.click(h.button('Older weeks'));assert.ok(h.content.querySelector('[data-source-plan="8"]'));assert.equal(h.button('Older weeks'),undefined);assert.equal(posts(h).length,1);
});

test('late response from a closed opening cannot paint a new view or historical data',async()=>{
  const h=harness();let release,first=true;h.override(r=>{if(first&&r.url==='/weekly/plan'){first=false;const old=clone(h.server.weekly);old.plan.focus='STALE VIEW';return new Promise(resolve=>{release=()=>resolve({data:old});});}});
  h.ids['mum-open-button'].click();h.ids['weekly-caper-open'].click();await flush();h.ids['weekly-caper-close'].click();await h.open();
  release();await flush();assert.doesNotMatch(h.content.textContent,/STALE VIEW/);assert.equal(posts(h).length,0);
});

test('account-generation changes discard in-flight data and actions',async()=>{
  const h=harness();let release;h.override(r=>r.url==='/weekly/plan'?new Promise(resolve=>{release=()=>resolve({data:h.server.weekly});}):undefined);
  h.ids['mum-open-button'].click();h.ids['weekly-caper-open'].click();await flush();h.stale();release();await flush();assert.equal(h.content.textContent,'');assert.equal(posts(h).length,0);
});

test('mismatched rollover responses require GET refresh before any old-plan action',async()=>{
  const h=harness();h.server.claims.current_plan.plan_id=99;await h.open();assert.match(h.status.textContent,/week changed/);assert.ok(h.button('Refresh Clover Caper'));assert.equal(h.button('CLAIM'),undefined);assert.equal(posts(h).length,0);
});

test('reduced motion keeps static celebration and explicit artwork fallback without confetti',async()=>{
  const h=harness({reduced:true});pending(h.server);let confetti=0;h.window.addEventListener('woodshed:celebrate',()=>confetti++);h.onPost(()=>({data:receipt}));await h.open();await h.click(h.button('CLAIM CLOVER'));
  assert.equal(confetti,0);assert.equal(h.content.dataset.reducedMotion,'true');assert.match(h.content.textContent,/artwork pending approval/);
  const img=h.content.querySelector('img');assert.equal(img.src,'/static/img/weekly/four-leaf-white-clover.png');assert.equal(img.width,1024);img.dispatchEvent({type:'error'});assert.equal(img.hidden,true);
  img.dispatchEvent({type:'load'});assert.equal(img.hidden,false);assert.equal(h.content.querySelector('.weekly-caper-art-placeholder').hidden,true);
});

test('shared renderer/controller can mount in a future container without an automatic popup or claim',async()=>{
  const h=harness();assert.equal(posts(h).length,0);assert.equal(h.requests.length,0);
  assert.equal(typeof h.window.WWWeeklyCaper.renderTasks,'function');assert.equal(typeof h.window.WWWeeklyCaper.create,'function');
  const other=h.document.createElement('div'),state=h.document.createElement('p');let continued=0;
  const controller=h.window.WWWeeklyCaper.create({container:other,status:state,heading:h.heading,onContinue:()=>continued++});await controller.open();
  assert.equal(other.querySelectorAll('.weekly-caper-task').length,5);assert.equal(posts(h).length,0);controller.close();assert.equal(other.textContent,'');assert.equal(continued,0);
});

test('late Earlier Weeks pagination cannot append to a later opening',async()=>{
  const h=harness();h.server.claims.next_before_plan_id=12;await h.open();let release;
  h.override(r=>r.url==='/weekly/claims?before_plan_id=12'?new Promise(resolve=>{release=()=>resolve({data:{historical_plans:[{plan_id:8,week_start:'STALE HISTORY',tasks:tasks(),clue:{claimed:false,claimable:false}}],next_before_plan_id:null}});}):undefined);
  h.button('Older weeks').click();await flush();h.server.claims.next_before_plan_id=null;await h.reopen();release();await flush();
  assert.doesNotMatch(h.content.textContent,/STALE HISTORY/);assert.equal(h.content.querySelectorAll('.weekly-caper-task').length,5);
});

test('a delayed Clover grant cannot reopen a closed panel; confirmed recovery still needs explicit dismissal',async()=>{
  const h=harness();pending(h.server);await h.open();let release;
  h.onPost(()=>new Promise(resolve=>{release=()=>resolve({data:receipt});}));h.button('CLAIM CLOVER').click();await flush();
  h.ids['weekly-caper-close'].click();release();await flush();assert.equal(h.content.textContent,'');assert.equal(h.storage.size,1);
  h.onPost(()=>({data:receipt}));await h.open();assert.match(h.content.textContent,/earned/);assert.equal(posts(h)[0].body.request_key,posts(h)[1].body.request_key);
});

test('ordinary motion celebrates only a server-confirmed Clover and preserves the clue artwork slot',async()=>{
  const h=harness();pending(h.server);let effects=0;h.window.addEventListener('woodshed:celebrate',()=>effects++);await h.open();
  const clue=h.content.querySelector('img');assert.equal(clue.src,'/static/img/weekly/clue-token.png');assert.equal(clue.width,512);
  assert.equal(effects,0);h.onPost(()=>({data:receipt}));await h.click(h.button('CLAIM CLOVER'));assert.equal(effects,1);
});


test('newer payload revisions never turn Caper into an account state or wallet writer',async()=>{
  const h=harness();h.server.weekly.state_revision=999;h.server.claims.state_revision=1000;await h.open();
  assert.equal(h.content.querySelectorAll('.weekly-caper-task').length,5);
  assert.equal(posts(h).length,0);
});

test('failed recovery-key removal keeps confirmation and cannot expose the next threshold',async()=>{
  const h=harness();pending(h.server);h.onPost(()=>({data:receipt}));await h.open();await h.click(h.button('CLAIM CLOVER'));
  const key=[...h.storage.keys()][0];h.storage.delete=()=>{};
  await h.click(h.content.querySelector('[aria-label="Continue to SHED"]'));
  assert.match(h.status.textContent,/could not be cleared/);assert.equal(h.storage.size,1);
  assert.equal(h.view.hidden,false);assert.equal(posts(h).length,1);assert.equal(h.button('CLAIM CLOVER'),undefined);
});

test('returned instructions and errors use text rather than executable markup',async()=>{
  const h=harness();h.server.weekly.plan.instruments[0].homework.instructions=['<img src=x onerror=alert(1)>'];await h.open();
  assert.match(h.card('homework').textContent,/<img src=x/);assert.equal(h.card('homework').querySelectorAll('img').length,0);
});


test('denied recovery write is handled inside the native lock callback with no POST or rejected callback',async()=>{
  const h=harness({blockStorageWrite:true});pending(h.server);await h.open();
  await h.click(h.button('CLAIM CLOVER'));
  assert.equal(posts(h).length,0);assert.equal(h.storage.size,0);
  assert.match(h.status.textContent,/Storage unavailable.*Checking saved state/);
  assert.deepEqual(h.lockRejections,[]);
});

test('a retained key is reused without a replacement write when new recovery writes are denied',async()=>{
  const storage=new Map([['woodshed:clover-caper:request:WC-UI','prior-explicit-action']]);
  const h=harness({storage,blockStorageWrite:true});pending(h.server);
  h.onPost(r=>{assert.equal(r.body.request_key,'prior-explicit-action');return {data:receipt};});
  await h.open();assert.equal(posts(h).length,1);assert.equal(storage.size,1);
  assert.equal(storage.get('woodshed:clover-caper:request:WC-UI'),'prior-explicit-action');
  assert.match(h.content.textContent,/Four-Leaf White Clover earned/);assert.deepEqual(h.lockRejections,[]);
});

// Deterministic callback scheduling models a tab waiting to enter the key lock.
// Native lock ordering and real receipts are verified separately in browsers.
function deferredLock() {
  const queued=[];
  return {queued,request:(name,fn)=>new Promise(resolve=>queued.push({name,fn,resolve})),
    async release(){assert.equal(queued.length,1);const item=queued.shift();item.resolve(await item.fn());await flush();}};
}
const pendingStorageKey='woodshed:clover-caper:request:WC-UI';
async function recoveryRace() {
  const storage=new Map(),server=payload();pending(server);server.claims.clue_count=14;
  const waiting=deferredLock(),a=harness({storage,server}),b=harness({storage,server,locksRequest:waiting.request});
  let confirm;const grants=new Map();
  a.onPost(r=>new Promise(resolve=>{grants.set(r.body.request_key,1);confirm=()=>resolve({data:{...receipt,clover_claimable:true}});}));
  b.onPost(r=>{assert.ok(grants.has(r.body.request_key),'recovery must use an existing action');return {data:{...receipt,clover_claimable:true}};});
  await a.open();a.button('CLAIM CLOVER').click();await flush();const original=storage.get(pendingStorageKey);
  await b.open();assert.equal(waiting.queued.length,1);assert.equal(posts(b).length,0);
  confirm();await flush();
  return {a,b,storage,server,waiting,original,grants};
}
test('queued recovery binds the unchanged original action; no new controller grant',async()=>{
  const {a,b,waiting,original,grants}=await recoveryRace();await waiting.release();
  assert.equal(posts(a).length,1);assert.equal(posts(b).length,1);assert.equal(posts(b)[0].body.request_key,original);
  assert.equal(grants.size,1);assert.deepEqual(b.lockRejections,[]);
});
test('confirmed Continue clears the key before queued recovery: no new key or POST',async()=>{
  const {a,b,storage,waiting}=await recoveryRace();await a.click(a.content.querySelector('[aria-label="Continue to SHED"]'));
  assert.equal(storage.size,0);await waiting.release();assert.equal(posts(b).length,0);assert.equal(storage.size,0);
  assert.match(b.status.textContent,/request changed/);assert.ok(b.button('CLAIM CLOVER'));assert.deepEqual(b.lockRejections,[]);
});
test('queued recovery cannot adopt another explicit action created after Continue',async()=>{
  const {a,b,storage,waiting,original}=await recoveryRace();await a.click(a.content.querySelector('[aria-label="Continue to SHED"]'));
  a.onPost(()=>({data:{...receipt,white_clover_balance:2}}));await a.open();await a.click(a.button('CLAIM CLOVER'));
  const second=storage.get(pendingStorageKey);assert.notEqual(second,original);await waiting.release();
  assert.equal(posts(b).length,0);assert.equal(storage.get(pendingStorageKey),second);assert.match(b.status.textContent,/request changed/);
  assert.ok(b.button('Resume Clover claim'));
});
test('Resume is bound to its displayed key and cannot become a fresh claim',async()=>{
  let hold=false;const waiting=deferredLock();const h=harness({locksRequest:(name,fn)=>hold?waiting.request(name,fn):fn()});
  pending(h.server);h.onPost(()=>{throw Error('Uncertain response');});await h.open();await h.click(h.button('CLAIM CLOVER'));
  hold=true;await h.click(h.button('Resume Clover claim'));assert.equal(waiting.queued.length,1);
  h.storage.delete(pendingStorageKey);await waiting.release();assert.equal(posts(h).length,1);assert.equal(h.storage.size,0);
  assert.match(h.status.textContent,/request changed/);
});
test('an explicit click queued before another pending action appears does not replace or adopt it',async()=>{
  const waiting=deferredLock(),h=harness({locksRequest:waiting.request});pending(h.server);await h.open();await h.click(h.button('CLAIM CLOVER'));
  h.storage.set(pendingStorageKey,'another-explicit-action');await waiting.release();
  assert.equal(posts(h).length,0);assert.equal(h.storage.get(pendingStorageKey),'another-explicit-action');assert.match(h.status.textContent,/request changed/);
});
for(const change of ['different-key','account','panel'])test(`delayed Continue preserves pending storage after ${change} changes`,async()=>{
  let hold=false;const waiting=deferredLock();const h=harness({locksRequest:(name,fn)=>hold?waiting.request(name,fn):fn()});
  pending(h.server);h.onPost(()=>({data:receipt}));await h.open();await h.click(h.button('CLAIM CLOVER'));
  hold=true;await h.click(h.content.querySelector('[aria-label="Continue to SHED"]'));assert.equal(waiting.queued.length,1);
  if(change==='different-key')h.storage.set(pendingStorageKey,'newer-explicit-action');
  else if(change==='account')h.stale();else h.ids['weekly-caper-close'].click();
  const expected=h.storage.get(pendingStorageKey);await waiting.release();assert.equal(h.storage.get(pendingStorageKey),expected);
  assert.equal(posts(h).length,1);assert.deepEqual(h.lockRejections,[]);
  if(change==='different-key')assert.match(h.status.textContent,/request changed/);
});
test('throwing removal stays inside lock callback and retains saved confirmation/key',async()=>{
  const h=harness({blockStorageRemove:true});pending(h.server);h.onPost(()=>({data:receipt}));await h.open();await h.click(h.button('CLAIM CLOVER'));
  const original=h.storage.get(pendingStorageKey);await h.click(h.content.querySelector('[aria-label="Continue to SHED"]'));
  assert.equal(h.storage.get(pendingStorageKey),original);assert.match(h.status.textContent,/Clover is saved.*Storage unavailable/);
  assert.equal(posts(h).length,1);assert.deepEqual(h.lockRejections,[]);assert.equal(h.view.hidden,false);
});
for(const change of ['account','panel'])test(`queued recovery invalidated by ${change} cannot POST or replace its key`,async()=>{
  const {b,storage,waiting,original}=await recoveryRace();
  if(change==='account')b.stale();else b.ids['weekly-caper-close'].click();
  await waiting.release();assert.equal(posts(b).length,0);assert.equal(storage.get(pendingStorageKey),original);
  assert.equal(b.content.textContent,'');
});
