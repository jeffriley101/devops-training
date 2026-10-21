// Controller-only transport doubles; real endpoints are covered separately in browsers.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const clone=x=>JSON.parse(JSON.stringify(x));
const flush=async()=>{for(let i=0;i<15;i++)await new Promise(r=>setImmediate(r));};
function element(tag='div', text='') {
  const classes=new Set();
  const el={tagName:tag.toUpperCase(),ownText:text,children:[],attributes:{},dataset:{},style:{},listeners:{},hidden:false,disabled:false,open:false,value:'',
    classList:{add(...xs){xs.forEach(x=>classes.add(x));},remove(...xs){xs.forEach(x=>classes.delete(x));},contains(x){return classes.has(x);}},
    get className(){return [...classes].join(' ');},set className(s){classes.clear();s.split(' ').filter(Boolean).forEach(x=>classes.add(x));},
    get textContent(){return this.ownText+this.children.map(x=>x.textContent).join(' ');},set textContent(s){this.ownText=String(s);this.children=[];},
    prepend(...xs){xs.forEach(x=>{x.parent=this;});this.children.unshift(...xs);},
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
function payload(){return {context:{available:true,week_start:'2026-10-19',week_end:'2026-10-25',recap_week_start:'2026-10-12',recap_week_end:'2026-10-18'},
 recap:{plan:null,practice:{practice_seconds:299,logging_days:1},tasks:null,receipts:{monday_bonus:null},recognition:{status:'pending',competitions:[]}},
 current:{plan:null,game_mode:'Challenge',active_instrument:'flute',start_context:null,plan_actions_available:true,monday_bonus:null}};}
function plan(){return {id:1,focus:'Build daily consistency',issued_game_mode:'Challenge',targets:{logging_days:4,practice_seconds:3600,homework_minutes:15,thirds:7},instruments:[]};}
function harness(data=payload()){
 const document={createElement:t=>{const e=element(t);e.document=document;return e;},getElementById:()=>null};
 const content=document.createElement('div'),status=document.createElement('p'),heading=document.createElement('h2');
 let generation=0,override,edit=0;const calls=[];
 const window={WWState:{accountRequest:()=>generation,stateForResponse:g=>g===generation?{}:null},WWSessionBoundary:{isCurrent:()=>true}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/js/weekly-newsletter.js'),'utf8'),{window,document,fetch:async(url,opt)=>{
  const call={url,method:opt.method||'GET',body:opt.body?JSON.parse(opt.body):null};calls.push(call);
  if(override){const r=await override(call);if(r)return r;}
  return {ok:true,json:async()=>clone(data)};
 }});
 const controller=window.WWNewsletter.create({container:content,status,heading,editProfile:()=>edit++});
 return {data,content,status,heading,calls,controller,open:()=>controller.open(),close:()=>controller.close(),switch:()=>generation++,set(fn){override=fn;},
  click(text){const b=content.querySelectorAll('button').find(x=>x.textContent===text);assert.ok(b,text);b.click();},
  select(){const s=content.querySelector('select');s.value='Build daily consistency';s.dispatchEvent({type:'change'});},get edits(){return edit;}};
}
const posts=h=>h.calls.filter(x=>x.method==='POST');
test('opening and dismissal read only; honest pending/unavailable history',async()=>{const h=harness();await h.open();assert.match(h.content.textContent,/Results: pending/);assert.match(h.content.textContent,/unavailable/);assert.match(h.content.textContent,/4 min 59 sec/);h.close();assert.equal(posts(h).length,0);});
test('publication unavailable contains no actions or inferred choices',async()=>{const d=payload();d.context.available=false;d.context.publication_at='Monday 13:30';const h=harness(d);await h.open();assert.equal(h.content.querySelectorAll('select').length,0);assert.match(h.content.textContent,/13:30/);});
test('NULL mode and disabled plan gate prevent issue',async()=>{for(const change of [d=>d.current.game_mode=null,d=>d.current.plan_actions_available=false]){const d=payload();change(d);const h=harness(d);await h.open();h.select();h.click('Confirm Focus and start this week');await flush();assert.equal(posts(h).length,0);}});
test('explicit Focus issues only focus then authoritative stage two',async()=>{const h=harness();h.set(c=>{if(c.method==='POST'){h.data.current.plan=plan();return {ok:true,json:async()=>({})};}});await h.open();h.select();h.click('Confirm Focus and start this week');await flush();assert.deepEqual(posts(h),[{url:'/weekly/plan',method:'POST',body:{focus:'Build daily consistency'}}]);assert.equal(h.content.dataset.stage,'2');});
test('changed instrument or week refuses mutation and refreshes',async()=>{for(const mutate of [d=>d.current.active_instrument='trumpet',d=>d.context.week_start='2026-10-26']){const h=harness();await h.open();h.select();mutate(h.data);h.click('Confirm Focus and start this week');await flush();assert.equal(posts(h).length,0);assert.match(h.status.textContent,/changed/);}});
test('lost committed issuance reloads saved plan without repeating POST',async()=>{const h=harness();h.set(c=>{if(c.method==='POST'){h.data.current.plan=plan();throw Error('Lost response');}});await h.open();h.select();h.click('Confirm Focus and start this week');await flush();assert.match(h.content.textContent,/Saved Focus/);assert.equal(posts(h).length,1);assert.equal(h.content.querySelectorAll('select').length,0);});
test('failed recovery GET exposes read retry only',async()=>{const h=harness();let failed=false;h.set(c=>{if(c.method==='POST'){failed=true;throw Error('Lost');}if(failed)throw Error('Unavailable');});await h.open();h.select();h.click('Confirm Focus and start this week');await flush();assert.equal(h.content.querySelectorAll('button').length,1);assert.equal(h.content.querySelector('button').textContent,'Retry Newsletter read');});
test('existing plan advances by GET only and start uses exact context',async()=>{const h=harness();h.data.current.plan=plan();h.data.current.start_context='saved-context';await h.open();h.click('View Your New Week');await flush();assert.equal(posts(h).length,0);h.click('Start work for flute');await flush();assert.deepEqual(posts(h)[0].body,{start_context:'saved-context'});});
test('stale context never silently starts changed work',async()=>{const h=harness();h.data.current.plan=plan();h.data.current.start_context='old';await h.open();h.click('View Your New Week');await flush();h.data.current.start_context='new';h.click('Start work for flute');await flush();assert.equal(posts(h).length,0);});
test('account change invalidates delayed reads and mutations',async()=>{for(const close of [h=>h.close(),h=>{h.close();h.switch();}]){const h=harness();let release;h.set(()=>new Promise(r=>release=r));const opened=h.open();close(h);release({ok:true,json:async()=>h.data});await opened;assert.equal(h.content.textContent,'');assert.equal(posts(h).length,0);}});
test('profile review uses existing handoff without a preference write',async()=>{const h=harness();await h.open();h.click('Review Instrument / Game Mode in profile');assert.equal(h.edits,1);assert.equal(posts(h).length,0);});
test('returned text remains text and prospective rewards never create claims',async()=>{const h=harness();h.data.current.plan=plan();h.data.current.plan.focus='<script>unsafe()</script>';await h.open();assert.match(h.content.textContent,/<script>/);assert.equal(h.content.querySelectorAll('script').length,0);assert.equal(posts(h).length,0);});
test('source uses only owned Newsletter reads and explicit plan writers; no reward/state/storage writer',()=>{
 const s=fs.readFileSync(path.join(__dirname,'../static/js/weekly-newsletter.js'),'utf8');
 assert.doesNotMatch(s,/\/family\/practice\/data|localStorage|indexedDB|saveState|applyEconomy|\/claim|\/weekly\/wheel|WWWeeklyCaper/);
 assert.deepEqual([...new Set([...s.matchAll(/request\('(\/[^']+)'/g)].map(x=>x[1]))].sort(),['/weekly/newsletter','/weekly/plan','/weekly/plan/instruments/current/start']);
});
test('entry uses existing Mum content, gated assets, and existing timer cancellation',()=>{
 const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
 const home=read('templates/home.html'),base=read('templates/base.html');
 assert.match(home,/<div id="mum-care-content"[^]*?if weekly_newsletter_allowed[^]*?id="weekly-newsletter-open"/);
 assert.match(base,/if weekly_newsletter_allowed[^]*?weekly-newsletter.js/);
 assert.match(read('static/js/app.js'),/"woodshed:newsletter-opened", \(\) => \{ cancelReady\(\); hideSnackChooser\(\); \}/);
 assert.match(read('static/css/weekly-newsletter.css'),/prefers-reduced-motion/);
});
test('finalized honors render saved recognition without ranking or rating calculations',async()=>{
 const h=harness();h.data.recap.recognition={status:'finalized',competitions:[{status:'finalized',honors:[{contest_key:'practice_minutes',medal:'gold',rank:1,subject_type:'student',rewards:[{reward_type:'dandelion',amount:50}]}]}]};
 await h.open();assert.match(h.content.textContent,/Results: finalized/);assert.match(h.content.textContent,/gold · rank 1/);assert.match(h.content.textContent,/50 dandelion recorded/);assert.match(h.content.textContent,/Well done/);assert.equal(posts(h).length,0);
});
