from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAME = (ROOT / "static/js/plunge-burrow.js").read_text(encoding="utf-8")
AUDIO = (ROOT / "static/js/audio.js").read_text(encoding="utf-8")
PAGE = (ROOT / "templates/plunge_burrow.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")


def run_core(script: str) -> dict:
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_default_board_has_balanced_non_overlapping_portals_and_obstacles() -> None:
    result = run_core(
        r"""
const core = require('./static/js/plunge-burrow.js');
const game = new core.PlungeBurrowGame({random:()=>0.37});
const snap = game.snapshot();
const pairs = Object.groupBy ? Object.groupBy(snap.portals, p=>p.pairId) : snap.portals.reduce((a,p)=>((a[p.pairId]??=[]).push(p),a),{});
const all = [...snap.trail,...snap.obstacles,...snap.portals,snap.dandelion].filter(Boolean);
const keys = all.map(c=>`${c.x},${c.y}`);
const obstacleKeys = new Set(snap.obstacles.map(c=>`${c.x},${c.y}`));
const safeExits = snap.portals.every(p=>Object.values({u:[0,-1],d:[0,1],l:[-1,0],r:[1,0]}).every(v=>{
  const x=p.x+v[0],y=p.y+v[1]; return x>=0&&y>=0&&x<20&&y<20&&!obstacleKeys.has(`${x},${y}`);
}));
console.log(JSON.stringify({
  portals:snap.portals.length, pairSizes:Object.values(pairs).map(v=>v.length).sort(),
  partners:Object.values(pairs).every(v=>v.length===2&&!(v[0].x===v[1].x&&v[0].y===v[1].y)),
  noOverlap:new Set(keys).size===keys.length, safeExits,
  rocks:snap.obstacles.filter(o=>o.type==='rock').length,
  rootFormations:new Set(snap.obstacles.filter(o=>o.type==='root').map(o=>o.formation)).size,
  safeStart:snap.trail.every(c=>!snap.obstacles.some(o=>o.x===c.x&&o.y===c.y)&&!snap.portals.some(p=>p.x===c.x&&p.y===c.y))
}));
"""
    )
    assert result == {
        "portals": 4, "pairSizes": [2, 2], "partners": True,
        "noOverlap": True, "safeExits": True, "rocks": 6,
        "rootFormations": 3, "safeStart": True,
    }


def test_portal_teleports_once_preserving_direction_trail_and_scores_even_if_feedback_fails() -> None:
    result = run_core(
        r"""
const {PlungeBurrowGame}=require('./static/js/plunge-burrow.js');
const game=new PlungeBurrowGame({random:()=>0.2,onEvent(event){if(event==='portal')throw Error('audio unavailable')}});
game.obstacles=[]; game.portals=[{x:11,y:10,pairId:'A',mark:'A'},{x:3,y:3,pairId:'A',mark:'A'},{x:15,y:15,pairId:'B',mark:'B'},{x:2,y:16,pairId:'B',mark:'B'}];
game.dandelion={x:0,y:0}; game.carrot=null; game.instrument=null; game.pendingGrowth=0;
const before={score:game.score,hearts:game.hearts,length:game.trail.length,direction:game.direction};
game.start(); game.tick();
const landed={head:{...game.trail[0]},score:game.score,hearts:game.hearts,length:game.trail.length,direction:game.direction,cooldown:game.portalCooldown,unique:new Set(game.trail.map(c=>`${c.x},${c.y}`)).size};
game.tick();
const grown=new PlungeBurrowGame({random:()=>0.2});grown.obstacles=[];grown.portals=game.portals;grown.dandelion={x:0,y:0};grown.pendingGrowth=1;grown.start();grown.tick();
console.log(JSON.stringify({before,landed,afterHead:game.trail[0],afterCooldown:game.portalCooldown,pendingPortalLength:grown.trail.length}));
"""
    )
    assert result["landed"] == {
        "head": {"x": 3, "y": 3}, "score": 0, "hearts": 3,
        "length": 3, "direction": "right", "cooldown": "3,3", "unique": 3,
    }
    assert result["afterHead"] == {"x": 4, "y": 3}
    assert result["afterCooldown"] is None
    assert result["landed"]["length"] == result["before"]["length"]
    assert result["pendingPortalLength"] == result["before"]["length"] + 1


def test_fifth_dandelion_spawns_one_carrot_and_carrot_rewards_are_exact() -> None:
    result = run_core(
        r"""
const {PlungeBurrowGame}=require('./static/js/plunge-burrow.js');
const game=new PlungeBurrowGame({random:()=>0.41}); game.obstacles=[]; game.portals=[]; game.start();
for(let i=0;i<5;i++){
  game.trail=game.initialTrail(); game.direction='right'; game.queuedDirection=null; game.pendingGrowth=0;
  game.carrot=null; game.instrument=null; game.dandelion={x:game.trail[0].x+1,y:game.trail[0].y}; game.tick();
}
const eligible={count:game.dandelionsCollected,carrot:game.carrot,score:game.score};
game.trail=game.initialTrail(); game.direction='right'; game.queuedDirection=null; game.pendingGrowth=0;
game.dandelion={x:0,y:0}; game.instrument=null; game.hearts=2; game.carrot={x:game.trail[0].x+1,y:game.trail[0].y};
const beforeLength=game.trail.length; game.tick(); const afterPickup={score:game.score,hearts:game.hearts,pending:game.pendingGrowth,length:game.trail.length,carrot:game.carrot};
game.dandelion={x:0,y:0}; game.instrument=null; game.tick(); const finalLength=game.trail.length;
game.trail=game.initialTrail();game.direction='right';game.pendingGrowth=0;game.hearts=3;game.carrot={x:game.trail[0].x+1,y:game.trail[0].y};game.tick();
console.log(JSON.stringify({eligible,beforeLength,afterPickup,finalLength,maxHearts:game.hearts}));
"""
    )
    assert result["eligible"]["count"] == 5
    assert result["eligible"]["carrot"] is not None
    assert result["eligible"]["score"] == 5
    assert result["afterPickup"] == {
        "score": 8, "hearts": 3, "pending": 1, "length": 4, "carrot": None,
    }
    assert result["finalLength"] == result["beforeLength"] + 2
    assert result["maxHearts"] == 3


def test_instrument_pool_repeat_scoring_growth_duplicates_and_band_set() -> None:
    result = run_core(
        r"""
const core=require('./static/js/plunge-burrow.js');
const game=new core.PlungeBurrowGame({random:()=>0}); game.obstacles=[];game.portals=[];game.dandelion={x:0,y:0};
const first=game.spawnInstrument(); game.instrument=null; const second=game.spawnInstrument();
game.instrument=null; game.score=0; game.bandSet=[]; const startLength=game.trail.length;
function collect(name){game.instrument={x:1,y:1,name,icon:'♪'};game.collectAt(game.instrument);return {score:game.score,set:[...game.bandSet]};}
const one=collect('Flute'); const duplicate=collect('Flute'); const two=collect('Clarinet'); const three=collect('Saxophone'); const complete=collect('Trumpet');
console.log(JSON.stringify({pool:core.INSTRUMENTS.map(i=>i.name),first:first.name,second:second.name,one,duplicate,two,three,complete,length:game.trail.length,startLength,instrument:game.instrument}));
"""
    )
    assert result["pool"] == ["Flute", "Clarinet", "Saxophone", "Trumpet", "Trombone", "Horn", "Tuba", "Percussion"]
    assert result["first"] == "Flute" and result["second"] == "Clarinet"
    assert result["one"] == {"score": 5, "set": ["Flute"]}
    assert result["duplicate"] == {"score": 10, "set": ["Flute"]}
    assert result["complete"] == {"score": 45, "set": []}
    assert result["length"] == result["startLength"]
    assert result["instrument"] is None


def test_instrument_and_carrot_spawn_only_on_centralized_empty_cells() -> None:
    result = run_core(
        r"""
const core=require('./static/js/plunge-burrow.js');const game=new core.PlungeBurrowGame({random:()=>0.63});
game.spawnCarrot();game.score=3;game.maybeSpawnInstrument();const s=game.snapshot();
const objects=[...s.trail,...s.obstacles,...s.portals,s.dandelion,s.carrot,s.instrument].filter(Boolean);
const keys=objects.map(c=>`${c.x},${c.y}`);
console.log(JSON.stringify({unique:new Set(keys).size===keys.length,carrotCount:s.carrot?1:0,instrumentCount:s.instrument?1:0,approved:core.INSTRUMENTS.some(i=>i.name===s.instrument.name),occupiedHasAll:objects.every(c=>game.occupiedSet().has(`${c.x},${c.y}`))}));
"""
    )
    assert result == {"unique": True, "carrotCount": 1, "instrumentCount": 1, "approved": True, "occupiedHasAll": True}


def test_restart_and_gameover_clear_run_local_pickups_growth_and_band_set() -> None:
    result = run_core(
        r"""
const {PlungeBurrowGame}=require('./static/js/plunge-burrow.js');const game=new PlungeBurrowGame({random:()=>0.26});
game.bandSet=['Flute','Tuba'];game.carrot={x:1,y:1};game.instrument={x:2,y:2,name:'Horn',icon:'📯'};game.pendingGrowth=2;game.dandelionsCollected=4;
game.status='running';game.hearts=1;game.trail=[{x:19,y:10},{x:18,y:10}];game.direction='right';game.tick();const over=game.snapshot();
game.reset();const reset=game.snapshot();
console.log(JSON.stringify({over:{status:over.status,set:over.bandSet,carrot:over.carrot,instrument:over.instrument,growth:over.pendingGrowth,count:over.dandelionsCollected},reset:{status:reset.status,score:reset.score,hearts:reset.hearts,set:reset.bandSet,carrot:reset.carrot,instrument:reset.instrument,growth:reset.pendingGrowth,count:reset.dandelionsCollected,portals:reset.portals.length}}));
"""
    )
    cleared = {"set": [], "carrot": None, "instrument": None, "growth": 0, "count": 0}
    assert result["over"] == {"status": "gameover", **cleared}
    assert result["reset"] == {"status": "ready", "score": 0, "hearts": 3, "portals": 4, **cleared}


def test_pause_freezes_portal_and_band_set_feedback_ticks() -> None:
    result = run_core(
        r"""
const {PlungeBurrowGame}=require('./static/js/plunge-burrow.js');const game=new PlungeBurrowGame({random:()=>0.2});
game.portalFlashTicks=2;game.bandSetFlashTicks=6;game.start();game.pause();game.tick();const paused={portal:game.portalFlashTicks,band:game.bandSetFlashTicks};
game.resume();game.tick();const resumed={portal:game.portalFlashTicks,band:game.bandSetFlashTicks};
console.log(JSON.stringify({paused,resumed}));
"""
    )
    assert result == {"paused": {"portal": 2, "band": 6}, "resumed": {"portal": 1, "band": 5}}


def test_second_pass_ui_accessibility_audio_and_persistence_boundaries() -> None:
    for text in ("Dandelion", "Carrot", "Matching holes", "Instrument", "Band Set"):
        assert text in PAGE
    assert 'id="plunge-dandelions"' in PAGE
    assert 'id="plunge-band-progress"' in PAGE
    assert 'aria-label="Instruments in the active Band Set"' in PAGE
    assert "flex-wrap: wrap" in CSS[CSS.index(".plunge-band-set"):]
    for effect in ("burrowPortal", "carrotCollected", "instrumentCollected", "bandSetCompleted"):
        assert f'"{effect}"' in AUDIO
        assert f'playEffect("{effect}")' in GAME
    instrument_event = GAME[GAME.index('if (event === "instrument")'):GAME.index('if (event === "hit")')]
    assert "if (detail.completed)" in instrument_event
    assert instrument_event.count('playEffect("instrumentCollected")') == 1
    assert instrument_event.count('playEffect("bandSetCompleted")') == 1
    assert "Tone.Transport" not in GAME
    assert "fetch(" not in GAME + PAGE
    assert "/account/state" not in GAME + PAGE
    assert "RewardGrant" not in GAME + PAGE
    assert "CampPoint" not in GAME + PAGE
    assert "inventory" not in GAME.casefold()
