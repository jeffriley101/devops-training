"""Ordinary browser integration; controlled tool audio is NOT physical-device QA."""
import json
import time
import pytest
from playwright.sync_api import sync_playwright
from test_overhaul_profile_saves_browser import save_site
from test_weekly2_game_mode_entry_browser import login, chooser, close_chooser, authenticated, pump_until
from test_weekly2_game_mode_browser import account, snapshot, open_editor

AUDIO_PROBE = r'''(()=>{
 const Native=window.AudioContext||window.webkitAudioContext;
 const p=window.toolProbe={contexts:[],voices:[],mic:[],tracks:[],intervals:[],frequency:440,denyMic:false,failAudio:false};
 const interval=window.setInterval.bind(window),clear=window.clearInterval.bind(window);
 window.setInterval=(fn,ms,...args)=>{const id=interval(fn,ms,...args);if(ms===25&&fn.name==='scheduler')p.intervals.push(id);return id};
 window.clearInterval=id=>{p.intervals=p.intervals.filter(x=>x!==id);return clear(id)};
 class Controlled {
  constructor(){this.state='suspended';this.sampleRate=48000;this.destination={};p.contexts.push(this);}
  get currentTime(){return performance.now()/1000}
  resume(){if(p.failAudio){p.failAudio=false;return Promise.reject(new DOMException('Synthetic audio start denial','NotAllowedError'))}this.state='running';return Promise.resolve()}
  suspend(){this.state='suspended';return Promise.resolve()}
  close(){this.state='closed';return Promise.resolve()}
  createMediaStreamSource(){return {connect(){},disconnect(){}}}
  createAnalyser(){return {fftSize:4096,disconnect(){},getFloatTimeDomainData(a){for(let i=0;i<a.length;i++)a[i]=.5*Math.sin(2*Math.PI*p.frequency*i/48000)}}}
  createOscillator(){const v={type:'',frequency:{setValueAtTime(x){v.hz=x}},connect(){},disconnect(){},start(t){v.time=t;p.voices.push(v)},stop(t){setTimeout(()=>v.onended?.(),Math.max(0,((t??this.currentTime)-performance.now()/1000)*1000))}};this.voice=v;return v}
  createGain(){const v=this.voice;return {connect(){},disconnect(){},gain:{setValueAtTime(){},exponentialRampToValueAtTime(x){if(x>.0001)v.peak=x}}}}
 }
 function Context(...args){if(/startMetronome|startTuner/.test(new Error().stack))return new Controlled();return new Native(...args)}
 if(Native){Context.prototype=Native.prototype;window.AudioContext=Context;if(window.webkitAudioContext)window.webkitAudioContext=Context;}
 Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async constraints=>{
   p.mic.push(constraints);if(p.denyMic)throw new DOMException('Synthetic microphone denial','NotAllowedError');
   const track={kind:'audio',readyState:'live',stop(){this.readyState='ended'}};p.tracks.push(track);
   return {getTracks:()=>[track],getAudioTracks:()=>[track]};
 }});
})()'''


def configuration(page, mode):
    page.wait_for_function('(mode)=>WWPracticeTools.getMode()===mode',arg=mode)
    assert page.locator('#practice-tools-mode').inner_text()==(mode or 'Not selected')
    assert page.locator('#metronome-mode-controls').evaluate('(n)=>n.hidden')== (mode not in ('Challenge','Performance'))
    settings=page.evaluate('WWPracticeTools.currentTuner()')
    assert settings==({'targetWindow':5,'history':7,'stableFrames':3} if mode=='Beginner' else {'targetWindow':2,'history':5,'stableFrames':2})


def save_mode(page, mode):
    open_editor(page)
    page.locator('#change-level-select').select_option(mode)
    page.locator('#change-level-form button[type=submit]').click()
    page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("saved successfully") && document.querySelector("#change-level-form").getAttribute("aria-busy")!=="true"')
    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
    configuration(page,mode)


def authoritative_change(page, mode):
    assert page.evaluate('''async mode=>{
      const r=await fetch('/account/profile/game-mode',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({game_mode:mode})});
      if(!r.ok)return false;
      await WWGameModeEntry.start();return true;
    }''',mode)
    configuration(page,mode)


def open_metronome(page):
    page.locator('#metronome-open-button').click();page.wait_for_selector('#metronome-panel',state='visible')


def volume(page,value):
    page.locator('#metronome-volume').evaluate('(n,value)=>{n.value=String(value);n.dispatchEvent(new Event("input",{bubbles:true}))}',value)


@pytest.mark.parametrize('browser_name',['chrome','firefox'])
@pytest.mark.parametrize('scenario',['registered','guest','safety','delayed'])
def test_practice_tools(save_site,tmp_path,browser_name,scenario):
    origin,database=save_site
    result={'browser':browser_name,'scenario':scenario,'checks':[],'page_errors':[],'physical_device':'NOT RUN','audio':'CONTROLLED SYNTHETIC TOOL API; not audible quality/latency proof'}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else p.firefox.launch()
            context=browser.new_context(base_url=origin,viewport={'width':1440,'height':900},reduced_motion='reduce')
            context.add_init_script(AUDIO_PROBE)
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
            page.on('dialog',lambda d:d.accept())
            requests=[]
            context.on('request',lambda r:requests.append({'path':r.url.removeprefix(origin),'method':r.method}) if r.url.startswith(origin) and '/account/' in r.url else None)
            if scenario=='guest':
                before=snapshot(database);page.goto(origin+'/guest')
                page.wait_for_function('window.WWGuest && !document.querySelector("#guest-setup-fields").disabled')
                storage=page.evaluate('({...localStorage})')
                page.locator('#guest-instrument').select_option('Flute');page.locator('#guest-game_mode').select_option('Performance');page.locator('#guest-goal').select_option(index=1)
                page.locator('#guest-setup-form button').click();configuration(page,'Performance')
                open_metronome(page)
                page.locator('#metronome-subdivision').select_option('2');page.locator('#metronome-meter').select_option('6');page.locator('#metronome-sound').select_option('Snap');volume(page,.1)
                page.locator('#metronome-start-button').click();page.wait_for_function('toolProbe.intervals.length===1 && toolProbe.voices.length>=2')
                assert page.evaluate('toolProbe.voices[0].type==="triangle" && toolProbe.voices[0].hz===1600')
                created=page.evaluate('toolProbe.contexts.length')
                page.locator('#guest-game_mode').select_option('Beginner');configuration(page,'Beginner')
                assert page.evaluate('toolProbe.intervals.length===0 && toolProbe.mic.length===0')
                assert page.evaluate('toolProbe.contexts.length')==created
                assert page.locator('#metronome-volume').input_value()=='0.1'
                page.locator('#guest-discard').click()
                assert page.locator('#guest-tools').is_hidden() and page.locator('#guest-game_mode').input_value()==''
                assert page.evaluate('WWPracticeTools.getMode()') is None
                assert page.evaluate('({...localStorage})')==storage
                assert not requests and snapshot(database)==before
                page.reload();page.wait_for_function('window.WWPracticeTools && window.WWGuest')
                assert page.locator('#guest-game_mode').input_value()=='' and page.evaluate('WWPracticeTools.getMode()') is None
                login(page,origin);chooser(page);close_chooser(page);configuration(page,None)
                assert account(database)['mode'] is None
                assert not any(r['path']=='/account/profile/game-mode' for r in requests)
                result['checks'].append('Guest local selection configures tools; no account transport or DB/storage writes; mode switch stops one scheduler without raising volume; discard/reload and login do not transfer mode')
            else:
                login(page,origin);chooser(page);close_chooser(page);configuration(page,None)
                if scenario=='registered':
                    before=snapshot(database);open_metronome(page)
                    assert page.evaluate('toolProbe.mic.length===0 && toolProbe.contexts.length===0')
                    assert not any(r['path']=='/account/profile/game-mode' for r in requests)
                    assert snapshot(database)==before
                    page.locator('#metronome-close-button').click()
                    save_mode(page,'Beginner')
                    assert page.evaluate('toolProbe.mic.length===0 && toolProbe.contexts.length===0')
                    page.evaluate('toolProbe.frequency=440*Math.pow(2,4/1200)')
                    page.locator('#tuner-open-button').click()
                    page.wait_for_function('document.querySelector("#tuner-note").textContent==="A4" && document.querySelector("#tuner-diagnosis").textContent==="PRISTINE"')
                    page.evaluate('toolProbe.frequency=466.1637615')
                    page.wait_for_function('document.querySelector("#tuner-note").textContent==="A♯4" && document.querySelector("#tuner-diagnosis").textContent==="PRISTINE"')
                    page.locator('#tuner-close-button').click()
                    assert page.evaluate('toolProbe.tracks.every(t=>t.readyState==="ended")')
                    for mode in ['Challenge','Performance']:
                        save_mode(page,mode);open_metronome(page)
                        page.locator('#metronome-subdivision').select_option('3');page.locator('#metronome-meter').select_option('3');page.locator('#metronome-sound').select_option('Clave');volume(page,.1)
                        offset=page.evaluate('toolProbe.voices.length')
                        page.locator('#metronome-start-button').click()
                        page.wait_for_function('(offset)=>toolProbe.intervals.length===1 && toolProbe.voices.length>=offset+10',arg=offset)
                        voices=page.evaluate('(offset)=>toolProbe.voices.slice(offset,offset+10).map(v=>({hz:v.hz,type:v.type,time:v.time,peak:v.peak}))',offset)
                        assert all(v['hz']==2500 and v['type']=='sine' for v in voices)
                        assert abs(voices[1]['time']-voices[0]['time']-1/6)<1e-6
                        assert voices[0]['peak']>voices[3]['peak']>voices[1]['peak']>0
                        assert abs(voices[9]['peak']-voices[0]['peak'])<1e-9
                        resources=page.evaluate('({contexts:toolProbe.contexts.length,mic:toolProbe.mic.length,volume:WoodshedAudio.getVolume()})')
                        authoritative_change(page,'Beginner')
                        assert page.evaluate('toolProbe.intervals.length===0')
                        assert page.evaluate('({contexts:toolProbe.contexts.length,mic:toolProbe.mic.length,volume:WoodshedAudio.getVolume()})')==resources
                        page.locator('#metronome-close-button').click()
                    save_mode(page,'Performance');page.reload();authenticated(page)
                    page.wait_for_function('window.WWPracticeTools');configuration(page,'Performance')
                    assert account(database)['mode']=='Performance' and account(database)['level']=='Beginner'
                    assert page.locator('a[data-scene-cell=L1]').get_attribute('href')=='/store'
                    assert page.locator('a[data-scene-cell=L2]').get_attribute('href')=='/p-book'
                    result['checks'].append('NULL honest/no mode write; each authoritative mode; save/refresh; Beginner+4cent and distinct A-sharp synthetic signal; both advanced configurations schedule triplets with3-beat accents; volume/resources stable and one scheduler stopped on mode changes; Level/navigation preserved')
                elif scenario=='safety':
                    page.evaluate('toolProbe.denyMic=true')
                    page.locator('#tuner-open-button').click();page.wait_for_function('document.querySelector("#tuner-diagnosis").textContent==="MICROPHONE DENIED"')
                    assert page.evaluate('toolProbe.contexts.every(c=>c.state==="closed") && toolProbe.mic.length===1')
                    denied=page.evaluate('({contexts:toolProbe.contexts.length,mic:toolProbe.mic.length})')
                    authoritative_change(page,'Beginner')
                    assert page.locator('#tuner-diagnosis').inner_text()=='MICROPHONE DENIED'
                    assert page.evaluate('({contexts:toolProbe.contexts.length,mic:toolProbe.mic.length})')==denied
                    page.locator('#tuner-close-button').click()
                    authoritative_change(page,'Challenge')
                    page.evaluate('toolProbe.denyMic=false;toolProbe.frequency=440*Math.pow(2,4/1200)')
                    page.locator('#tuner-open-button').click();page.wait_for_function('document.querySelector("#tuner-diagnosis").textContent==="SHARP"')
                    before=page.evaluate('({contexts:toolProbe.contexts.length,mic:toolProbe.mic.length})')
                    authoritative_change(page,'Beginner');page.wait_for_function('document.querySelector("#tuner-diagnosis").textContent==="PRISTINE"')
                    assert page.evaluate('({contexts:toolProbe.contexts.length,mic:toolProbe.mic.length})')==before
                    page.locator('#tuner-close-button').click();assert page.evaluate('toolProbe.tracks.every(t=>t.readyState==="ended")')
                    open_metronome(page);page.evaluate('toolProbe.failAudio=true')
                    page.locator('#metronome-start-button').click();page.wait_for_function('document.querySelector("#metronome-status").textContent.includes("Tap Start to try again")')
                    assert page.evaluate('toolProbe.intervals.length===0')
                    created=page.evaluate('toolProbe.contexts.length')
                    page.locator('#metronome-start-button').click();page.wait_for_function('toolProbe.intervals.length===1')
                    assert page.evaluate('toolProbe.contexts.length')==created
                    page.locator('#metronome-close-button').click();assert page.evaluate('toolProbe.intervals.length===0')
                    result['checks'].append('Synthetic microphone denial closes input context and recovers on explicit gesture; active tuner mode change resets feedback without permission/context duplication; synthetic audio resume error recovers with same context and one scheduler; teardown verified')
                else:
                    page.locator('#instrument-object').click();page.wait_for_function('WWSurfaces.current()?.id==="your-woodchuck"')
                    held=[]
                    def hold(route):
                        response=route.fetch();assert response.status==200;held.append((route,response))
                    page.route('**/account/profile/game-mode',hold)
                    page.locator('#change-level-select').select_option('Challenge');page.locator('#change-level-form button[type=submit]').click()
                    pump_until(page,lambda:bool(held))
                    other=context.new_page();other.on('dialog',lambda d:d.accept())
                    assert other.request.post(origin+'/account/logout',headers={'Origin':origin}).status==200
                    login(other,origin,'B');chooser(other);close_chooser(other);configuration(other,None)
                    before=snapshot(database)
                    held[0][0].fulfill(response=held[0][1]);page.wait_for_function('document.querySelector(".app-shell").hidden')
                    other.evaluate('window.dispatchEvent(new CustomEvent("ww:game-mode-projected",{detail:{mode:"Performance"}}));window.dispatchEvent(new CustomEvent("woodshed:mode",{detail:{mode:"Challenge"}}))')
                    configuration(other,None)
                    assert snapshot(database)==before and account(database,'B')['mode'] is None
                    assert other.evaluate('toolProbe.contexts.length===0 && toolProbe.mic.length===0')
                    result['checks'].append('Actual A commit response delayed across real logout/login B; stale tab stops; B remains unselected with unchanged DB and no audio resources; untrusted event details cannot configure tools')
            assert not result['page_errors'],result['page_errors']
            result['requests']=requests;result['audio_probe']=page.evaluate('({contexts:toolProbe.contexts.map(c=>c.state),mic:toolProbe.mic,trackStates:toolProbe.tracks.map(t=>t.readyState),activeSchedulers:toolProbe.intervals.length,voices:toolProbe.voices.length})')
            assert result['audio_probe']['activeSchedulers']==0
            page.screenshot(path=str(tmp_path/(scenario+'-'+browser_name+'.png')))
            result['result']='PASS';context.close();browser.close()
    finally:
        (tmp_path/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
