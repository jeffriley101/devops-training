// Real player/controller source in a standalone VM; no browser or live transport.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = name => fs.readFileSync(path.join(__dirname, '../static/js', name), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
let pendingCallbacks=0;
function schedule(callback){pendingCallbacks++;setImmediate(()=>{try{callback();}finally{pendingCallbacks--;}});}
const flush = async () => {
 let idle=0;
 for(let turn=0;turn<1000;turn++){
  await new Promise(resolve=>setImmediate(resolve));
  idle=pendingCallbacks===0?idle+1:0;
  if(idle===2)return;
 }
 throw Error('Mock IndexedDB did not drain');
};
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
    focus(){this.document.activeElement=this;},scrollIntoView(){},animate(){return {cancel(){}};},remove(){this.parent.children=this.parent.children.filter(x=>x!==this);},
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
const weights=[['activity',2950],['dandelion',2000],['xp',2000],['spin_again',2000],['ufo',800],['dandelion_1000',200],['white_clover',50]].map(([outcome,weight])=>({outcome,weight}));
// Unit adapter only: native IndexedDB transaction ordering is verified in browsers.
function fakeIndexedDB(storage, denied, events) {
  return {open(){
    if(denied()==='open')throw Error('open denied');
    const request={};
    schedule(()=>{
      if(denied()==='blocked'){request.onblocked();return;}
      request.result={objectStoreNames:{contains:()=>true},close(){},transaction(name,mode,options){
        if(denied()==='transaction')throw Error('transaction denied');
        if(mode==='readwrite')assert.equal(options.durability,'strict');
        let active=true,pending=0;
        const writes=[];
        const tx={abort(){if(!active)return;active=false;events.push('abort');schedule(()=>tx.onabort());},objectStore(){return {
          get(key){if(denied()==='read')throw Error('read denied');return operation(()=>storage.get(key));},
          add(value,key){if(denied()==='write')throw Error('write denied');return operation(()=>{assert.equal(storage.has(key),false);writes.push(()=>storage.set(key,value));});},
          delete(key){if(denied()==='remove')throw Error('remove denied');return operation(()=>writes.push(()=>storage.delete(key)));},
        };}};
        function operation(fn){
          const req={};pending++;
          schedule(()=>{
            if(!active)return;
            try{req.result=fn();req.onsuccess?.();}
            catch(error){tx.error=error;tx.onerror?.();tx.abort();}
            pending--;
            schedule(()=>{
              if(active&&pending===0){
                if(denied()==='abort'&&mode==='readwrite'){tx.abort();return;}
                active=false;writes.forEach(f=>f());events.push(mode+'-complete');tx.oncomplete();
              }
            });
          });
          return req;
        }
        return tx;
      }};
      request.onsuccess();
    });
    return request;
  }};
}
function harness({locks, storage=new Map()}={}) {
  let epoch=0,current=true,data={play:null,wallet:{pickled_carrot:9,pants_choice:0},cost:3,remaining_plays:9,daily_limit:9,funded_day:'2026-10-21',credits:37,weights},fail='',handler;
  const requests=[],errors=[],events=[];
  const document={body:{dataset:{accountId:'A'}},getElementById:()=>null,createElement(tag){const x=element(tag);x.document=document;return x;}};
  const container=document.createElement('div'),status=document.createElement('p'),heading=document.createElement('h2');
  const window={WWSessionBoundary:{isCurrent:()=>current},WWState:{accountRequest:()=>({epoch}),stateForResponse:r=>r.epoch===epoch&&current?{}:null},matchMedia:()=>({matches:true})};
  const key='A';
  const context=vm.createContext({window,document,console,indexedDB:fakeIndexedDB(storage,()=>fail,events),crypto:{randomUUID:()=>`new-${++uuid}`},navigator:{locks:{request:async(n,fn)=>{try{return await (locks?locks(fn):fn());}catch(e){errors.push(e);throw e;}}}},fetch:async(url,opts)=>{
    events.push(opts.method||'GET');
    const req={url,method:opts.method||'GET',body:opts.body&&JSON.parse(opts.body),saved:storage.get(key)};requests.push(req);
    const result=handler?await handler(req):data;return {ok:true,json:async()=>clone(result===undefined?data:result)};
  }});
  vm.runInContext(source('weekly-wheel.js'),context);
  const controller=window.WWPrizeWheel.create({container,status,heading});
  return {controller,container,status,key,storage,requests,errors,events,window,
    click(label){const b=container.querySelectorAll('button').find(b=>b.textContent===label);assert.ok(b,label);b.click();},
    fail(value){fail=value;},handler(fn){handler=fn;},set(value){data={...data,...value};},
    switch(){epoch++;current=false;controller.close();},postCount(){return requests.filter(r=>r.method==='POST').length;}};
}
function play(outcome='activity',count=1){return {id:'owned',complete:outcome!=='spin_again',next_rotation:outcome==='spin_again'?count+1:null,outcomes:Array(count-1).fill('spin_again').concat(outcome)};}
function fund(h){h.click('Exchange 3 jars — PLAY');}
test('read only exact odds, NULL play and no state writer',async()=>{const h=harness();await h.controller.open();assert.equal(h.postCount(),0);assert.match(h.container.textContent,/29.5% — 14 Activity points/);assert.match(h.container.textContent,/2% — 1,000 Dandelions/);assert.equal(h.storage.size,0);});
test('explicit payment persists before POST; double click and confirmation clearing',async()=>{const h=harness();await h.controller.open();h.handler(r=>{if(r.method==='POST'){assert.equal(JSON.parse(r.saved).key,r.body.request_key);h.set({play:play(),wallet:{pickled_carrot:6}});}});fund(h);fund(h);await flush();assert.equal(h.postCount(),1);h.click('Continue');await flush();assert.equal(h.storage.size,0);assert.equal(h.errors.length,0);});
for(const operation of ['read','write'])test(`denied ${operation} never sends funding`,async()=>{const h=harness();await h.controller.open();h.fail(operation);fund(h);await flush();assert.equal(h.postCount(),0);assert.equal(h.errors.length,0);assert.match(h.container.textContent+h.status.textContent,/storage|retained/i);});
test('missing native locks fails closed',async()=>{const h=harness({locks:()=>{throw Error('coordination unavailable');}});await h.controller.open();fund(h);await flush();assert.equal(h.postCount(),0);assert.equal(h.storage.size,0);});
test('uncertain funding recovers exact key after close, no fresh key',async()=>{const h=harness();await h.controller.open();h.handler(r=>{if(r.method==='POST')throw Error('lost');});fund(h);await flush();const raw=h.storage.get(h.key);h.controller.close();h.handler(r=>{if(r.method==='POST'){assert.equal(r.body.request_key,JSON.parse(raw).key);h.set({play:play()});}});await h.controller.open();assert.equal(h.storage.get(h.key),raw);assert.equal(h.postCount(),2);});
for(const change of ['clear','replace'])test(`queued recovery ${change} cannot become funding`,async()=>{let release;const h=harness({locks:fn=>new Promise(resolve=>{release=async()=>resolve(await fn());})});const raw=JSON.stringify({kind:'fund',key:'original'});h.storage.set(h.key,raw);const opening=h.controller.open();await flush();if(change==='clear')h.storage.delete(h.key);else h.storage.set(h.key,JSON.stringify({kind:'fund',key:'newer'}));await release();await opening;assert.equal(h.postCount(),0);assert.equal(h.errors.length,0);});
test('recovery unchanged while queued uses exact action',async()=>{let release;const h=harness({locks:fn=>new Promise(resolve=>{release=async()=>resolve(await fn());})});h.storage.set(h.key,JSON.stringify({kind:'fund',key:'original'}));h.set({play:play()});const opening=h.controller.open();await flush();await release();await opening;assert.equal(h.requests.find(r=>r.method==='POST').body.request_key,'original');});
test('delayed Continue never deletes replacement action',async()=>{let queued=false,release;const h=harness({locks:fn=>queued?new Promise(resolve=>{release=async()=>resolve(await fn());}):fn()});h.storage.set(h.key,JSON.stringify({kind:'fund',key:'original'}));h.set({play:play()});await h.controller.open();queued=true;h.click('Continue');await flush();const replacement=JSON.stringify({kind:'fund',key:'newer'});h.storage.set(h.key,replacement);await release();await flush();assert.equal(h.storage.get(h.key),replacement);assert.equal(h.postCount(),1);});
test('denied removal preserves key and no subsequent payment',async()=>{const h=harness();h.storage.set(h.key,JSON.stringify({kind:'fund',key:'original'}));h.set({play:play()});await h.controller.open();h.fail('remove');h.click('Continue');await flush();assert.ok(h.storage.get(h.key));assert.equal(h.postCount(),1);assert.equal(h.errors.length,0);});
test('Spin Again only explicit next index; saved continuation recovery is GET only',async()=>{const h=harness();h.set({play:play('spin_again')});await h.controller.open();assert.equal(h.postCount(),0);h.handler(r=>{if(r.method==='POST'){assert.deepEqual(r.body,{rotation:2});h.set({play:play('spin_again',2)});}});h.click('SPIN AGAIN — FREE');await flush();assert.equal(h.postCount(),1);h.controller.close();await h.controller.open();assert.equal(h.postCount(),1);h.click('Continue');await flush();h.handler(r=>{if(r.method==='POST'){assert.deepEqual(r.body,{rotation:3});h.set({play:play('ufo',3)});}});h.click('SPIN AGAIN — FREE');await flush();assert.equal(h.postCount(),2);});
test('delayed response after account or panel change never repaints',async()=>{for(const action of ['switch','close']){let release;const h=harness();await h.controller.open();h.handler(r=>r.method==='POST'?new Promise(resolve=>{release=()=>resolve({play:play()});}):undefined);fund(h);await flush();if(action==='switch')h.switch();else h.controller.close();release();await flush();assert.equal(h.container.textContent,'');assert.ok(h.storage.get(h.key));assert.equal(h.postCount(),1);}});
test('historical pants readable, absent new odds',async()=>{const h=harness();h.set({play:play('pants_choice')});await h.controller.open();assert.match(h.container.textContent,/Saved result: Historical pants-choice prize/);assert.doesNotMatch(h.container.querySelector('.weekly-wheel-odds').textContent,/pants/i);});


// The original WL2-14c stale-localStorage deletion regression is preserved in
// its immutable evidence. These exercise the new transaction contract; real
// independent documents and native transactions are required in browser tests.
test('creation commits and readback completes before POST',async()=>{
 const h=harness();await h.controller.open();h.handler(r=>{if(r.method==='POST')h.set({play:play()});});
 fund(h);await flush();const post=h.events.indexOf('POST');
 assert.ok(post>0);assert.ok(h.events.slice(0,post).includes('readwrite-complete'));
 assert.equal(h.events[post-1],'readonly-complete');assert.equal(h.postCount(),1);
});
for(const failure of ['open','blocked','transaction','abort'])test(`IndexedDB ${failure} fails closed`,async()=>{
 const h=harness();await h.controller.open();h.fail(failure);fund(h);await flush();
 assert.equal(h.postCount(),0);assert.equal(h.storage.size,0);assert.equal(h.errors.length,0);
 assert.match(h.status.textContent+h.container.textContent,/storage|retained|commit/i);
});
for(const changed of ['clear','replace'])test(`completed response ${changed} reconciles without another POST`,async()=>{
 const h=harness();await h.controller.open();let newer;
 h.handler(r=>{if(r.method==='POST'){
   h.set({play:play('dandelion')});
   if(changed==='clear')h.storage.delete(h.key);
   else {newer=JSON.stringify({kind:'fund',key:'newer'});h.storage.set(h.key,newer);}
 }});
 fund(h);await flush();assert.equal(h.postCount(),1);
 assert.doesNotMatch(h.status.textContent,/Checking saved action/);
 assert.match(h.container.textContent,/Saved result: 400 Dandelions/);
 assert.equal(h.requests.at(-1).method,'GET');
 assert.equal(h.storage.get(h.key),newer);assert.equal(h.errors.length,0);
});
