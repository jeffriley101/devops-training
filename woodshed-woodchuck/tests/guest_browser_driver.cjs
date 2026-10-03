// Run only with test_guest_browser.py's disposable server and synthetic accounts.
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const assert=require('node:assert/strict');
let input='';process.stdin.on('data',c=>input+=c);
process.stdin.on('end',async()=>{
 const config=JSON.parse(input);
 const chrome=spawn(config.chrome,['--headless','--no-sandbox','--remote-debugging-pipe','--no-first-run',
 '--disable-background-networking','--disable-component-update','--disable-sync','--disable-extensions',
 '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
 '--autoplay-policy=no-user-gesture-required','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream',
 '--user-data-dir='+config.profile],{stdio:['ignore','ignore','ignore','pipe','pipe']});
 let seq=0,buffer=''; const pending=new Map();const events=[];const requestURLs=new Map();const requestMetadata=new Map();let intercept=null;
 const activeRequests=new Map(), completedRequests=[], networkActivity=new Map();
 const requestKey=m=>`${m.sessionId}:${m.params.requestId}`;
 const recordDiscardMetadata=requestId=>{
   const url=requestURLs.get(requestId),headers=requestMetadata.get(requestId);
   if(url && new URL(url).pathname==='/guest/discard' && headers)fs.writeFileSync(config.output+'/native-discard-network-headers.json',JSON.stringify(headers,null,2));
 };
 const timer=setTimeout(()=>{chrome.kill();process.exit(2);},170000);
 const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{
   const id=++seq;pending.set(id,{resolve,reject});chrome.stdio[3].write(JSON.stringify({id,method,params,sessionId})+'\0');
 });
 chrome.stdio[4].on('data',chunk=>{
   buffer+=chunk;let end;
   while((end=buffer.indexOf('\0'))>=0){
     const m=JSON.parse(buffer.slice(0,end));buffer=buffer.slice(end+1);
     if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}
     else if(m.method==='Network.requestWillBeSent'){
       const request={session:m.sessionId,url:m.params.request.url,method:m.params.request.method,index:events.length};
       events.push(request);
       // Blob workers are local resources and may have no loadingFinished
       // event. Keep them in the security log, but await only real network I/O.
       if(['http:','https:'].includes(new URL(request.url).protocol)){
         activeRequests.set(requestKey(m),request);networkActivity.set(m.sessionId,Date.now());
       }
       requestURLs.set(m.params.requestId,m.params.request.url);recordDiscardMetadata(m.params.requestId);
     }
     else if(m.method==='Network.responseReceived'){
       const request=activeRequests.get(requestKey(m));if(request)request.status=m.params.response.status;
     }
     else if(m.method==='Network.loadingFinished' || m.method==='Network.loadingFailed'){
       const request=activeRequests.get(requestKey(m));
       if(request){
         completedRequests.push({...request,error:m.params.errorText});
         activeRequests.delete(requestKey(m));networkActivity.set(m.sessionId,Date.now());
       }
     }
     else if(m.method==='Network.requestWillBeSentExtraInfo'){requestMetadata.set(m.params.requestId,Object.fromEntries(Object.entries(m.params.headers).filter(([key])=>['origin','sec-fetch-site','sec-fetch-mode','sec-fetch-dest'].includes(key.toLowerCase()))));recordDiscardMetadata(m.params.requestId);}
     else if(m.method==='Fetch.requestPaused' && intercept)intercept(m);
   }
 });
 const tab=async(newWindow=false)=>{
   const {targetId}=await send('Target.createTarget',{url:'about:blank',newWindow});
   const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
   await send('Network.enable',{},sessionId);
   const evaluate=async(expression)=>{
     const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true},sessionId);
     if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;
   };
   const until=async(expression)=>{
     const deadline=Date.now()+12000;
     while(Date.now()<deadline){
       // Check readiness and the predicate in one evaluation: navigation can
       // replace the document between separate protocol commands.
       try {
         if(await evaluate(`document.readyState==='complete' && !!document.body && (${expression})`))return;
       } catch(error) {
         // Only a destroyed execution context is transient. Predicate errors
         // in a usable document still fail immediately.
         if(error.code!==-32000 || !/Cannot find context|Execution context was destroyed/.test(error.message || ''))throw error;
       }
       await new Promise(r=>setTimeout(r,50));
     }
     throw new Error('Timeout waiting for a complete document and: '+expression);
   };
   const navigate=async pathname=>{
     const result=await send('Page.navigate',{url:config.origin+pathname},sessionId);
     assert.equal(result.errorText,undefined,`Navigation failed: ${pathname}: ${result.errorText}`);
     await until(`location.origin===${JSON.stringify(config.origin)} && location.pathname===${JSON.stringify(new URL(config.origin+pathname).pathname)} && !!window.WWSessionBoundary && window.WWSessionBoundary.isCurrent()`);
     await evaluate('window.WWSessionBoundary.ready');
   };
   return {sessionId,evaluate,until,navigate};
 };
 const settle=async(pages,{since=0,required=[]}={})=>{
   const sessions=new Set(pages.map(page=>page.sessionId)),started=Date.now(),deadline=started+12000;
   while(Date.now()<deadline){
     const outstanding=[...activeRequests.values()].filter(r=>sessions.has(r.session));
     const completed=completedRequests.filter(r=>sessions.has(r.session) && r.index>=since);
     const missing=required.filter(path=>!completed.some(r=>new URL(r.url).pathname===path && !r.error && r.status>=200 && r.status<300));
     // Require actual response completion, including known home initialization,
     // then a short network-quiet window for follow-up requests. Never just
     // sleep before taking a database baseline.
     if(!missing.length && !outstanding.length && [...sessions].every(id=>Date.now()-Math.max(started,networkActivity.get(id) || 0)>=250))return;
     await new Promise(r=>setTimeout(r,50));
   }
   throw new Error('Network did not settle: '+JSON.stringify({required,since,
     outstanding:[...activeRequests.values()].filter(r=>sessions.has(r.session)),
     completed:completedRequests.filter(r=>sessions.has(r.session) && r.index>=since)}));
 };
 const settledHome=async(page,accountId,since)=>{
   await page.until(`location.pathname==='/home' && window.WWState?.getState().account.woodchuckId===${JSON.stringify(accountId)} && !document.querySelector('.app-shell').hidden`);
   assert.equal(await page.evaluate('window.WWSessionBoundary.ready'),true);
   await settle([page],{since,required:['/teams','/account/login-streak','/xp','/store/inventory','/practice-charts/streak']});
 };
 const snapshot=()=>fetch(config.origin+'/test/snapshot').then(r=>r.json());
 const setupGuest=async g=>{
   await g.until(`!!document.getElementById('guest-setup-fields') && !document.getElementById('guest-setup-fields').disabled`);
   await g.evaluate(`for(const s of document.querySelectorAll('#guest-setup-form select'))s.value=s.options[1].value;document.querySelector('#guest-setup-form button').click();`);
   await g.until(`!document.getElementById('guest-tools').hidden && window.WWGuest.isCurrent() && !document.getElementById('guest-plunge-open').disabled`);
 };
 const buildChart=async g=>{
   await g.evaluate(`document.getElementById('guest-chart-minutes').value='17';document.querySelector('[data-guest-detail]').checked=true;document.getElementById('guest-chart-create').click()`);
   await g.until(`!document.getElementById('guest-chart-preview').hidden && document.getElementById('guest-chart-preview').textContent.includes('Practice minutes: 17')`);
 };
 const storageEvidence=g=>g.evaluate(`(async()=>({local:Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)])),session:Object.fromEntries(Object.keys(sessionStorage).map(k=>[k,sessionStorage.getItem(k)])),indexedDB:(await indexedDB.databases()).map(d=>d.name),caches:await caches.keys(),cookies:document.cookie}))()`);
 const assertFreshGuest=async g=>{
   await g.until(`document.readyState==='complete' && window.WWGuestChart?.ready && window.WWGuestPlunge?.ready && document.getElementById('guest-tools').hidden && Array.from(document.querySelectorAll('#guest-setup-form select')).every(s=>s.value==='') && document.getElementById('metronome-bpm-input').value==='120' && document.getElementById('guest-chart-preview').hidden && document.getElementById('guest-chart-preview').textContent==='' && window.WWGuestPlunge.snapshot().score===0 && window.WWGuestPlunge.snapshot().status==='ready'`);
 };
 let attackerServer;
 try{
   if(config.scenario==='stale_plunge') {
     const a=await tab(true);await a.navigate('/guest/login');
     const login=async (page,label)=>{
       const since=events.length;
       await page.evaluate(`document.getElementById('login-woodchuck-id').value='WC-GUEST-${label}';document.getElementById('login-pin').value='2468';document.querySelector('#account-login-form button').click()`);
       await settledHome(page,'WC-GUEST-'+label,since);
     };
     await login(a,'A');await a.navigate('/plunge-burrow');
     await a.until(`!!window.PlungeBurrowCore && !!window.WoodshedArcadeEconomy`);
     await a.evaluate(`window.realTick=PlungeBurrowCore.PlungeBurrowGame.prototype.tick;window.holdTick=false;PlungeBurrowCore.PlungeBurrowGame.prototype.tick=function(){window.oldGame=this;if(holdTick)return false;this.obstacles=[];this.portals=[];this.dandelion={x:this.trail[0].x+1,y:this.trail[0].y};const result=realTick.call(this);if(this.score>0)holdTick=true;return result;};document.getElementById('plunge-start').click()`);
     await a.until(`window.oldGame?.score===1 && holdTick`);
     const b=await tab(true);await b.navigate('/guest');
     await b.evaluate(`document.getElementById('guest-confirm-logout').click()`);
     await b.until(`document.body.dataset.guest==='local' && !!window.WWGuest`);
     await a.until(`!WWSessionBoundary.isCurrent() && oldGame.status==='paused' && document.getElementById('plunge-start').disabled && document.getElementById('plunge-restart').disabled`);
     await b.navigate('/guest/login');await login(b,'B');
     assert.equal(await b.evaluate(`localStorage.getItem('woodshed.plungeBurrow.bestScore')`),null);
     // B's real home initialization (including contest/calendar bootstrap) has
     // completed. Establish B's visible Plunge state before the security baseline.
     const plungeStart=events.length;
     await b.navigate('/plunge-burrow');
     await settle([a,b],{since:plungeStart,required:['/xp/plunge-best','/arcade/plays/status/plunge-burrow']});
     const visibleBefore=await b.evaluate(`({best:document.getElementById('plunge-best').textContent,score:document.getElementById('plunge-score').textContent})`);
     assert.deepEqual(visibleBefore,{best:'0',score:'0'});
     const before=await snapshot();
     const storageBefore=await storageEvidence(b);
     const staleStorageBefore=await storageEvidence(a);
     const requests=events.length;
     // Reproduce even a late callback that bypasses the cancelled animation loop.
     await a.evaluate(`oldGame.status='running';oldGame.score=999;oldGame.hearts=1;oldGame.trail=[{x:19,y:10},{x:18,y:10},{x:17,y:10}];oldGame.direction='right';realTick.call(oldGame);oldGame.writeBest(999)`);
     await settle([a,b]);
     assert.equal(await a.evaluate(`oldGame.status`),'gameover');
     assert.equal(await a.evaluate(`!WWSessionBoundary.isCurrent() && document.getElementById('plunge-start').disabled && document.getElementById('plunge-restart').disabled`),true);
     assert.deepEqual(await storageEvidence(b),storageBefore);
     assert.deepEqual(await storageEvidence(a),staleStorageBefore);
     assert.deepEqual(await snapshot(),before);
     assert.deepEqual(events.slice(requests).filter(e=>e.session===a.sessionId),[]);
     assert.deepEqual(await b.evaluate(`({best:document.getElementById('plunge-best').textContent,score:document.getElementById('plunge-score').textContent})`),visibleBefore);
     // Recreate B's game as well: a stale persisted 999 must not be consumed by
     // a fresh game, even if the already-open UI had stayed at zero.
     await b.navigate('/plunge-burrow');await b.until(`!!window.PlungeBurrowCore`);
     await b.evaluate(`const start=PlungeBurrowCore.PlungeBurrowGame.prototype.start;PlungeBurrowCore.PlungeBurrowGame.prototype.start=function(){window.newBest=this.best;return start.call(this)};document.getElementById('plunge-start').click()`);
     await b.until(`window.newBest!==undefined`);
     assert.equal(await b.evaluate(`newBest`),0);
     assert.equal(await b.evaluate(`document.getElementById('plunge-best').textContent`),'0');
     console.log(JSON.stringify({stale_plunge:true,old_game_stopped:true,late_collision_no_writes:true,B_best:0}));return;
   }
   if(config.scenario==='foreign_origin') {
     const http=require('node:http');
     attackerServer=http.createServer((req,res)=>{
       const route=new URL(req.url,'http://attacker').searchParams.get('route');
       if(req.url==='/favicon.ico'){res.writeHead(204);res.end();return;}
       assert.ok(['/guest/secret-symbol','/guest/discard','/account/logout','/account/login','/account/create'].includes(route));
       res.writeHead(200,{'Content-Type':'text/html'});
       res.end(`<form method="post" action="${config.origin}${route}"><input name="passcode" value="C001"></form><script>document.forms[0].submit()</script>`);
     });
     await new Promise(resolve=>attackerServer.listen(0,'127.0.0.1',resolve));
     const foreign='http://127.0.0.1:'+attackerServer.address().port;
     const g=await tab();
     await send('Page.navigate',{url:config.origin+'/prebeta/C001'},g.sessionId);
     await g.until(`location.pathname==='/guest' && document.readyState==='complete' && document.body.dataset.c001Context==='true'`);
     const cookies=async()=> (await send('Network.getCookies',{urls:[config.origin]},g.sessionId)).cookies.map(c=>({name:c.name,value:c.value}));
     const security=()=>fetch(config.origin+'/test/security-snapshot').then(r=>r.json());
     const beforeCookies=await cookies(),before=await snapshot(),beforeSecurity=await security();
     for(const route of ['/guest/secret-symbol','/guest/discard','/account/logout','/account/login','/account/create']) {
       await send('Page.navigate',{url:foreign+'/?route='+encodeURIComponent(route)},g.sessionId);
       await g.until(`location.origin===${JSON.stringify(config.origin)} && location.pathname===${JSON.stringify(route)} && document.readyState==='complete' && document.body.innerText.includes('same-origin request is required')`);
       assert.deepEqual(await cookies(),beforeCookies);
       assert.deepEqual(await snapshot(),before);
       assert.deepEqual(await security(),beforeSecurity);
     }
     // Normal native forms (including no-referrer opaque Origin) still work.
     await g.navigate('/guest');await setupGuest(g);
     await g.evaluate(`document.getElementById('shed-secret-button').click();document.getElementById('shed-secret-passcode').value='C001';document.querySelector('#shed-secret-form button[type=submit]').click()`);
     await g.until(`document.readyState==='complete' && document.body.innerText.includes('C001 Pre-Beta recognized.')`);
     await g.evaluate(`document.getElementById('guest-discard').click()`);
     await g.until(`document.readyState==='complete' && document.body.dataset.c001Context==='false'`);
     await g.navigate('/guest/login');
     const loginStart=events.length;
     await g.evaluate(`document.getElementById('login-woodchuck-id').value='WC-GUEST-A';document.getElementById('login-pin').value='2468';document.querySelector('#account-login-form button').click()`);
     // Foreign-origin rejection needs the same stable registered baseline as
     // stale Plunge; normal /teams bootstrap must finish before comparison.
     await settledHome(g,'WC-GUEST-A',loginStart);
     const signedCookies=await cookies(),signedBefore=await snapshot();
     await send('Page.navigate',{url:foreign+'/?route='+encodeURIComponent('/account/logout')},g.sessionId);
     await g.until(`location.pathname==='/account/logout' && document.body.innerText.includes('same-origin request is required')`);
     assert.deepEqual(await cookies(),signedCookies);assert.deepEqual(await snapshot(),signedBefore);
     await g.navigate('/guest');await g.evaluate(`document.getElementById('guest-confirm-logout').click()`);
     await g.until(`document.body.dataset.guest==='local' && !!window.WWGuest`);
     console.log(JSON.stringify({foreign_origin:true,rejected_routes:5,authenticated_logout_rejected:true,first_party_forms_work:true}));return;
   }
   if(config.scenario==='guest_history') {
     const g=await tab();await g.navigate('/guest');await setupGuest(g);await buildChart(g);
     const initial=await g.evaluate(`history.length`);
     await g.evaluate(`document.getElementById('metronome-open-button').click();document.getElementById('metronome-bpm-input').value='149';document.getElementById('metronome-bpm-input').dispatchEvent(new Event('change'))`);
     await g.until(`WWSurfaces.current()?.id==='metronome-panel'`);
     assert.equal(await g.evaluate(`history.state?.wwSurface ?? null`),null);
     await g.evaluate(`document.getElementById('metronome-close-button').click();document.getElementById('tuner-open-button').click()`);
     await g.until(`WWSurfaces.current()?.id==='tuner-panel'`);
     assert.equal(await g.evaluate(`history.state?.wwSurface ?? null`),null);
     await g.evaluate(`document.getElementById('tuner-close-button').click();document.getElementById('guest-plunge-open').click();document.getElementById('guest-plunge-start').click()`);
     assert.equal(await g.evaluate(`history.length`),initial);
     await g.evaluate(`document.getElementById('guest-discard').click()`);await assertFreshGuest(g);
     await g.until(`WWSurfaces.current()===null`);
     assert.equal(await g.evaluate(`history.state?.wwSurface ?? null`),null);
     // Retire history written by an older Guest version on reload/restoration.
     await g.evaluate(`history.replaceState({wwSurface:'metronome-panel',unrelated:'kept'},'',location.href)`);
     await send('Page.reload',{},g.sessionId);await assertFreshGuest(g);
     assert.deepEqual(await g.evaluate(`history.state`),{unrelated:'kept'});
     await setupGuest(g);await buildChart(g);await g.navigate('/guest/login');
     await g.evaluate(`history.back()`);await g.until(`location.pathname==='/guest' && document.readyState==='complete'`);await assertFreshGuest(g);
     assert.equal(await g.evaluate(`history.state?.wwSurface ?? null`),null);
     await g.evaluate(`history.forward()`);await g.until(`location.pathname==='/guest/login' && document.readyState==='complete'`);
     assert.equal(await g.evaluate(`history.state?.wwSurface ?? null`),null);
     await g.navigate('/guest/login');
     await g.evaluate(`document.getElementById('login-woodchuck-id').value='WC-GUEST-B';document.getElementById('login-pin').value='2468';document.querySelector('#account-login-form button').click()`);
     await g.until(`location.pathname==='/home' && document.readyState==='complete' && window.WWSessionBoundary?.isCurrent() && !!window.WWSurfaces`);
     await g.evaluate(`window.WWSessionBoundary.ready`);
     const registeredSurface=await g.evaluate(`({state:history.state,current:WWSurfaces.current()?.id ?? null})`);
     await g.evaluate(`document.getElementById('metronome-open-button').click()`);
     await g.until(`history.state?.wwSurface==='metronome-panel'`);
     await g.evaluate(`history.back()`);
     await g.until(`document.getElementById('metronome-panel').classList.contains('hidden') && WWSurfaces.current()?.id!=='metronome-panel'`);
     assert.deepEqual(await g.evaluate(`({state:history.state,current:WWSurfaces.current()?.id ?? null})`),registeredSurface);
     assert.equal(await g.evaluate(`location.pathname`),'/home');
     console.log(JSON.stringify({guest_history:true,no_tool_markers:true,discard_reload_back_forward_clear:true,registered_surface_history_preserved:true}));return;
   }
   if(config.scenario==='native_navigation') {
     const g=await tab();await g.navigate('/guest');await setupGuest(g);
     await g.evaluate(`document.getElementById('shed-secret-button').click();document.getElementById('shed-secret-passcode').value='C001';document.querySelector('#shed-secret-form button[type=submit]').click()`);
     await g.until(`document.readyState==='complete' && document.body.dataset.c001Context==='true'`);
     await assertFreshGuest(g);
     const login=await tab();await login.navigate('/guest/login');
     await login.until(`!!document.getElementById('account-login-form')`);
     await login.evaluate(`document.getElementById('login-woodchuck-id').value='WC-GUEST-B';document.getElementById('login-pin').value='2468'`);
     let heldDiscard;
     await send('Fetch.enable',{patterns:[{urlPattern:'*/guest/discard'}]},g.sessionId);
     intercept=m=>{
       heldDiscard=m;
       fs.writeFileSync(config.output+'/native-discard-headers.json',JSON.stringify(Object.fromEntries(Object.entries(m.params.request.headers).filter(([key])=>['origin','sec-fetch-site','sec-fetch-mode','sec-fetch-dest'].includes(key.toLowerCase()))),null,2));
     };
     await send('Page.bringToFront',{},g.sessionId);
     await g.evaluate(`document.getElementById('guest-discard').click()`);
     for(let i=0;i<240 && !heldDiscard;i++)await new Promise(r=>setTimeout(r,25));
     assert.ok(heldDiscard,'native Guest discard intercepted');
     await send('Page.bringToFront',{},login.sessionId);
     await login.evaluate(`document.querySelector('#account-login-form button[type=submit]').click()`);
     await login.until(`navigator.locks.query().then(q=>q.pending.some(lock=>lock.name==='woodshed-account-transition'))`);
     assert.equal(events.filter(e=>e.session===login.sessionId && new URL(e.url).pathname==='/account/login').length,0);
     await send('Fetch.continueRequest',{requestId:heldDiscard.params.requestId},g.sessionId);
     await g.until(`location.pathname==='/guest' && document.readyState==='complete' && document.body.dataset.c001Context==='false'`);
     await login.until(`location.pathname==='/home' && document.readyState==='complete' && window.WWState?.getState().account.woodchuckId==='WC-GUEST-B' && !document.querySelector('.app-shell').hidden`);
     assert.equal(await login.evaluate(`fetch('/account/me').then(r=>r.json()).then(p=>p.authenticated && p.profile.woodchuck_id==='WC-GUEST-B')`),true);
     const registered=await snapshot();
     assert.deepEqual(registered.states['WC-GUEST-B'].practiceLog,[{note:'saved account B'}]);
     assert.equal(registered.counts.tester_enrollments,0);
     await send('Fetch.disable',{},g.sessionId);intercept=null;
     fs.writeFileSync(config.output+'/native-navigation-network.json',JSON.stringify(events,null,2));
     console.log(JSON.stringify({native_guest_navigation_serializes_login:true,native_discard_source_cleared:true,later_account_cookie_preserved:true}));
     return;
   }
   if(config.scenario==='c001') {
   // Classroom code uses the real shared SHED form and deliberate QR route.
   const g=await tab();await g.navigate('/guest');
   const beforeSymbol=await snapshot();
   await setupGuest(g);
   await g.evaluate(`document.getElementById('shed-secret-button').click()`);
   await g.evaluate(`document.getElementById('shed-secret-passcode').value='C002';document.querySelector('#shed-secret-form button[type=submit]').click()`);
   await g.until(`document.getElementById('guest-secret-feedback')?.textContent.includes('did not match')`);
   assert.equal(await g.evaluate(`Array.from(document.querySelectorAll('a[href="/setup?from=guest"]')).some(a=>a.textContent.includes('C001'))`),false);
   assert.equal(await g.evaluate(`document.body.dataset.c001Context`),'false');
   await assertFreshGuest(g);
   for(let i=0;i<2;i++) {
     await setupGuest(g);
     await g.evaluate(`document.getElementById('shed-secret-button').click()`);
     await g.evaluate(`document.getElementById('shed-secret-passcode').value='  c001  ';document.querySelector('#shed-secret-form button[type=submit]').click()`);
     await g.until(`location.pathname==='/guest' && document.readyState==='complete' && document.body.innerText.includes('C001 Pre-Beta recognized.') && document.getElementById('shed-secret-panel').hidden`);
     assert.equal(await g.evaluate(`document.body.dataset.guest`),'local');
     await assertFreshGuest(g);
     assert.equal(await g.evaluate(`document.body.dataset.c001Context`),'true');
     assert.equal(await g.evaluate(`localStorage.getItem('woodshed:guest:v1:preferences')`),null);
     assert.deepEqual(await snapshot(),beforeSymbol);
   }
   assert.equal(await g.evaluate(`fetch('/account/daily-secret',{method:'POST'}).then(()=>false,()=>true)`),true);
   // Explicit discard clears the temporary signed source claim through its native form.
   await send('Fetch.enable',{patterns:[{urlPattern:'*/guest/discard'}]},g.sessionId);
   intercept=async m=>{
     const headers=Object.fromEntries(Object.entries(m.params.request.headers).filter(([key])=>['origin','sec-fetch-site','sec-fetch-mode','sec-fetch-dest'].includes(key.toLowerCase())));
     fs.writeFileSync(config.output+'/c001-discard-headers.json',JSON.stringify(headers,null,2));
     await send('Fetch.continueRequest',{requestId:m.params.requestId},m.sessionId);
   };
   await g.evaluate(`document.getElementById('guest-discard').click()`);
   await g.until(`document.readyState==='complete' && document.body.dataset.c001Context==='false'`);
   await send('Fetch.disable',{},g.sessionId);intercept=null;
   await assertFreshGuest(g);await setupGuest(g);
   await g.evaluate(`document.getElementById('shed-secret-button').click();document.getElementById('shed-secret-passcode').value='C001';document.querySelector('#shed-secret-form button[type=submit]').click()`);
   await g.until(`document.readyState==='complete' && document.body.dataset.c001Context==='true'`);
   await assertFreshGuest(g);
   await g.evaluate(`document.querySelector('a[href="/setup?from=guest"]').click()`);
   await g.until(`location.pathname==='/setup' && document.readyState==='complete'`);
   await g.evaluate(`document.getElementById('registration-age').value='13to17';document.querySelector('form[action="/setup"] button').click()`);
   await g.until(`!!document.getElementById('account-create-form') && document.readyState==='complete'`);
   await g.evaluate(`document.getElementById('woodchuck-name').value='Classroom Tester';document.getElementById('student-pin').value='2468';for(const s of document.querySelectorAll('#account-create-form select'))s.value=s.options[1].value;document.querySelector('#account-create-form button[type=submit]').click()`);
   await g.until(`!!document.getElementById('created-woodchuck-id')?.textContent.trim()`);
   const enrolled=await fetch(config.origin+'/test/c001').then(r=>r.json());
   assert.deepEqual(enrolled,[{cohort:'C001',source:'DIRECTOR1',full:true}]);
   const afterSymbol=await snapshot();
   assert.equal(afterSymbol.counts.woodchuck_profiles,beforeSymbol.counts.woodchuck_profiles+1);
   assert.equal(afterSymbol.counts.tester_enrollments,beforeSymbol.counts.tester_enrollments+1);
   await g.navigate('/guest');
   assert.equal(await g.evaluate(`document.body.dataset.c001Context`),'false');
   fs.writeFileSync(config.output+'/c001-network.json',JSON.stringify(events,null,2));
     console.log(JSON.stringify({secret_symbol_c001_registration:true,c001_discard_cleared:true,c001_registration_consumed_once:true,c001_guest_activity_memory_only:true}));
     return;
   }
   const before=await snapshot();const g=await tab();await g.navigate('/');
   assert.equal(await g.evaluate(`localStorage.getItem('woodshedWoodchuckState.v1')`),null);
   await g.navigate('/guest');
   const guestStart=events.length;
   await setupGuest(g);
   await g.evaluate(`document.getElementById('metronome-open-button').click();document.getElementById('metronome-start-button').click();`);
   await g.until(`document.getElementById('metronome-start-button').textContent==='Stop'`);
   assert.equal(await g.evaluate(`document.getElementById('metronome-bpm-input').value`),'120');
   await g.evaluate(`const bpm=document.getElementById('metronome-bpm-input');bpm.value='132';bpm.dispatchEvent(new Event('change'));document.getElementById('metronome-start-button').click();`);
   await send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:config.output});
   await g.evaluate(`(()=>{window.guestExports={shared:[],copied:[],blobs:[],revoked:[]};Object.defineProperty(navigator,'share',{configurable:true,value:async payload=>{guestExports.shared.push(payload.files ? await payload.files[0].text() : payload.text);}});Object.defineProperty(navigator,'canShare',{configurable:true,value:()=>true});Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{guestExports.copied.push(text);}}});const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);URL.createObjectURL=blob=>{guestExports.blobs.push(blob);return create(blob);};URL.revokeObjectURL=url=>{guestExports.revoked.push(url);return revoke(url);};})()`);
   await buildChart(g);
   const preview=await g.evaluate(`document.getElementById('guest-chart-preview').textContent`);
   assert.equal(/Woodchuck ID|Student name|Email|Verifier|Director|Team|Contest|Reward/i.test(preview),false);
   assert.equal(await g.evaluate(`guestExports.copied.length+guestExports.shared.length+guestExports.blobs.length`),0);
   await g.evaluate(`document.getElementById('guest-chart-share').click()`);
   await g.until(`guestExports.shared.length===1 && !document.getElementById('guest-chart-copy').disabled`);
   await g.evaluate(`document.getElementById('guest-chart-copy').click()`);
   await g.until(`guestExports.copied.length===1 && !document.getElementById('guest-chart-download').disabled`);
   await g.evaluate(`document.getElementById('guest-chart-download').click()`);
   await g.until(`guestExports.blobs.length===1 && guestExports.revoked.length===1`);
   assert.equal(await g.evaluate(`guestExports.shared[0]`),preview);
   assert.equal(await g.evaluate(`guestExports.copied[0]`),preview);
   assert.equal(await g.evaluate(`guestExports.blobs[0].text()`),preview);
   // Unsupported native Share falls back to another local Blob download.
   await g.evaluate(`Object.defineProperty(navigator,'share',{configurable:true,value:undefined});document.getElementById('guest-chart-share').click()`);
   await g.until(`guestExports.blobs.length===2 && guestExports.revoked.length===2`);
   assert.equal(await g.evaluate(`guestExports.blobs[1].text()`),preview);
   // The browser runs the real engine; make its first pickup deterministic.
   await g.evaluate(`(()=>{window.originalGuestTick=PlungeBurrowCore.PlungeBurrowGame.prototype.tick;PlungeBurrowCore.PlungeBurrowGame.prototype.tick=function(){this.obstacles=[];this.portals=[];this.dandelion={x:this.trail[0].x+1,y:this.trail[0].y};return originalGuestTick.call(this);};document.getElementById('guest-plunge-open').click();document.getElementById('guest-plunge-start').click();})()`);
   await g.until(`window.WWGuestPlunge.snapshot().score>0 && window.WWGuestPlunge.snapshot().status==='running'`);
   await g.evaluate(`document.getElementById('guest-plunge-pause').click();PlungeBurrowCore.PlungeBurrowGame.prototype.tick=originalGuestTick`);
   assert.equal(await g.evaluate(`window.WWGuestPlunge.snapshot().status`),'paused');
   await g.evaluate(`document.getElementById('guest-plunge-replay').click()`);
   assert.equal(await g.evaluate(`window.WWGuestPlunge.snapshot().score`),0);
   await g.evaluate(`(()=>{PlungeBurrowCore.PlungeBurrowGame.prototype.tick=function(){this.score=2147483647;this.hearts=1;this.handleCollision('wall');};document.getElementById('guest-plunge-start').click();})()`);
   await g.until(`window.WWGuestPlunge.snapshot().status==='gameover' && document.getElementById('guest-plunge-score').textContent==='2147483647'`);
   await g.evaluate(`PlungeBurrowCore.PlungeBurrowGame.prototype.tick=originalGuestTick`);
   assert.equal(await g.evaluate(`!!document.querySelector('#guest-plunge-best,[data-arcade-balance],#plunge-leaderboard')`),false);
   await g.evaluate(`window.guestStreams=[];window.guestAudioContexts=[];const gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async(...args)=>{const s=await gum(...args);guestStreams.push(s);return s;};const GuestNativeAudioContext=window.AudioContext;window.AudioContext=class extends GuestNativeAudioContext{constructor(...args){super(...args);guestAudioContexts.push(this);}};document.getElementById('tuner-open-button').click();`);
   await g.until(`guestStreams.length===1 && document.getElementById('tuner-diagnosis').textContent!=='REQUESTING MICROPHONE'`);
   assert.equal(await g.evaluate(`guestStreams[0].getTracks()[0].readyState`),'live');
   const afterTools=await snapshot();assert.deepEqual(afterTools,before);
   const guestStorage=await storageEvidence(g);
   assert.deepEqual(guestStorage,{local:{},session:{},indexedDB:[],caches:[],cookies:''});
   const browserCookies=await send('Network.getCookies',{urls:[config.origin+'/guest']},g.sessionId);
   assert.deepEqual(browserCookies.cookies,[]); // Include HttpOnly cookies in the identity check.
   await g.evaluate(`document.getElementById('guest-discard').click()`);
   await g.until(`guestStreams[0].getTracks()[0].readyState==='ended' && guestAudioContexts.every(c=>c.state==='closed')`);
   await assertFreshGuest(g);
   await setupGuest(g);await buildChart(g);
   await g.evaluate(`document.getElementById('metronome-bpm-input').value='139';document.getElementById('metronome-bpm-input').dispatchEvent(new Event('change'));document.getElementById('guest-plunge-open').click()`);
   await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true},g.sessionId);
   const image=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},g.sessionId);
   fs.writeFileSync(config.output+'/guest-mobile.png',Buffer.from(image.data,'base64'));
   await send('Page.reload',{},g.sessionId);await assertFreshGuest(g);
   await setupGuest(g);await buildChart(g);
   await g.evaluate(`document.getElementById('metronome-bpm-input').value='144';document.getElementById('metronome-bpm-input').dispatchEvent(new Event('change'));window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));`);
   await assertFreshGuest(g);
   assert.equal(await g.evaluate(`window.WWSessionBoundary.isCurrent()`),false);
   await send('Page.reload',{},g.sessionId);await assertFreshGuest(g);
   const guestRequests=events.slice(guestStart).filter(e=>{
     const url=new URL(e.url);
     // Inline browser date-picker art and explicit Blob output make no network request.
     if(['data:','blob:'].includes(url.protocol))return false;
     return !(url.origin===config.origin && (url.pathname==='/guest' || url.pathname.startsWith('/static/') || url.pathname.startsWith('/favicon')));
   });
   assert.deepEqual(guestRequests,[]);
   assert.deepEqual(await snapshot(),before);
   // Traverse actual browser history with live Guest product data present.
   // Account-entry navigation is measured separately from the zero-I/O journey.
   await setupGuest(g);await buildChart(g);
   await g.evaluate(`document.getElementById('metronome-bpm-input').value='147';document.getElementById('metronome-bpm-input').dispatchEvent(new Event('change'));window.guestHistoryTick=PlungeBurrowCore.PlungeBurrowGame.prototype.tick;PlungeBurrowCore.PlungeBurrowGame.prototype.tick=function(){this.obstacles=[];this.portals=[];this.dandelion={x:this.trail[0].x+1,y:this.trail[0].y};return guestHistoryTick.call(this);};document.getElementById('guest-plunge-open').click();document.getElementById('guest-plunge-start').click()`);
   await g.until(`window.WWGuestPlunge.snapshot().score>0`);
   await g.evaluate(`document.getElementById('guest-plunge-pause').click();PlungeBurrowCore.PlungeBurrowGame.prototype.tick=guestHistoryTick`);
   await g.navigate('/guest/login');
   await g.evaluate(`history.back()`);
   await g.until(`location.pathname==='/guest' && document.readyState==='complete'`);
   await assertFreshGuest(g);
   assert.equal(await g.evaluate(`window.WWGuest.isCurrent()`),false);
   await g.evaluate(`history.forward()`);
   await g.until(`location.pathname==='/guest/login' && document.readyState==='complete' && !!document.getElementById('account-login-form')`);
   assert.equal(await g.evaluate(`!!document.getElementById('guest-tools') || !!window.WWGuestPlunge || !!window.WWGuestChart`),false);
   if(!await g.evaluate(`window.WWSessionBoundary.isCurrent()`)) {
     await send('Page.reload',{},g.sessionId);
     await g.until(`document.readyState==='complete' && window.WWSessionBoundary?.isCurrent() && !!document.getElementById('account-login-form')`);
   }
   assert.deepEqual(await snapshot(),before);
   await g.navigate('/guest');await assertFreshGuest(g);
   await g.evaluate(`localStorage.setItem('woodshed:guest:v1:history',JSON.stringify({progress:{credits:99999},practiceLog:['Guest-only'],inventory:{ownedItems:['all']}}));document.querySelector('a[href="/guest/login"]').click();`);
   await g.until(`location.pathname==='/guest/login' && document.readyState==='complete' && !!document.getElementById('account-login-form')`);
   await g.evaluate(`document.getElementById('login-woodchuck-id').value='WC-GUEST-B';document.getElementById('login-pin').value='2468';document.querySelector('#account-login-form button').click()`);
   await g.until(`location.pathname==='/home' && document.readyState==='complete' && window.WWState?.getState().account.woodchuckId==='WC-GUEST-B'`);
   const installed=await g.evaluate(`window.WWState.getState()`);const server=await snapshot();
   assert.equal(installed.progress.credits,server.states['WC-GUEST-B'].progress.credits);
   assert.deepEqual(installed.practiceLog,[{note:'saved account B'}]);
   assert.deepEqual(server.states['WC-GUEST-B'].practiceLog,[{note:'saved account B'}]);
   const old=await tab();await old.navigate('/home');
   await g.navigate('/guest');assert.equal(await g.evaluate(`!!document.getElementById('guest-confirm-logout')`),true);
   assert.equal(await g.evaluate(`!!document.getElementById('guest-setup-form')`),false);
   await send('Fetch.enable',{patterns:[{urlPattern:'*/account/logout'}]},g.sessionId);
   intercept=async m=>{await send('Fetch.fulfillRequest',{requestId:m.params.requestId,responseCode:503,body:Buffer.from('{}').toString('base64')},m.sessionId);};
   await g.evaluate(`document.getElementById('guest-confirm-logout').click()`);
   await g.until(`document.getElementById('guest-feedback').textContent.includes('Guest tools remain closed')`);
   assert.equal(await g.evaluate(`fetch('/account/me').then(r=>r.json()).then(p=>p.profile.woodchuck_id)`),'WC-GUEST-B');
   await send('Fetch.disable',{},g.sessionId);intercept=null;
   // Hold the actual Guest logout request before the server processes it.
   // The late account document still renders B but must remain closed while
   // its verification waits for the browser's shared transition lock.
   let heldLogout;
   await send('Fetch.enable',{patterns:[{urlPattern:'*/account/logout'}]},g.sessionId);
   intercept=m=>{heldLogout=m;};
   await g.evaluate(`document.getElementById('guest-confirm-logout').click()`);
   for(let i=0;i<240 && !heldLogout;i++)await new Promise(r=>setTimeout(r,25));
   assert.ok(heldLogout,'logout intercepted');
   const during=await tab();
   await send('Page.navigate',{url:config.origin+'/home'},during.sessionId);
   await during.until(`document.readyState==='complete' && !!window.WWSessionBoundary`);
   assert.equal(await during.evaluate(`document.body.dataset.accountId`),'WC-GUEST-B');
   assert.equal(await during.evaluate(`document.querySelector('.app-shell').hidden && !window.WWState`),true);
   await send('Fetch.continueRequest',{requestId:heldLogout.params.requestId},g.sessionId);
   await g.until(`document.readyState==='complete' && !!document.getElementById('guest-setup-form')`);
   await send('Fetch.disable',{},g.sessionId);intercept=null;
   assert.equal(await during.evaluate(`window.WWSessionBoundary.ready`),false);
   assert.equal(await during.evaluate(`window.WWSessionBoundary.isCurrent()`),false);
   assert.equal(await during.evaluate(`getComputedStyle(document.querySelector('.app-shell')).display`),'none');
   const lateRequests=events.filter(e=>e.session===during.sessionId).length;
   assert.equal(await during.evaluate(`fetch('/account/daily-secret',{method:'POST'}).then(()=>false,()=>true)`),true);
   assert.equal(events.filter(e=>e.session===during.sessionId).length,lateRequests);

   await old.until(`document.querySelector('.app-shell').hidden && getComputedStyle(document.querySelector('.app-shell')).display==='none'`);
   const oldRequestCount=events.filter(e=>e.session===old.sessionId).length;
   assert.equal(await old.evaluate(`fetch('/account/daily-secret',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passcode:'union'})}).then(()=>false,()=>true)`),true);
   assert.equal(events.filter(e=>e.session===old.sessionId).length,oldRequestCount);
   const afterLogout=await snapshot();
   await g.evaluate(`document.querySelector('a[href="/guest/login"]').click()`);
   await g.until(`location.pathname==='/guest/login' && document.readyState==='complete' && !!document.getElementById('account-login-form')`);
   await g.evaluate(`document.getElementById('login-woodchuck-id').value='WC-GUEST-A';document.getElementById('login-pin').value='2468';document.querySelector('#account-login-form button').click()`);
   await g.until(`location.pathname==='/home' && document.readyState==='complete' && window.WWState?.getState().account.woodchuckId==='WC-GUEST-A'`);
   // Registered History keeps its real server protocol and shared state consumers.
   await g.navigate('/arcade/history-mystery');
   await g.until(`document.getElementById('history-mystery-start') && !document.getElementById('history-mystery-start').disabled`);
   await g.evaluate(`document.getElementById('history-mystery-start').click()`);
   for(let index=0;index<5;index++) {
     await g.until(`!!document.querySelector('#history-mystery-answers button:not(:disabled)')`);
     await g.evaluate(`document.querySelector('#history-mystery-answers button:not(:disabled)').click()`);
     if(index<4)await g.until(`document.getElementById('history-mystery-progress').textContent==='${index+2}'`);
     else await g.until(`document.getElementById('history-mystery-start').textContent==='Played Today'`);
   }
   const final=await snapshot();assert.deepEqual(final.states['WC-GUEST-B'],afterLogout.states['WC-GUEST-B']);
   assert.deepEqual(await g.evaluate(`window.WWState.getState().practiceLog`),[{note:'saved account A'}]);
   // Delay the boundary script (not old Set-Cookie headers) of an already
   // requested/rendered A document, then complete logout before initialization.
   // Repeat with same-account return and different-account sign-in to prove
   // identity equality alone cannot validate a stale document.
   const delayedCases=[];
   async function loginAccount(label) {
     await g.navigate('/guest/login');
     await g.evaluate(`document.getElementById('login-woodchuck-id').value='WC-GUEST-${label}';document.getElementById('login-pin').value='2468';document.querySelector('#account-login-form button').click()`);
     await g.until(`location.pathname==='/home' && window.WWState?.getState().account.woodchuckId==='WC-GUEST-${label}' && !document.querySelector('.app-shell').hidden`);
   }
   // A's actual P-Book draft and timer stay owned by A through account transitions.
   const draftOwner=await tab();await draftOwner.navigate('/p-book');
   await draftOwner.until(`window.WWState?.getState().account.woodchuckId==='WC-GUEST-A' && !!document.getElementById('p-book-verifier-manage')`);
   await draftOwner.evaluate(`document.getElementById('p-book-minutes').value='23';document.getElementById('p-book-note').value='Private account A draft';document.getElementById('practice-timer-start-btn').click();document.getElementById('p-book-verifier-manage').click()`);
   await draftOwner.until(`location.pathname==='/trusted-verifiers' && document.readyState==='complete'`);
   const accountDraft=await draftOwner.evaluate(`({draft:sessionStorage.getItem('woodshed:p-book:verifier-draft:v1'),timer:sessionStorage.getItem('woodshed:practice-timer-started-at')})`);
   assert.equal(JSON.parse(accountDraft.draft).binding.accountId,'WC-GUEST-A');
   assert.equal(JSON.parse(accountDraft.timer).binding.accountId,'WC-GUEST-A');
   await g.navigate('/guest');await g.evaluate(`document.getElementById('guest-confirm-logout').click()`);
   await g.until(`document.readyState==='complete' && !!document.getElementById('guest-setup-form')`);
   await draftOwner.until(`!sessionStorage.getItem('woodshed:p-book:verifier-draft:v1') && !sessionStorage.getItem('woodshed:practice-timer-started-at') && !window.WWSessionBoundary.isCurrent()`);
   assert.equal(await g.evaluate(`localStorage.getItem('woodshedWoodchuckState.v1')`),null);
   await loginAccount('B');
   await draftOwner.navigate('/p-book');
   await draftOwner.evaluate(`sessionStorage.setItem('woodshed:p-book:verifier-draft:v1',${JSON.stringify(accountDraft.draft)});sessionStorage.setItem('woodshed:practice-timer-started-at',${JSON.stringify(accountDraft.timer)})`);
   await send('Page.reload',{},draftOwner.sessionId);
   await draftOwner.until(`document.readyState==='complete' && window.WWState?.getState().account.woodchuckId==='WC-GUEST-B' && !document.querySelector('.app-shell').hidden`);
   assert.equal(await draftOwner.evaluate(`document.getElementById('p-book-note').value==='' && document.getElementById('p-book-minutes').value==='' && document.getElementById('practice-timer-display').textContent==='00:00' && !sessionStorage.getItem('woodshed:p-book:verifier-draft:v1') && !sessionStorage.getItem('woodshed:practice-timer-started-at')`),true);
   // Return to an actual old A document while B owns the server session.
   // A cached page stays closed; a network reload receives B's authority.
   const accountHistory=await send('Page.getNavigationHistory',{},draftOwner.sessionId);
   assert.equal(new URL(accountHistory.entries[accountHistory.currentIndex-1].url).pathname,'/trusted-verifiers');
   const assertBHistory=async pathname=>{
     await draftOwner.until(`location.pathname===${JSON.stringify(pathname)} && document.readyState==='complete' && !!window.WWSessionBoundary`);
     await draftOwner.evaluate(`window.WWSessionBoundary.ready`);
     assert.equal(await draftOwner.evaluate(`window.WWSessionBoundary.isCurrent() ? window.WWState?.getState().account.woodchuckId==='WC-GUEST-B' : document.querySelector('.app-shell').hidden && getComputedStyle(document.querySelector('.app-shell')).display==='none'`),true);
     assert.equal(await draftOwner.evaluate(`JSON.parse(localStorage.getItem('woodshedWoodchuckState.v1')).account.woodchuckId==='WC-GUEST-B' && !sessionStorage.getItem('woodshed:p-book:verifier-draft:v1') && !sessionStorage.getItem('woodshed:practice-timer-started-at')`),true);
   };
   await draftOwner.evaluate(`history.back()`);await assertBHistory('/trusted-verifiers');
   await send('Page.reload',{},draftOwner.sessionId);
   await draftOwner.until(`document.readyState==='complete' && window.WWSessionBoundary?.isCurrent() && window.WWState?.getState().account.woodchuckId==='WC-GUEST-B' && !document.querySelector('.app-shell').hidden`);
   await draftOwner.evaluate(`history.forward()`);await assertBHistory('/p-book');
   await send('Page.reload',{},draftOwner.sessionId);
   await draftOwner.until(`document.readyState==='complete' && window.WWSessionBoundary?.isCurrent() && window.WWState?.getState().account.woodchuckId==='WC-GUEST-B' && !document.querySelector('.app-shell').hidden`);
   assert.equal(await draftOwner.evaluate(`document.getElementById('p-book-note').value==='' && document.getElementById('p-book-minutes').value==='' && document.getElementById('practice-timer-display').textContent==='00:00'`),true);
   await g.navigate('/guest');await g.evaluate(`document.getElementById('guest-confirm-logout').click()`);
   await g.until(`document.readyState==='complete' && !!document.getElementById('guest-setup-form')`);
   await loginAccount('A');
   for (const next of ['', 'A', 'B']) {
     if(next!=='') await loginAccount('A');
     const delayed=await tab();let heldScript;
     await send('Network.setCacheDisabled',{cacheDisabled:true},delayed.sessionId);
     await send('Fetch.enable',{patterns:[{urlPattern:'*/static/js/session-boundary.js*'}]},delayed.sessionId);
     intercept=m=>{heldScript=m;};
     await send('Page.navigate',{url:config.origin+(next ? '/home' : '/account/privacy')},delayed.sessionId);
     for(let i=0;i<240 && !heldScript;i++)await new Promise(r=>setTimeout(r,25));
     assert.ok(heldScript,'old document boundary script intercepted');
     assert.equal(await delayed.evaluate(`document.body.dataset.accountId`),'WC-GUEST-A');
     assert.equal(await delayed.evaluate(`document.querySelector('.app-shell').hidden && !window.WWState`),true);
     await g.navigate('/guest');
     await g.evaluate(`document.getElementById('guest-confirm-logout').click()`);
     await g.until(`document.readyState==='complete' && !!document.getElementById('guest-setup-form')`);
     if(next) await loginAccount(next);
     const savedBefore=await g.evaluate(`localStorage.getItem('woodshedWoodchuckState.v1')`);
     await send('Fetch.continueRequest',{requestId:heldScript.params.requestId},delayed.sessionId);
     await delayed.until(`!!window.WWSessionBoundary`);
     assert.equal(await delayed.evaluate(`window.WWSessionBoundary.ready`),false);
     assert.equal(await delayed.evaluate(`document.querySelector('.app-shell').hidden && !window.WWState`),true);
     assert.equal(await delayed.evaluate(`getComputedStyle(document.querySelector('.app-shell')).display`),'none');
     const count=events.filter(e=>e.session===delayed.sessionId).length;
     assert.equal(await delayed.evaluate(`fetch('/teams',{method:'POST'}).then(()=>false,()=>true)`),true);
     assert.equal(events.filter(e=>e.session===delayed.sessionId).length,count);
     assert.equal(await g.evaluate(`localStorage.getItem('woodshedWoodchuckState.v1')`),savedBefore);
     await send('Fetch.disable',{},delayed.sessionId);intercept=null;
     delayedCases.push(next || 'signed out');
     if(next) {
       // Ordinary account logout also works after the verified script gate.
       await g.navigate('/store');
       await g.evaluate(`document.getElementById('authenticated-logout').click()`);
       await g.until(`location.pathname==='/' && document.readyState==='complete' && !document.body.dataset.accountId`);
     }
   }
   const proof={guest_application_requests:guestRequests,all_database_tables_unchanged_during_guest:true,all_database_rows_unchanged_during_guest:true,guest_storage:guestStorage,account_draft_timer_isolation:true,actual_guest_back_forward_cleared:true,actual_account_back_forward_isolated:true,
     tables_compared:Object.keys(before.counts).length,row_digests_compared:Object.keys(before.rows).length,old_tab_blocked:true,pending_logout_tab_blocked:true,delayed_document_cases:delayedCases,ordinary_logout_verified:true,failed_logout_stayed_signed_in:true,server_history_preserved:true,
     tools:['metronome real Web Audio','tuner synthetic microphone and context cleanup','local P-Chart Share Download Copy','local Plunge current score and replay'],guest_reload_and_discard:true,guest_bfcache_cleared:true,guest_chart_client_exports:true,guest_plunge_tampering_local_only:true,welcome_has_no_product_cache:true,accounts_switched:['B','A'],registered_history_completed:true};
   fs.writeFileSync(config.output+'/network.json',JSON.stringify(events,null,2));
   fs.writeFileSync(config.output+'/database-checkpoints.json',JSON.stringify({before,afterTools,afterLogout,final},null,2));
   fs.writeFileSync(config.output+'/guest-storage.json',JSON.stringify(guestStorage,null,2));
   console.log(JSON.stringify(proof));
 }catch(error){fs.writeFileSync(config.output+'/network-failure.json',JSON.stringify(events,null,2));console.error(error.stack||error);process.exitCode=1;}
 finally{clearTimeout(timer);chrome.kill();if(attackerServer)attackerServer.close();}
});
