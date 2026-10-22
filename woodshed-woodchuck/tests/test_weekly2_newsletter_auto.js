// Sequencing doubles only; actual authorization and writes require separate backend/browser evidence.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(){
 const window={};vm.runInNewContext(fs.readFileSync('static/js/weekly-newsletter-auto.js','utf8'),{window,location:{pathname:'/other'}});
 let current=true,idle=true,readHook,reserveHook;const calls=[];
 const context={available:true,week_start:'2026-10-19',publication_at:'2026-10-19T13:30:00-05:00'};
 const c=window.WWNewsletterAuto.create({current:()=>current,idle:()=>idle,read:async()=>{calls.push('GET');return readHook?await readHook():{context};},reserve:async w=>{calls.push(['POST',w]);return reserveHook?await reserveHook():{status:'reserved',context};},open:w=>calls.push(['OPEN',w])});
 return {c,calls,context,setCurrent:v=>current=v,setIdle:v=>idle=v,read:f=>readHook=f,reserve:f=>reserveHook=f};
}
test('published winner opens once with exact server edition',async()=>{const h=harness();await h.c.attempt();await h.c.attempt();assert.deepEqual(h.calls,['GET',['POST','2026-10-19'],['OPEN','2026-10-19']]);});
test('unpublished read never reserves',async()=>{const h=harness();h.context.available=false;await h.c.attempt();assert.deepEqual(h.calls,['GET']);});
test('duplicate reservation never opens',async()=>{const h=harness();h.reserve(()=>({status:'already_reserved',context:h.context}));await h.c.attempt();assert.equal(h.calls.length,2);});
test('active surface or practice defers without any request',async()=>{const h=harness();h.setIdle(false);await h.c.attempt();assert.equal(h.calls.length,0);h.setIdle(true);await h.c.attempt();assert.equal(h.calls.length,3);});
for(const change of ['account','surface','epoch','pagehide'])test('delayed read rejects '+change,async()=>{const h=harness();let release;h.read(()=>new Promise(r=>release=r));const p=h.c.attempt();if(change==='account')h.setCurrent(false);if(change==='surface')h.setIdle(false);if(change==='epoch')h.c.invalidate();if(change==='pagehide')h.c.stop();release({context:h.context});await p;assert.deepEqual(h.calls,['GET']);});
for(const change of ['account','surface','epoch','pagehide'])test('committed reservation never paints stale '+change,async()=>{const h=harness();let release;h.reserve(()=>new Promise(r=>release=r));const p=h.c.attempt();await new Promise(r=>setImmediate(r));if(change==='account')h.setCurrent(false);if(change==='surface')h.setIdle(false);if(change==='epoch')h.c.invalidate();if(change==='pagehide')h.c.stop();release({status:'reserved',context:h.context});await p;assert.equal(h.calls.length,2);});
test('lost committed response never opens or repeats this attempt',async()=>{const h=harness();h.reserve(()=>{throw Error('lost');});await h.c.attempt();await h.c.attempt();assert.equal(h.calls.length,2);});
test('server stale-week rejection never opens',async()=>{const h=harness();h.reserve(()=>({status:'unavailable',context:{...h.context,week_start:'2026-10-26'}}));await h.c.attempt();assert.equal(h.calls.length,2);});
test('failed GET permits later safe entry without a write',async()=>{const h=harness();h.read(()=>{throw Error('offline');});await h.c.attempt();assert.deepEqual(h.calls,['GET']);h.read(null);await h.c.attempt();assert.equal(h.calls.length,4);});
test('no local clock, wallet, cache or preference writer',()=>{const s=fs.readFileSync('static/js/weekly-newsletter-auto.js','utf8');assert.doesNotMatch(s,/Date\(|localStorage|setInterval|saveState|\/claim|\/wheel|\/family/);});

test('automatic asset loads behind native account script verification after renderer',()=>{const s=fs.readFileSync('templates/base.html','utf8');assert.equal((s.match(/weekly-newsletter-auto.js/g)||[]).length,1);assert.ok(s.indexOf('weekly-newsletter-auto.js')>s.indexOf('<template id="account-scripts">'));assert.ok(s.indexOf('weekly-newsletter-auto.js')>s.indexOf('weekly-newsletter.js'));});
test('idle transition during a pending read schedules a fresh guarded attempt',async()=>{
 const window={};vm.runInNewContext(fs.readFileSync('static/js/weekly-newsletter-auto.js','utf8'),{window,location:{pathname:'/other'}});
 let release,wakes=0,posts=0;
 const c=window.WWNewsletterAuto.create({current:()=>true,idle:()=>true,read:()=>new Promise(r=>release=r),reserve:()=>{posts++;},open:()=>{},reschedule:()=>wakes++});
 const p=c.attempt();c.invalidate();await c.attempt();release({context:{available:true,week_start:'2026-10-19',publication_at:'server'}});await p;
 assert.equal(posts,0);assert.equal(wakes,1);
});
