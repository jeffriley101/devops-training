"""Run the real Medal Board renderer in Chromium with synthetic endpoint data."""
import json
from pathlib import Path
import shutil
import subprocess

from jinja2 import Template
import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
const {spawn}=require('node:child_process');
let input='';process.stdin.on('data',c=>input+=c);
process.stdin.on('end',async()=>{
 const c=JSON.parse(input), pending=new Map();let id=0,buffer='';
 const chrome=spawn(c.chrome,['--headless','--no-sandbox','--remote-debugging-pipe',
 '--no-first-run','--disable-background-networking','--disable-component-update',
 '--disable-sync','--host-resolver-rules=MAP * ~NOTFOUND','--user-data-dir='+c.profile],
 {stdio:['ignore','ignore','ignore','pipe','pipe']});
 const timer=setTimeout(()=>{chrome.kill();process.exit(2)},20000);
 const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{
  const seq=++id;pending.set(seq,{resolve,reject});
  chrome.stdio[3].write(JSON.stringify({id:seq,method,params,sessionId})+'\0');
 });
 chrome.stdio[4].on('data',chunk=>{
  buffer+=chunk;let end;
  while((end=buffer.indexOf('\0'))>=0){
   const m=JSON.parse(buffer.slice(0,end));buffer=buffer.slice(end+1);
   const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}
  }
 });
 try {
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
  const evaluate=async expression=>{
   const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);
   if(r.exceptionDetails)throw r.exceptionDetails;return r.result.value;
  };
  await send('Emulation.setDeviceMetricsOverride',{width:c.width,height:900,deviceScaleFactor:1,mobile:false},sessionId);
  await evaluate('document.documentElement.innerHTML='+JSON.stringify(c.html));
  await evaluate(c.script+`;window.WWPracticeDuration={minutes:n=>n+' min'};
   window.fetch=async url=>({ok:true,status:200,json:async()=>url.endsWith('/finalized')?
    {weeks:[{week_start:'2026-09-14',week_end:'2026-09-21',season:{name:'Back to School'}},
            {week_start:'2026-09-07',week_end:'2026-09-14',season:{name:'Band Camp'}}]}:
    {results:url.includes('2026-09-14')?[]:[{contest:{key:'team-lifetime-practice',name:'Lifetime'},
     division:'open',subject_type:'team',team_name:'Union',rank:1,medal:'gold',score:30.5}]}});
   wirePastWinners();`);
  await evaluate('new Promise(r=>setTimeout(r,20))');
  const empty=await evaluate(`({options:document.querySelectorAll('#past-winners-week option').length,
   value:document.getElementById('past-winners-week').value,
   visible:!document.getElementById('past-winners-content').classList.contains('hidden'),
   message:document.getElementById('past-winners-week-empty').innerText,
   noWeeks:!document.getElementById('past-winners-empty').classList.contains('hidden')})`);
  await evaluate(`document.getElementById('past-winners-week').value='2026-09-07';
   document.getElementById('past-winners-week').dispatchEvent(new Event('change'));
   new Promise(r=>setTimeout(r,20));`);
  const populated=await evaluate(`({rows:document.querySelectorAll('.medal-row').length,
   label:document.querySelector('.medal-row-subject strong').innerText,
   emptyHidden:document.getElementById('past-winners-week-empty').classList.contains('hidden')})`);
  console.log(JSON.stringify({empty,populated}));
 }catch(error){console.error(JSON.stringify(error));process.exitCode=1}
 finally{clearTimeout(timer);chrome.kill()}
});
'''


@pytest.mark.parametrize('width', [390, 1440])
def test_finalized_empty_week_stays_selectable_and_can_switch(tmp_path, width):
    chrome = shutil.which('google-chrome')
    if not chrome or not shutil.which('node'):
        pytest.skip('Chromium and Node required')
    template=(ROOT/'templates/quest.html').read_text()
    start=template.index('      <details\n        id="past-winners"')
    snippet=template[start:template.index('</details>',start)+len('</details>')]
    html='<head><style>.hidden{display:none!important}</style></head><body>'+Template(snippet).render()+'</body>'
    html=html.replace('id="past-winners"','open id="past-winners"',1)
    js=(ROOT/'static/js/app.js').read_text()
    script=js[js.index('  const BOARD_CONTEST_TITLES'):js.index('  function wireHallOfChampions()')]
    run=subprocess.run(['node','-e',DRIVER],input=json.dumps(dict(chrome=chrome,profile=str(tmp_path/'chrome'),
        width=width,html=html,script=script)),text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr
    data=json.loads(run.stdout)
    assert data['empty']['options']==2 and data['empty']['value']=='2026-09-14'
    assert data['empty']['visible'] and not data['empty']['noWeeks']
    assert 'This week is finalized.' in data['empty']['message']
    assert data['populated']=={'rows':1,'label':'🛡 Union','emptyHidden':True}
