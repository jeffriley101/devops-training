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
 let seq=0,buffer=''; const pending=new Map();const events=[];let intercept=null;
 const timer=setTimeout(()=>{chrome.kill();process.exit(2);},100000);
 const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{
   const id=++seq;pending.set(id,{resolve,reject});chrome.stdio[3].write(JSON.stringify({id,method,params,sessionId})+'\0');
 });
 chrome.stdio[4].on('data',chunk=>{
   buffer+=chunk;let end;
   while((end=buffer.indexOf('\0'))>=0){
     const m=JSON.parse(buffer.slice(0,end));buffer=buffer.slice(end+1);
     if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}
     else if(m.method==='Network.requestWillBeSent')events.push({session:m.sessionId,url:m.params.request.url,method:m.params.request.method});
     else if(m.method==='Fetch.requestPaused' && intercept)intercept(m);
   }
 });
 const tab=async()=>{
   const {targetId}=await send('Target.createTarget',{url:'about:blank'});
   const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
   await send('Network.enable',{},sessionId);
   const evaluate=async(expression)=>{
     const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true},sessionId);
     if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;
   };
   const until=async(expression)=>{for(let i=0;i<240;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,50));}throw new Error('Timeout: '+expression);};
   const navigate=async pathname=>{await send('Page.navigate',{url:config.origin+pathname},sessionId);await until(`document.readyState==='complete' && location.pathname===${JSON.stringify(pathname)} && !!window.WWSessionBoundary && window.WWSessionBoundary.isCurrent()`);await evaluate('window.WWSessionBoundary.ready');};
   return {sessionId,evaluate,until,navigate};
 };
 const snapshot=()=>fetch(config.origin+'/test/snapshot').then(r=>r.json());
 try{
   const before=await snapshot();const g=await tab();await g.navigate('/guest');
   const guestStart=events.length;
   await g.evaluate(`localStorage.setItem('woodshedWoodchuckState.v1','saved-account-cache');localStorage.setItem('woodshedWoodchuckMetronomeBpm','88');sessionStorage.setItem('woodshed:p-book:verifier-draft:v1','account draft');`);
   await g.evaluate(`for(const s of document.querySelectorAll('#guest-setup-form select'))s.value=s.options[1].value;document.querySelector('#guest-setup-form button').click();`);
   await g.until(`!document.getElementById('guest-tools').hidden`);
   await g.evaluate(`document.getElementById('metronome-open-button').click();document.getElementById('metronome-start-button').click();`);
   await g.until(`document.getElementById('metronome-start-button').textContent==='Stop'`);
   assert.equal(await g.evaluate(`document.getElementById('metronome-bpm-input').value`),'120');
   await g.evaluate(`const bpm=document.getElementById('metronome-bpm-input');bpm.value='132';bpm.dispatchEvent(new Event('change'));document.getElementById('metronome-start-button').click();`);
   await g.evaluate(`window.guestStreams=[];const gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async(...args)=>{const s=await gum(...args);guestStreams.push(s);return s;};document.getElementById('tuner-open-button').click();`);
   await g.until(`guestStreams.length===1 && document.getElementById('tuner-diagnosis').textContent!=='REQUESTING MICROPHONE'`);
   assert.equal(await g.evaluate(`guestStreams[0].getTracks()[0].readyState`),'live');
   await g.evaluate(`document.getElementById('tuner-close-button').click()`);
   assert.equal(await g.evaluate(`guestStreams[0].getTracks()[0].readyState`),'ended');
   const afterTools=await snapshot();assert.deepEqual(afterTools,before);
   await send('Page.reload',{},g.sessionId);await g.until(`document.readyState==='complete' && !!document.getElementById('guest-tools') && !document.getElementById('guest-tools').hidden`);
   assert.equal(await g.evaluate(`document.getElementById('metronome-bpm-input').value`),'132');
   assert.equal(await g.evaluate(`localStorage.getItem('woodshedWoodchuckMetronomeBpm')`),'88');
   await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true},g.sessionId);
   const image=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},g.sessionId);
   fs.writeFileSync(config.output+'/guest-mobile.png',Buffer.from(image.data,'base64'));
   await g.evaluate(`document.getElementById('guest-discard').click()`);
   assert.equal(await g.evaluate(`Object.keys(localStorage).some(k=>k.startsWith('woodshed:guest:v1:'))`),false);
   assert.equal(await g.evaluate(`localStorage.getItem('woodshedWoodchuckState.v1')`),'saved-account-cache');
   assert.equal(await g.evaluate(`sessionStorage.getItem('woodshed:p-book:verifier-draft:v1')`),'account draft');
   const guestRequests=events.slice(guestStart).filter(e=>new URL(e.url).pathname!=='/guest' && !new URL(e.url).pathname.startsWith('/static/') && !new URL(e.url).pathname.startsWith('/favicon'));
   assert.deepEqual(guestRequests,[]);
   assert.deepEqual(await snapshot(),before);
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
   const proof={guest_application_requests:guestRequests,all_database_tables_unchanged_during_guest:true,
     tables_compared:Object.keys(before.counts).length,old_tab_blocked:true,pending_logout_tab_blocked:true,delayed_document_cases:delayedCases,ordinary_logout_verified:true,failed_logout_stayed_signed_in:true,server_history_preserved:true,
     tools:['metronome real Web Audio','tuner synthetic microphone with track cleanup'],guest_reload_and_discard:true,accounts_switched:['B','A'],registered_history_completed:true};
   fs.writeFileSync(config.output+'/network.json',JSON.stringify(events,null,2));
   fs.writeFileSync(config.output+'/database-checkpoints.json',JSON.stringify({before,afterTools,afterLogout,final},null,2));
   console.log(JSON.stringify(proof));
 }catch(error){console.error(error.stack||error);process.exitCode=1;}
 finally{clearTimeout(timer);chrome.kill();}
});
