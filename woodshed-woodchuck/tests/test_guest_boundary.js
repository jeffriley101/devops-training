const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, '../static/js', name), 'utf8');
const pending = () => {let resolve; const promise = new Promise(r => resolve=r); return {promise,resolve};};
const response = (body, status=200) => new Response(JSON.stringify(body), {status, headers:{'Content-Type':'application/json'}});

function storage(map = new Map()) {
  return new Proxy({getItem:k => map.get(k) ?? null, setItem:(k,v)=>map.set(k,String(v)), removeItem:k=>map.delete(k)}, {
    ownKeys:()=>[...map.keys()], getOwnPropertyDescriptor:()=>({enumerable:true,configurable:true}),
  });
}
function element() {
  return {listeners:{}, dataset:{}, value:'', hidden:false, disabled:false, textContent:'', children:[],
    addEventListener(name, fn) {this.listeners[name]=fn;},
    setAttribute(){}, appendChild(item){this.children.push(item);},
    classList:{add(){},remove(){},toggle(){}}, focus(){}, scrollIntoView(){},
  };
}
function browser({shared = new Map(), account='WC-A', guest='transition', generation=null, network=async()=>response({})}={}) {
  const ids = new Map(); const events={}; const shell=element(); const body=element();
  body.dataset={accountId:account,guest,pageGeneration:generation};
  const loadedScripts=[];
  if (generation !== null) {
    shell.hidden=true;
    ids.set('account-scripts', {content:{querySelectorAll:()=>[
      {attributes:[{name:'src',value:'/static/js/state.js'}]},
      {attributes:[{name:'src',value:'/static/js/app.js'}]},
    ]}});
    body.appendChild=item=>{body.children.push(item);if(item.onload){loadedScripts.push(item);item.onload();}};
  }
  const session = new Map(); const requests=[]; const alerts=[];
  const window={localStorage:storage(shared),sessionStorage:storage(session),
    fetch:async (url,options={})=>{requests.push({url,options});return network(url,options);},
    addEventListener:(name,fn)=>{(events[name] ||= []).push(fn);},
    dispatchEvent:event=>{for(const fn of events[event.type] || [])fn(event);},
    location:{href:'http://localhost/guest',origin:'http://localhost',pathname:'/guest',assign(url){this.assigned=url;}},
    alert:m=>alerts.push(m),setTimeout(){},clearTimeout(){},
  };
  const context={window,document:{body,getElementById:id=>ids.get(id)||null,querySelector:selector=>selector==='.app-shell'?shell:null,querySelectorAll:()=>[],createElement:element},
    navigator:{locks:{request:async (_name,options,fn)=>(fn || options)()}},URL,Headers,Response,console,
    CustomEvent:class {constructor(type,init={}){this.type=type;this.detail=init.detail;}},
  };
  context.fetch=(...args)=>window.fetch(...args);
  vm.createContext(context);
  vm.runInContext(source('session-boundary.js'),context);
  return {context,window,ids,body,shell,shared,session,requests,alerts,loadedScripts,load:name=>vm.runInContext(source(name),context)};
}

test('Guest setup, autosave, reload and discard use only the Guest namespace', async()=>{
  const shared=new Map([['woodshedWoodchuckState.v1','account-saved'],['woodshedWoodchuckMetronomeBpm','88'],['woodshedWoodchuckConflictBackup.keep','saved']]);
  function setup() {
    const b=browser({shared,account:'',guest:'local'});
    const form=element();form.elements={};
    for(const [key,value] of [['instrument','Flute'],['level','Beginner'],['goal','Daily']]) {
      form.elements[key]={value:'',options:[{value:''},{value}]};
    }
    form.reset=()=>Object.values(form.elements).forEach(e=>e.value='');
    form.reportValidity=()=>true;
    b.ids.set('guest-setup-form',form);b.ids.set('guest-feedback',element());b.ids.set('guest-tools',element());b.ids.set('guest-discard',element());
    b.load('guest.js'); return {...b,form};
  }
  const b=setup();
  for(const f of Object.values(b.form.elements))f.value=f.options[1].value;
  b.form.listeners.submit({preventDefault(){}});
  assert.equal(b.ids.get('guest-tools').hidden,false);
  assert.deepEqual(JSON.parse(shared.get('woodshed:guest:v1:preferences')),{instrument:'Flute',level:'Beginner',goal:'Daily'});
  // Arbitrary Guest history is never read by account state or the Guest preference projection.
  shared.set('woodshed:guest:v1:untrusted-history',JSON.stringify({credits:99999,practiceLog:['guest']}));
  const reloaded=setup();assert.equal(reloaded.form.elements.instrument.value,'Flute');
  assert.equal(reloaded.ids.get('guest-tools').hidden,false);
  reloaded.session.set('woodshed:guest:v1:draft','guest');reloaded.session.set('woodshed:p-book:verifier-draft:v1','account draft');
  reloaded.ids.get('guest-discard').listeners.click();
  assert.equal([...shared.keys()].some(k=>k.startsWith('woodshed:guest:')),false);
  assert.equal(reloaded.session.has('woodshed:guest:v1:draft'),false);
  assert.equal(reloaded.session.get('woodshed:p-book:verifier-draft:v1'),'account draft');
  assert.equal(shared.get('woodshedWoodchuckState.v1'),'account-saved');
  assert.equal(shared.get('woodshedWoodchuckMetronomeBpm'),'88');
  assert.equal(shared.get('woodshedWoodchuckConflictBackup.keep'),'saved');
  assert.equal(b.requests.length+reloaded.requests.length,0);
  await assert.rejects(reloaded.window.fetch('/practice-charts',{method:'POST'}),/local/);
  assert.equal(reloaded.requests.length,0);
});

test('late network and state consumers in an old tab cannot cross logout and same-account login',async()=>{
  const shared=new Map();const late=pending();
  const old=browser({shared,network:()=>late.promise});old.load('state.js');
  const api=old.window.WWState; const a=api.getState();a.account={woodchuckId:'WC-A',authenticated:true,serverRevision:8};a.progress.credits=37;api.saveState(a,{sync:false});
  const ticket=api.accountRequest();
  const request=old.window.fetch('/account/daily-secret',{method:'POST'});
  const rejected=assert.rejects(request,/Sign-in changed/);
  const other=browser({shared,network:async url=>response(url==='/account/logout'?{authenticated:false}:{profile:{woodchuck_id:'WC-A'}})});
  await other.window.fetch('/account/logout',{method:'POST'});
  await other.window.fetch('/account/login',{method:'POST'});
  late.resolve(response({credits:999,revision:9}));await rejected;
  assert.equal(api.stateForResponse(ticket),null);
  a.progress.credits=999;api.saveState(a);
  assert.equal(JSON.parse(shared.get('woodshedWoodchuckState.v1')).progress.credits,37);
  assert.equal(old.shell.hidden,true);
  await assert.rejects(old.window.fetch('/teams',{method:'POST'}),/Sign-in changed/);
  assert.equal(old.requests.length,1);
});

test('a delayed JSON body is guarded as well as the fetch promise',async()=>{
  const shared=new Map(); const body=pending();
  const old=browser({shared,network:async()=>({ok:true,status:200,json:()=>body.promise,text:async()=>''})});
  const res=await old.window.fetch('/account/state');const reading=res.json();const rejected=assert.rejects(reading,/Sign-in changed/);
  const other=browser({shared,network:async()=>response({profile:{woodchuck_id:'WC-B'}})});
  await other.window.fetch('/account/login',{method:'POST'});
  body.resolve({state:{account:{woodchuckId:'WC-A'},progress:{credits:999}}});await rejected;
  assert.equal(old.shell.hidden,true);
});

test('active account requests carry a page binding; Guest and stale tabs send no API request',async()=>{
  const b=browser();await b.window.fetch('/practice-charts',{method:'POST',headers:{'Content-Type':'application/json'}});
  assert.equal(b.requests[0].options.headers.get('X-Woodshed-Account'),'WC-A');
  b.shared.set('woodshed:session-change:v1','1');
  await assert.rejects(b.window.fetch('/account/state',{method:'PUT'}),/Sign-in changed/);
  assert.equal(b.requests.length,1);
});

test('failed Guest logout keeps tools closed and preserves all account data',async()=>{
  for(const failure of [async()=>response({detail:'unavailable'},503),async()=>{throw new Error('offline');}]) {
    const b=browser({network:failure});b.shared.set('woodshedWoodchuckState.v1','saved account');
    b.ids.set('guest-confirm-logout',element());b.ids.set('guest-feedback',element());b.load('guest.js');
    await b.ids.get('guest-confirm-logout').listeners.click();
    assert.equal(b.window.location.assigned,undefined);
    assert.equal(b.shared.get('woodshedWoodchuckState.v1'),'saved account');
    assert.match(b.ids.get('guest-feedback').textContent,/Guest tools remain closed/);
    assert.equal(b.ids.get('guest-confirm-logout').disabled,false);
  }
});

test('ordinary logout real handler retains local account state on HTTP or network failure',async()=>{
  for(const failure of [async()=>response({},503),async()=>{throw new Error('offline');}]) {
    const b=browser({network:failure});b.shared.set('woodshedWoodchuckState.v1','saved account');
    b.ids.set('authenticated-logout',element());
    const app=source('app.js');const start=app.indexOf('  function wireAuthenticatedLogout()');const end=app.indexOf('  function wireWoodchuckIdCopy()',start);
    vm.runInContext(app.slice(start,end)+'\nwireAuthenticatedLogout();',b.context);
    await b.ids.get('authenticated-logout').listeners.click();
    assert.equal(b.shared.get('woodshedWoodchuckState.v1'),'saved account');
    assert.equal(b.window.location.assigned,undefined);assert.equal(b.alerts.length,1);
  }
});

test('Guest sign-in real handler installs only latest server state, with no upload or Guest import',async()=>{
  const state={account:{woodchuckId:'WC-B',authenticated:true,serverRevision:13},profile:{woodchuckName:'Server B',instrument:'Flute',level:'Beginner',goal:'Server goal'},progress:{credits:83},practiceLog:[{note:'intervening server edit'}]};
  const b=browser({account:'',network:async url=>url==='/account/login'?response({profile:{woodchuck_id:'WC-B',display_name:'Server B',instrument:'Flute',level:'Beginner',goal:'Server goal'}}):response({state,revision:13})});
  b.shared.set('woodshed:guest:v1:preferences',JSON.stringify({instrument:'Tuba',credits:99999,practiceLog:['Guest'],inventory:{ownedItems:['all']},teams:['Guest']}));
  const form=element();form.querySelector=()=>({disabled:false,textContent:''});
  b.ids.set('account-login-form',form);b.ids.set('login-error',element());
  b.context.FormData=class {constructor(){this.values={woodchuck_id:'WC-B',pin:'2468'};}get(k){return this.values[k];}set(k,v){this.values[k]=v;}};
  b.load('state.js');b.load('account.js');
  await form.listeners.submit({preventDefault(){}});
  const saved=b.window.WWState.getState();
  assert.equal(saved.progress.credits,83);assert.equal(saved.account.serverRevision,13);
  assert.equal(saved.profile.instrument,'Flute');assert.deepEqual(JSON.parse(JSON.stringify(saved.practiceLog)),[{note:'intervening server edit'}]);
  assert.deepEqual(b.requests.map(r=>[r.url,r.options.method||'GET']),[['/account/login','POST'],['/account/state','GET']]);
  assert.equal(b.requests[1].options.headers.get('X-Woodshed-Account'),'WC-B');
  assert.equal(b.requests[1].options.cache,'no-store');
  assert.equal(b.window.location.assigned,'/home');
  assert.ok(b.shared.has('woodshed:guest:v1:preferences'));
});

test('bfcache restore stops the old page without silently changing the shared session',()=>{
  const b=browser();b.window.dispatchEvent({type:'pageshow',persisted:true});
  assert.equal(b.window.WWSessionBoundary.isCurrent(),false);assert.equal(b.shell.hidden,true);assert.equal(b.requests.length,0);
});

test('ordinary successful logout clears only its established account caches and drafts',async()=>{
  const b=browser({network:async()=>response({authenticated:false})});
  b.shared.set('woodshedWoodchuckState.v1','saved account');
  b.shared.set('woodshedWoodchuckConflictBackup.1','conflict');
  b.shared.set('woodshed:guest:v1:preferences','Guest preferences');
  b.session.set('woodshed:p-book:verifier-draft:v1','account draft');
  b.session.set('woodshed:practice-timer-started-at','123');
  b.ids.set('authenticated-logout',element());
  const app=source('app.js');const start=app.indexOf('  function wireAuthenticatedLogout()');const end=app.indexOf('  function wireWoodchuckIdCopy()',start);
  vm.runInContext(app.slice(start,end)+'\nwireAuthenticatedLogout();',b.context);
  await b.ids.get('authenticated-logout').listeners.click();
  assert.equal(b.shared.has('woodshedWoodchuckState.v1'),false);
  assert.equal(b.shared.has('woodshedWoodchuckConflictBackup.1'),false);
  assert.equal(b.shared.get('woodshed:guest:v1:preferences'),'Guest preferences');
  assert.equal(b.session.size,0);
  assert.equal(b.window.location.assigned,'/');
});

test('account page opened during Guest logout becomes stale on completion', async () => {
  const shared = new Map([['woodshedWoodchuckState.v1', JSON.stringify({
    account: {woodchuckId: 'WC-A', authenticated: true}
  })]]);
  const held = pending();
  const confirmation = browser({shared, account: 'WC-A', network: () => held.promise});
  confirmation.ids.set('guest-confirm-logout', element());
  confirmation.ids.set('guest-feedback', element());
  confirmation.load('guest.js');
  const logout = confirmation.ids.get('guest-confirm-logout').listeners.click();
  const lateTab = browser({shared, account: 'WC-A'});
  held.resolve(response({authenticated: false}));
  await logout;
  assert.equal(confirmation.window.location.assigned, '/guest');
  assert.equal(lateTab.window.WWSessionBoundary.isCurrent(), false);
  assert.equal(lateTab.shell.hidden, true);
});

for (const outcome of ['http-failure', 'network-failure', 'invalid-json']) {
  test(`pending tab is invalidated after ${outcome}, with Guest tools closed`, async()=>{
    const shared=new Map([['woodshedWoodchuckState.v1','saved']]);const held=pending();
    const b=browser({shared,network:()=>held.promise});
    b.ids.set('guest-confirm-logout',element());b.ids.set('guest-feedback',element());b.load('guest.js');
    const action=b.ids.get('guest-confirm-logout').listeners.click();const late=browser({shared});
    if(outcome==='http-failure') held.resolve(response({},503));
    else if(outcome==='network-failure') held.resolve(Promise.reject(new Error('connection lost')));
    else held.resolve(new Response('not json'));
    await action;
    assert.equal(late.window.WWSessionBoundary.isCurrent(),false);assert.equal(late.shell.hidden,true);
    assert.equal(b.window.location.assigned,undefined);
    assert.match(b.ids.get('guest-feedback').textContent,/Guest tools remain closed/);
    assert.equal(shared.get('woodshedWoodchuckState.v1'),'saved');
    assert.equal(b.window.WWSessionBoundary.isCurrent(),true);
  });
}
for (const [scenario,payload] of [
  ['signed out',{authenticated:false}],
  ['different account',{authenticated:true,profile:{woodchuck_id:'WC-B'},page_generation:'new'}],
  ['same account new session',{authenticated:true,profile:{woodchuck_id:'WC-A'},page_generation:'new'}],
  ['missing generation',{authenticated:true,profile:{woodchuck_id:'WC-A'}}],
]) {
  test(`delayed account document rejects ${scenario} before any consumer starts`, async()=>{
    const shared=new Map([['woodshed:session-change:v1','4'],['woodshedWoodchuckState.v1','new account data']]);
    const b=browser({shared,generation:'old',network:async()=>response(payload)});
    assert.equal(b.shell.hidden,true);assert.equal(b.window.WWSessionBoundary.isCurrent(),false);
    assert.equal(await b.window.WWSessionBoundary.ready,false);
    assert.equal(b.shell.hidden,true);assert.equal(b.loadedScripts.length,0);
    assert.equal(shared.get('woodshedWoodchuckState.v1'),'new account data');
    const before=b.requests.length;await assert.rejects(b.window.fetch('/account/state'),/Sign-in changed/);
    assert.equal(b.requests.length,before);
  });
}
test('fresh account gate loads consumers in order and only then opens the shell',async()=>{
  const held=pending();const b=browser({generation:'current',network:()=>held.promise});
  assert.equal(b.shell.hidden,true);assert.equal(b.loadedScripts.length,0);
  assert.equal(b.requests[0].url,'/account/me');assert.equal(b.requests[0].options.cache,'no-store');
  held.resolve(response({authenticated:true,profile:{woodchuck_id:'WC-A'},page_generation:'current'}));
  assert.equal(await b.window.WWSessionBoundary.ready,true);
  assert.equal(b.loadedScripts.length,2);assert.equal(b.shell.hidden,false);
  assert.equal(b.window.WWSessionBoundary.isCurrent(),true);
});
test('unavailable verification keeps account content closed',async()=>{
  const b=browser({generation:'current',network:async()=>{throw new Error('offline');}});
  assert.equal(await b.window.WWSessionBoundary.ready,false);
  assert.equal(b.shell.hidden,true);assert.equal(b.loadedScripts.length,0);
});
