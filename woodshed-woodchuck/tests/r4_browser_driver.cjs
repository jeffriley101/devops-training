// Run only with test_r4_browser.py's disposable server and synthetic accounts.
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
 const timer=setTimeout(()=>{chrome.kill();process.exit(2);},240000);
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
   const g=await tab(); let checks=0;
   await send('Page.enable',{},g.sessionId);
   await send('Page.addScriptToEvaluateOnNewDocument',{source: `
     window.appearanceFrames=[];
     const decode=HTMLImageElement.prototype.decode;
     HTMLImageElement.prototype.decode=async function(){await decode.call(this);if(this.matches('[data-student-woodchuck]'))await new Promise(r=>setTimeout(r,250));};
     let frames=0;
     function sample(){
       const image=document.querySelector('[data-student-woodchuck]');
       if(image)appearanceFrames.push({ready:image.hasAttribute('data-appearance-ready'),visible:getComputedStyle(image).visibility==='visible',src:image.currentSrc});
       if(++frames<600)requestAnimationFrame(sample);
     }
     requestAnimationFrame(sample);
   `},g.sessionId);
   await g.navigate('/guest/login');
   await g.evaluate(`fetch('/account/login',{method:'POST',body:new URLSearchParams({woodchuck_id:'WC-GUEST-A',pin:'2468'})}).then(r=>r.json())`);
   // Real Woodshed entry: the same XP/Streak panel opens only after arrival.
   await g.navigate('/');
   await g.until(`document.querySelector('.welcome-hero-art').complete`);
   await g.evaluate(`document.querySelector('[data-world-entry="woodshed"]').click()`);
   await g.until(`!!document.querySelector('.world-entry-overlay[open]')`);
   assert.equal(await g.evaluate(`location.pathname`),'/');checks++;
   await g.until(`location.pathname==='/home' && window.WWSurfaces?.current()?.id==='xp-panel'`);
   assert.equal(await g.evaluate(`document.body.classList.contains('world-entry-arrival') || !!document.querySelector('.world-entry-overlay[open]')`),false);checks++;
   assert.equal(await g.evaluate(`document.querySelector('#xp-panel #login-streak-card')!==null`),true);checks++;
   await g.until(`document.getElementById('login-streak-days').textContent==='1'`);checks++;
   assert.equal(await g.evaluate(`getComputedStyle(document.querySelector('.login-streak-crown-progress')).display`),'flex');checks++;
   assert.equal(await g.evaluate(`document.querySelector('.login-streak-crown-progress').getBoundingClientRect().height<40`),true);checks++;
   const xpShot=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
   fs.writeFileSync(config.output+'/entry-xp-streak.png',Buffer.from(xpShot.data,'base64'));
   await g.evaluate(`history.back()`);
   await g.until(`document.getElementById('xp-panel').hidden && document.activeElement.id==='xp-level-control'`);checks++;
   await g.navigate('/store');
   assert.equal(await g.evaluate(`!!document.getElementById('login-streak-card') || !!WWSurfaces.current()`),false);checks++;
   await g.navigate('/home');
   await g.until(`!!window.WWWoodchuck`);
   assert.equal(await g.evaluate(`document.getElementById('xp-panel').hidden`),true);checks++;
   await g.evaluate(`document.getElementById('xp-level-control').click()`);
   await g.until(`WWSurfaces.current()?.id==='xp-panel'`);
   await g.evaluate(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
   await g.until(`document.getElementById('xp-panel').hidden && document.activeElement.id==='xp-level-control'`);checks++;
   await g.evaluate(`localStorage.removeItem('woodshed:streak-popup:daily:v1:'+document.body.dataset.worldEntryAccount)`);
   await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]},g.sessionId);
   await g.navigate('/');
   await g.evaluate(`document.querySelector('[data-world-entry="woodshed"]').click()`);
   await g.until(`location.pathname==='/home' && window.WWSurfaces?.current()?.id==='xp-panel'`);
   assert.equal(await g.evaluate(`document.body.classList.contains('world-entry-arrival')`),false);checks++;
   await g.evaluate(`WWNavigation.dismissCurrent()`);
   await g.until(`document.getElementById('xp-panel').hidden && !history.state?.wwSurface`);
   assert.equal(await g.evaluate(`document.activeElement.id`),'xp-level-control');checks++;
   await send('Emulation.setEmulatedMedia',{features:[]},g.sessionId);
   for (const width of [390,1440]) {
     await send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<500},g.sessionId);
     await g.navigate('/home');
     await g.until(`!!window.WWWoodchuck && document.querySelector('[data-student-woodchuck]').hasAttribute('data-appearance-ready')`);
     assert.equal(await g.evaluate(`appearanceFrames.some(f=>!f.ready&&!f.visible)`),true);checks++;
     assert.equal(await g.evaluate(`appearanceFrames.some(f=>!f.ready&&f.visible)`),false);checks++;
     await g.evaluate(`document.getElementById('instrument-object').click()`);
     await g.until(`document.getElementById('your-woodchuck').open && document.activeElement.id==='woodchuck-instrument'`);
     assert.equal(await g.evaluate(`document.getElementById('your-woodchuck').getBoundingClientRect().bottom <= innerHeight`),true); checks++;
     assert.equal(await g.evaluate(`document.getElementById('woodchuck-instrument').options.length`),16);checks++;
     await g.evaluate(`document.getElementById('woodchuck-instrument').value='Trumpet';document.getElementById('woodchuck-hoodie').value='purple';document.getElementById('woodchuck-hat').value='pink';document.querySelector('#woodchuck-editor-form [type=submit]').click()`);
     await g.until(`document.getElementById('woodchuck-editor-status').textContent==='Your Woodchuck is saved.'`);
     assert.equal(await g.evaluate(`document.querySelector('[data-student-woodchuck]').src.startsWith('data:image/png')`),true); checks++;
     await g.evaluate(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
     await g.until(`!document.getElementById('your-woodchuck').open && document.activeElement.id==='instrument-object'`);checks++;
     await g.evaluate(`document.getElementById('instrument-object').click()`);
     await g.until(`document.getElementById('your-woodchuck').open && history.state.wwSurface==='your-woodchuck'`);
     await g.evaluate(`history.back()`);
     await g.until(`!document.getElementById('your-woodchuck').open && location.pathname==='/home'`);checks++;
     // Dirty forms retain edits on cancelled Back; accepting asks just once.
     await g.evaluate(`window.confirmCount=0;window.confirm=()=>{confirmCount++;return false};document.getElementById('instrument-object').click()`);
     await g.until(`document.getElementById('your-woodchuck').open && history.state.wwSurface==='your-woodchuck'`);
     await g.evaluate(`document.getElementById('woodchuck-hat').value='red';history.back()`);
     await g.until(`confirmCount===1 && history.state.wwSurface==='your-woodchuck'`);
     assert.equal(await g.evaluate(`document.getElementById('your-woodchuck').open`),true);checks++;
     await g.evaluate(`window.confirm=()=>{confirmCount++;return true};history.back()`);
     await g.until(`!document.getElementById('your-woodchuck').open`);
     assert.equal(await g.evaluate(`confirmCount`),2);checks++;
     // A pending save cannot be dismissed, and needs no follow-up state GET.
     await g.evaluate(`document.getElementById('instrument-object').click();window.realFetch=window.fetch;window.fetch=(url,init)=>url==='/account/appearance'?new Promise(resolve=>window.finishAppearance=()=>resolve(realFetch(url,init))):realFetch(url,init);document.querySelector('#woodchuck-editor-form [type=submit]').click()`);
     await g.until(`document.getElementById('your-woodchuck').dataset.busy==='true'`);
     assert.equal(await g.evaluate(`WWNavigation.dismissCurrent();document.getElementById('your-woodchuck').open`),true);checks++;
     await g.evaluate(`window.fetch=realFetch;finishAppearance()`);
     await g.until(`document.getElementById('woodchuck-editor-status').textContent==='Your Woodchuck is saved.'`);
     assert.equal(await g.evaluate(`WWState.getState().appearance.hat`),'pink');checks++;
     await g.evaluate(`WWNavigation.dismissCurrent()`);
     await g.until(`history.state?.wwSurface!=='your-woodchuck'`);
     await g.evaluate(`document.getElementById('metronome-open-button').click()`);
     await g.until(`window.WWSurfaces.current()?.id==='metronome-panel'`);
     assert.equal(await g.evaluate(`document.getElementById('metronome-panel').getBoundingClientRect().top < innerHeight`),true);checks++;
     await g.evaluate(`document.getElementById('metronome-start-button').click()`);
     await g.until(`document.getElementById('metronome-start-button').textContent==='Stop'`);
     await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))`);
     await g.until(`document.getElementById('metronome-start-button').textContent==='Start'`);checks++;
     await g.until(`Tone.getContext().rawContext.state!=='running'`);checks++;
     await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))`);
     assert.equal(await g.evaluate(`Tone.getContext().rawContext.state!=='running' && document.getElementById('metronome-start-button').textContent==='Start'`),true);checks++;
     await g.evaluate(`window.WWNavigation.dismissCurrent()`);
     await g.until(`document.getElementById('metronome-panel').classList.contains('hidden')`);checks++;
     await g.evaluate(`window.captured=[];window.realGetUserMedia=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async opts=>{const stream=await realGetUserMedia(opts);captured.push(stream);return stream};document.getElementById('tuner-open-button').click()`);
     await g.until(`captured.length===1 && !document.getElementById('tuner-panel').hidden`);
     await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))`);
     await g.until(`document.getElementById('tuner-panel').hidden`);checks++;
     assert.equal(await g.evaluate(`captured.every(s=>s.getTracks().every(t=>t.readyState==='ended'))`),true);checks++;
     await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))`);
     assert.equal(await g.evaluate(`document.getElementById('tuner-panel').hidden`),true);checks++;
     assert.equal(await g.evaluate(`captured.length`),1);checks++;
     // Permission resolving after backgrounding must also release its track.
     await g.evaluate(`navigator.mediaDevices.getUserMedia=opts=>new Promise(resolve=>window.releasePermission=async()=>{const stream=await realGetUserMedia(opts);captured.push(stream);resolve(stream)});document.getElementById('tuner-open-button').click()`);
     await g.until(`!!window.releasePermission`);
     await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));releasePermission()`);
     await g.until(`captured.length===2 && captured[1].getTracks().every(t=>t.readyState==='ended')`);checks++;
     await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))`);
     await g.navigate('/store');await g.until(`!!window.WWColorizer`);
     assert.equal(await g.evaluate(`Number(getComputedStyle(document.querySelector('.shop-student-character-layer')).zIndex) > Number(getComputedStyle(document.querySelector('.shop-viking-art')).zIndex)`),true);checks++;
     await g.until(`document.querySelector('[data-student-woodchuck]').hasAttribute('data-appearance-ready')`);
     const shopImage=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
     fs.writeFileSync(config.output+'/r4-shop-'+width+'.png',Buffer.from(shopImage.data,'base64'));
     assert.equal(await g.evaluate(`JSON.parse(document.getElementById('woodchuck-appearance-data').textContent).instrument`),'Trumpet');checks++;
     assert.equal(await g.evaluate(`!!document.querySelector('a[href*="brassspectrogram"]')`),false);checks++;
     await g.evaluate(`document.querySelector('[data-shop-panel="practice-room"]').click()`);
     await g.until(`document.getElementById('shop-feature-dialog').open`);
     const image=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
     fs.writeFileSync(config.output+'/r4-'+width+'.png',Buffer.from(image.data,'base64'));
     assert.equal(await g.evaluate(`document.querySelectorAll('.practice-room-door').length`),4); checks++;
     await g.evaluate(`window.WWNavigation.dismissCurrent()`);
     await g.until(`!document.getElementById('shop-feature-dialog').open`);
     assert.equal(await g.evaluate(`document.activeElement.dataset.shopPanel`),'practice-room');checks++;
     await g.until(`history.state?.wwSurface!=='shop-feature-dialog'`);
     await g.evaluate(`document.querySelector('[data-shop-panel="gear"]').click()`);
     await g.until(`!!document.querySelector('[data-shop-panel-content="gear"] [data-shop-buy]:not(:disabled)')`);
     await g.evaluate(`document.querySelector('[data-shop-panel-content="gear"] [data-shop-buy]').click()`);
     await g.until(`document.getElementById('shop-purchase-confirmation').open && history.state.wwSurface==='shop-purchase-confirmation'`);
     assert.equal(await g.evaluate(`document.getElementById('shop-purchase-confirm').disabled=true;WWNavigation.dismissCurrent();document.getElementById('shop-purchase-confirmation').open`),true);checks++;
     await g.evaluate(`document.getElementById('shop-purchase-confirm').disabled=false;WWNavigation.dismissCurrent()`);
     await g.until(`!document.getElementById('shop-purchase-confirmation').open && history.state.wwSurface==='shop-feature-dialog'`);
     assert.equal(await g.evaluate(`document.activeElement.hasAttribute('data-shop-buy')`),true);checks++;
     await g.evaluate(`history.back()`);
     await g.until(`!document.getElementById('shop-feature-dialog').open`);checks++;
     assert.equal(await g.evaluate(`!!document.getElementById('your-woodchuck') || !!window.WWWoodchuck`),false);checks++;

   }
   // R4A geometry: artwork and grid share one uncropped rectangle at every size.
   const layouts=[[320,568],[390,844],[430,932],[768,1024],[1024,768],[844,390],[1440,900]];
   for (const [width,height] of layouts) {
     await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<500},g.sessionId);
     for (const path of ['/home','/store']) {
       await g.navigate(path);
       await g.until(`document.querySelector('.room-scene-art')?.complete && document.querySelector('.room-scene-art').naturalWidth>0`);
       await g.until(`document.querySelector('[data-student-woodchuck]').hasAttribute('data-appearance-ready')`);
       const proof=await g.evaluate(`(()=>{
         const scene=document.querySelector('.artwork-scene'), art=scene.querySelector('.room-scene-art'), grid=scene.querySelector('.scene-hotspots');
         const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom}};
         const s=rect(scene);
         return {scene:s,art:rect(art),grid:rect(grid),ratio:art.naturalWidth/art.naturalHeight,nav:rect(document.querySelector('.main-nav')),
           overflow:document.documentElement.scrollWidth>innerWidth,bodyOverflow:document.documentElement.scrollHeight>innerHeight,visibleStreak:!!document.getElementById('login-streak-card')?.getClientRects().length,footer:!!document.querySelector('a[href=\"/family/practice\"]'),
           characterPointer:getComputedStyle(scene.querySelector('[data-presentation-only]')).pointerEvents,
           cells:Array.from(grid.children).map(n=>{const r=rect(n),hit=document.elementFromPoint(r.x+r.w*.5,r.y+r.h*.5);return {
             cell:n.dataset.sceneCell,rect:r,label:n.getAttribute('aria-label'),tag:n.tagName,tab:n.tabIndex,
             hit:hit===n||n.contains(hit),color:getComputedStyle(n).backgroundColor,font:getComputedStyle(n).fontSize};})};
       })()`);
       assert.equal(proof.overflow,false);assert.equal(proof.characterPointer,'none');
       assert.ok(Math.abs(proof.scene.x+proof.scene.w/2-width/2)<.6);checks++;
       assert.equal(proof.bodyOverflow,false,JSON.stringify({path,width,height,proof}));checks++;
       assert.equal(proof.footer,false);checks++;
       assert.equal(proof.visibleStreak,false);checks++;
       assert.ok(Math.abs(proof.scene.h-Math.min(proof.nav.y,width/proof.ratio))<.6);checks++;
       assert.equal(await g.evaluate(`appearanceFrames.some(f=>!f.ready&&f.visible)`),false);checks++;
       assert.equal(await g.evaluate(`document.querySelector('[data-student-woodchuck]').src.startsWith('data:image/png')`),true);checks++;
       assert.ok(proof.scene.b<=proof.nav.y+.6,JSON.stringify({path,width,height,proof}));
       assert.ok(Math.abs(proof.scene.w/proof.scene.h-proof.ratio)<.002);
       for(const key of ['x','y','w','h']) {
         assert.ok(Math.abs(proof.scene[key]-proof.grid[key])<.6);
         assert.ok(Math.abs(proof.scene[key]-proof.art[key])<.6);
       }
       assert.equal(proof.cells.length,10);
       proof.cells.forEach((c,i)=>{
         assert.equal(c.cell,(i%2?'R':'L')+(Math.floor(i/2)+1));
         assert.ok(c.label && c.tab===0 && ['BUTTON','A'].includes(c.tag));
         assert.ok(c.hit,JSON.stringify({path,width,height,c}));
         assert.equal(c.font,'0px');assert.equal(c.color,'rgba(0, 0, 0, 0)');
         assert.ok(Math.abs(c.rect.w-proof.scene.w*.5)<.6);
         assert.ok(Math.abs(c.rect.h-proof.scene.h*.2)<.6);
         assert.ok(Math.abs(c.rect.x-proof.scene.x-(i%2)*proof.scene.w*.5)<.6);
         assert.ok(Math.abs(c.rect.y-proof.scene.y-Math.floor(i/2)*proof.scene.h*.2)<.6);
       });checks+=10;
       const shot=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
       fs.writeFileSync(config.output+'/r4a-'+path.slice(1)+'-'+width+'x'+height+'.png',Buffer.from(shot.data,'base64'));
       await g.evaluate(`document.querySelector('[data-scene-cell="L1"]').focus()`);
       await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9},g.sessionId);
       await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9},g.sessionId);
       assert.equal(await g.evaluate(`document.activeElement.dataset.sceneCell`),'R1');checks++;
       assert.equal(await g.evaluate(`getComputedStyle(document.activeElement).outlineStyle`),'solid');checks++;
       if(path==='/home' && [320,390,1440].includes(width)) {
         await g.evaluate(`document.getElementById('shed-decorate-button').focus();document.getElementById('shed-decorate-button').click()`);
         await g.until(`document.body.classList.contains('stickerbook-open') && document.querySelector('[data-decoration-size="large"]:not(:disabled)')`);
         const workspace=await g.evaluate(`(()=>{const p=document.getElementById('shed-decorate-panel'),s=document.querySelector('.artwork-scene'),r=p.getBoundingClientRect();return {position:getComputedStyle(p).position,modal:p.hasAttribute('aria-modal')||p.hasAttribute('data-room-panel'),current:WWSurfaces.current()?.id||null,scroll:document.documentElement.scrollHeight>innerHeight,below:r.top>=s.getBoundingClientRect().bottom,visible:r.top<innerHeight-72,inert:s.inert,sceneHeight:s.getBoundingClientRect().height,focus:document.activeElement.id};})()`);
         assert.equal(await g.evaluate(`getComputedStyle(document.getElementById('shed-stickerbook-title')).color`),'rgb(36, 53, 27)');checks++;
         assert.equal(workspace.position,'static');assert.equal(workspace.modal,false);assert.equal(workspace.current,null);
         assert.equal(workspace.scroll,true);assert.equal(workspace.below,true);assert.equal(workspace.visible,true);assert.equal(workspace.inert,false);
         assert.ok(Math.abs(workspace.sceneHeight-proof.scene.h)<.6);assert.equal(workspace.focus,'shed-decorate-close');checks+=9;
         assert.equal(await g.evaluate(`WWNavigation.dismissCurrent()`),false);checks++;
         await g.evaluate(`document.querySelector('[data-decoration-size="large"]').click()`);
         await g.until(`document.querySelector('[data-decoration-size="large"][aria-pressed="true"]:not(:disabled)')`);
         const inventory=()=>g.evaluate(`fetch('/store/inventory').then(r=>r.json()).then(p=>p.items.find(i=>i.item_key==='candle'))`);
         assert.equal((await inventory()).placement_size,'large');checks++;
         await g.evaluate(`document.getElementById('shed-decoration-view-room').click()`);
         assert.ok(await g.evaluate(`document.querySelector('.artwork-scene').getBoundingClientRect().top>=-.6`),JSON.stringify(await g.evaluate(`({top:document.querySelector('.artwork-scene').getBoundingClientRect().top,scroll:scrollY})`)));
         const before=await inventory();
         const drag=await g.evaluate(`(()=>{const r=document.querySelector('.shed-decoration').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
         await send('Input.dispatchMouseEvent',{type:'mousePressed',x:drag.x,y:drag.y,button:'left',clickCount:1},g.sessionId);
         await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:drag.x+12,y:drag.y+15,button:'left',buttons:1},g.sessionId);
         await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:drag.x+12,y:drag.y+15,button:'left',clickCount:1},g.sessionId);
         await g.until(`!document.querySelector('[data-decoration-size="large"]').disabled`);
         const moved=await inventory();assert.ok(moved.placement_x>before.placement_x && moved.placement_y>before.placement_y);checks++;
         await g.evaluate(`document.getElementById('shed-decorate-panel').scrollIntoView({block:'start'})`);
         const shot=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
         fs.writeFileSync(config.output+'/workspace-'+width+'.png',Buffer.from(shot.data,'base64'));
         await g.evaluate(`document.getElementById('shed-decorate-close').click()`);
         await g.until(`!document.body.classList.contains('stickerbook-open') && scrollY===0`);
         assert.equal(await g.evaluate(`document.documentElement.scrollHeight<=innerHeight && document.activeElement.id==='shed-decorate-button'`),true);checks++;
         await g.evaluate(`document.getElementById('shed-decorate-button').click()`);
         await g.until(`document.querySelector('[data-decoration-size="large"][aria-pressed="true"]:not(:disabled)')`);
         const saved=await inventory();assert.equal(saved.placement_x,moved.placement_x);assert.equal(saved.placement_y,moved.placement_y);checks+=2;
         // Display toggle preserves ownership and can place the same copy again.
         await g.evaluate(`document.querySelector('[data-decoration-display-toggle]').click()`);
         await g.until(`!document.querySelector('[data-decoration-display-toggle]').checked && !document.querySelector('[data-decoration-display-toggle]').disabled`);
         assert.equal((await inventory()).placement_x,null);checks++;
         await g.evaluate(`document.querySelector('[data-decoration-display-toggle]').click()`);
         await g.until(`document.querySelector('[data-decoration-display-toggle]').checked && !document.querySelector('[data-decoration-display-toggle]').disabled`);
         assert.equal((await inventory()).id,moved.id);checks++;
         await g.evaluate(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
         await g.until(`!document.body.classList.contains('stickerbook-open') && scrollY===0`);
         assert.equal(await g.evaluate(`document.documentElement.scrollHeight<=innerHeight && document.activeElement.id==='shed-decorate-button'`),true);checks++;
       }
       if (width===390) {
         const actions=path==='/home'
           ? [['woodchuck-name-value','change-name-panel'],['shed-team-button','shed-team-panel'],['level-value','change-level-panel'],['xp-level-control','xp-panel'],['mum-open-button','mum-panel'],['sound-effects-button','sound-effects-panel'],['shed-secret-button','shed-secret-panel']]
           : ['crown','little-buddy','goat','share','artist','practice-definition'].map(key=>['[data-shop-panel="'+key+'"]','shop-feature-dialog']);
         for (const [control,panel] of actions) {
           const selector=control.startsWith('[')?control:'#'+control;
           await g.evaluate(`document.querySelector(${JSON.stringify(selector)}).focus();document.querySelector(${JSON.stringify(selector)}).click()`);
           await g.until(`WWSurfaces.current()?.id===${JSON.stringify(panel)}`);
           assert.equal(await g.evaluate(`(()=>{const p=document.getElementById(${JSON.stringify(panel)}),r=p.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&getComputedStyle(p).position==='fixed'&&getComputedStyle(p).overflowY==='auto'&&document.documentElement.scrollHeight<=innerHeight})()`),true, panel);checks++;
           const panelShot=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
           fs.writeFileSync(config.output+'/smoke-'+panel+'.png',Buffer.from(panelShot.data,'base64'));
           if (panel==='shed-team-panel') {
             const close=await g.evaluate(`(()=>{const b=document.getElementById('shed-team-close'),r=b.getBoundingClientRect(),p=document.getElementById('shed-team-panel').getBoundingClientRect();return {label:b.getAttribute('aria-label'),text:b.textContent,type:b.type,x:r.x+r.width/2,y:r.y+r.height/2,visible:r.width>0&&r.height>0&&r.top>=p.top&&r.bottom<=innerHeight,upperRight:r.x>p.x+p.width/2&&r.top<p.top+80};})()`);
             assert.equal(close.label,'Close Team panel');assert.equal(close.text,'×');assert.equal(close.type,'button');
             assert.ok(close.visible&&close.upperRight);checks+=4;
             assert.equal(await g.evaluate(`document.getElementById('shed-team-button').dataset.sceneCell`),'R1');checks++;
             // Clicking outside must leave the panel and draft input intact.
             await g.evaluate(`document.getElementById('shed-team-name').value='Unsubmitted draft'`);
             await send('Input.dispatchMouseEvent',{type:'mousePressed',x:2,y:2,button:'left',clickCount:1},g.sessionId);
             await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:2,y:2,button:'left',clickCount:1},g.sessionId);
             assert.equal(await g.evaluate(`WWSurfaces.current()?.id`),'shed-team-panel');checks++;
             for (const method of ['button','Escape','Back']) {
               if (method==='button') {
                 await send('Input.dispatchMouseEvent',{type:'mousePressed',x:close.x,y:close.y,button:'left',clickCount:1},g.sessionId);
                 await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:close.x,y:close.y,button:'left',clickCount:1},g.sessionId);
               } else if (method==='Escape') {
                 await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},g.sessionId);
                 await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},g.sessionId);
               } else await g.evaluate(`history.back()`);
               await g.until(`document.getElementById('shed-team-panel').hidden && !WWSurfaces.current() && !history.state?.wwSurface`);
               assert.equal(await g.evaluate(`document.activeElement.id`),'shed-team-button');checks++;
               assert.equal(await g.evaluate(`document.getElementById('shed-team-button').getAttribute('aria-expanded')`),'false');checks++;
               await g.evaluate(`document.getElementById('shed-team-button').click()`);
               await g.until(`WWSurfaces.current()?.id==='shed-team-panel' && history.state?.wwSurface==='shed-team-panel'`);
               assert.equal(await g.evaluate(`document.getElementById('shed-team-name').value`),'Unsubmitted draft');checks++;
             }
             await g.evaluate(`document.getElementById('shed-team-name').value=''`);
           }
           await g.evaluate(`WWNavigation.dismissCurrent()`);
           await g.until(`WWSurfaces.current()?.id!==${JSON.stringify(panel)} && !history.state?.wwSurface`);
           assert.equal(await g.evaluate(`document.activeElement===document.querySelector(${JSON.stringify(selector)})`),true);checks++;
         }
         if (path==='/store') {
           await send('Page.bringToFront',{},g.sessionId);
           await g.evaluate(`document.getElementById('dandelion-object').focus()`);
           await send('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32},g.sessionId);
           await send('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32},g.sessionId);
           await g.until(`document.querySelectorAll('.shop-dandelion-balance-burst').length===1`);checks++;
         }
       }
     }
     await g.navigate('/arcade');
     await g.evaluate(`document.querySelector('.rhythm-baseball-cabinet').scrollIntoView({block:'center'})`);
     await g.until(`document.querySelector('.rhythm-baseball-cabinet').naturalWidth===1024`);
     const arcade=await g.evaluate(`(()=>{const img=document.querySelector('.rhythm-baseball-cabinet'),r=img.getBoundingClientRect(),standings=document.querySelector('.rhythm-baseball-standings').getBoundingClientRect(),style=getComputedStyle(document.body);return {ratio:r.width/r.height,top:r.top,bottom:standings.bottom,fit:getComputedStyle(img).objectFit,wallpaper:style.backgroundImage,repeat:style.backgroundRepeat,size:style.backgroundSize,animation:style.animationName,link:!!img.closest('a'),overflow:document.documentElement.scrollWidth>innerWidth}})()`);
     assert.ok(Math.abs(arcade.ratio-2/3)<.002);assert.ok(arcade.bottom<=arcade.top+.6);
     assert.equal(arcade.fit,'contain');assert.equal(arcade.link,false);assert.equal(arcade.overflow,false);
     assert.ok(arcade.wallpaper.includes('repeating-linear-gradient')&&!arcade.wallpaper.includes('url('));
     assert.ok(arcade.repeat.split(',').every(value=>value.trim()==='no-repeat'));
     assert.ok(arcade.size.split(',').every(value=>value.trim()==='auto'));
     assert.equal(arcade.animation,'none');checks+=9;
     const shot=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
     fs.writeFileSync(config.output+'/r4a-arcade-'+width+'x'+height+'.png',Buffer.from(shot.data,'base64'));
     // Inspect the actual tall page across former tile boundaries and through its end.
     if ([320,390,430,768,1440].includes(width)) {
       const maximum=await g.evaluate(`document.documentElement.scrollHeight-innerHeight`);
       assert.ok(maximum>height,'Arcade must span multiple viewports for seam checks');checks++;
       for (const [index,y] of [0,720,1440,maximum/2,maximum].entries()) {
         await g.evaluate(`scrollTo({top:${y},behavior:'instant'});new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
         const wallpaper=await g.evaluate(`(()=>{const s=getComputedStyle(document.body);return {image:s.backgroundImage,size:s.backgroundSize,repeat:s.backgroundRepeat,animation:s.animationName};})()`);
         assert.equal(wallpaper.image,arcade.wallpaper);
         assert.equal(wallpaper.size,arcade.size);
         assert.equal(wallpaper.repeat,arcade.repeat);
         assert.equal(wallpaper.animation,'none');checks+=4;
         const seamShot=await send('Page.captureScreenshot',{format:'png'},g.sessionId);
         fs.writeFileSync(config.output+'/arcade-seams-'+width+'-'+index+'.png',Buffer.from(seamShot.data,'base64'));
       }
     }
   }
   assert.equal(events.some(e=>e.url.endsWith('/account/appearance')&&e.method==='GET'),false);checks++;
   // Distinct shared recovery states, and no loader on a completed fast operation.
   assert.equal(await g.evaluate(`WWRecovery.begin();WWRecovery.clear();!!document.querySelector('.shared-recovery')`),false);checks++;
   for(const [status,kind] of [[401,'session'],[503,'server'],[400,'feature']]) {
     assert.equal(await g.evaluate(`WWRecovery.failure({status:${status}});document.querySelector('.shared-recovery').dataset.recovery`),kind);checks++;
   }
   assert.equal(await g.evaluate(`Object.defineProperty(navigator,'onLine',{configurable:true,value:false});WWRecovery.failure(new TypeError('network'));document.querySelector('.shared-recovery').dataset.recovery`),'offline');checks++;
   // Real preflight failures leave actionable content, never initialize account consumers.
   for(const [status,kind] of [[503,'server'],[401,'session']]) {
     intercept=m=>send('Fetch.fulfillRequest',{requestId:m.params.requestId,responseCode:status,
       responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from('{}').toString('base64')},m.sessionId);
     await send('Fetch.enable',{patterns:[{urlPattern:'*/account/me'}]},g.sessionId);
     await send('Page.navigate',{url:config.origin+'/home'},g.sessionId);
     await g.until(`document.querySelector('.shared-recovery')?.dataset.recovery==='${kind}'`);
     assert.equal(await g.evaluate(`!!window.WWState`),false);checks++;
     assert.equal(await g.evaluate(`document.querySelector('.app-shell').hidden`),true);checks++;
     await send('Fetch.disable',{},g.sessionId);
   }
   intercept=null;
   // Pristine capture stops without saving, and a zero-time session can be restarted.
   await g.navigate('/practice/pristine');
   const beforePractice=await snapshot();
   await g.evaluate(`window.streams=[];window.gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async opts=>{const s=await gum(opts);streams.push(s);return s};document.querySelector('[data-pristine-start]').click()`);
   await g.until(`streams.length===1 && document.querySelector('[data-pristine-start]').hidden`);
   await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))`);
   await g.until(`streams[0].getTracks().every(t=>t.readyState==='ended')`);checks++;
   await g.evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))`);
   assert.equal(await g.evaluate(`streams.length===1 && !document.querySelector('[data-pristine-start]').hidden`),true);checks++;
   const afterPractice=await snapshot();
   assert.deepEqual(afterPractice.states,beforePractice.states);checks++;
   assert.equal(events.some(e=>e.method==='POST' && e.url.includes('/practice-charts/pristine')),false);checks++;
   await g.navigate('/p-book');
   const beforeBook=await snapshot();
   await g.evaluate(`document.getElementById('practice-timer-start-btn').click();Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))`);
   assert.deepEqual((await snapshot()).states,beforeBook.states);checks++;
   assert.equal(events.some(e=>e.method==='POST' && e.url.includes('/practice-charts')),false);checks++;
   console.log(JSON.stringify({viewports:[390,1440],layouts,checks}));
 }catch(error){console.error(error.stack||error);process.exitCode=1;}
 finally{clearTimeout(timer);chrome.kill();}
});
