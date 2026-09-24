"""Shared R4 interactions in Chromium with disposable synthetic accounts."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest


SERVER = '''
from sqlalchemy import select, func
from app.main import app
from app.db import Base, engine, SessionLocal
from app.models import WoodchuckProfile, WoodchuckState, OwnedItemCopy
from app.security import hash_pin
from app.age_privacy import declare_age
Base.metadata.create_all(engine)
with SessionLocal() as session:
    for label,credits in [('A',37),('B',83)]:
        p=WoodchuckProfile(woodchuck_id='WC-GUEST-'+label,display_name='Synthetic '+label,
            pin_hash=hash_pin('2468'),instrument='Flute',level='Beginner',goal='Practice every day')
        session.add(p);session.flush()
        declare_age(session, p.id, '13to17')
        session.add(OwnedItemCopy(profile_id=p.id,item_key='candle',acquisition_source='store',purchase_price=25,placement_x=.2,placement_y=.3,placement_size='medium'))
        session.add(WoodchuckState(profile_id=p.id,revision=7,state_json={
            'account':{'woodchuckId':p.woodchuck_id,'authenticated':True,'serverRevision':7},
            'profile':{'woodchuckName':p.display_name,'instrument':'Flute','level':'Beginner','goal':'Practice every day'},
            'progress':{'credits':credits},'practiceLog':[{'note':'saved account '+label}]}))
    session.commit()
# Diagnostics exist only in this disposable test app, never application routes.
@app.get('/test/snapshot')
def snapshot():
    with SessionLocal() as s:
        return {'counts':{t.name:s.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables},
                'states':{p.woodchuck_id:s.get(WoodchuckState,p.id).state_json for p in s.scalars(select(WoodchuckProfile))}}
'''


@pytest.mark.skipif(not shutil.which('node') or not Path('/opt/google/chrome/chrome').exists(), reason='Local Node and Chromium required')
def test_r4_shared_surfaces_and_character(tmp_path):
    source = Path(__file__).resolve().parents[1]
    (tmp_path/'guest_test_app.py').write_text(SERVER)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    env={'PATH':os.environ['PATH'],'LANG':'C.UTF-8','PYTHONPATH':str(source),
         'DATABASE_URL':'sqlite:///'+str(tmp_path/'guest.db'),
         'SESSION_SECRET':'synthetic-browser-guest-session-secret-long','SESSION_COOKIE_SECURE':'false',
         'LOGIN_RATE_LIMIT_MODE':'off','LOGIN_RATE_LIMIT_REQUIRED':'false',
         'PYTHONPYCACHEPREFIX':str(tmp_path/'pycache'),
         'XDG_CACHE_HOME':str(tmp_path/'cache'),'XDG_CONFIG_HOME':str(tmp_path/'config'),'XDG_DATA_HOME':str(tmp_path/'data')}
    with (tmp_path/'uvicorn.log').open('w') as out:
        server=subprocess.Popen([sys.executable,'-m','uvicorn','guest_test_app:app','--app-dir',str(tmp_path),
            '--host','127.0.0.1','--port',str(port),'--no-proxy-headers','--timeout-graceful-shutdown','2'],cwd=source,env=env,stdout=out,stderr=out)
        try:
            for _ in range(1000):
                try:
                    with urlopen(origin+'/guest',timeout=2) as r: assert r.status==200
                    break
                except URLError:
                    assert server.poll() is None,(tmp_path/'uvicorn.log').read_text()
                    time.sleep(.05)
            else: pytest.fail('Disposable Uvicorn did not start')
            config={'origin':origin,'chrome':'/opt/google/chrome/chrome','profile':str(tmp_path/'chrome'),'output':str(tmp_path)}
            (tmp_path/'browser-command.json').write_text(json.dumps(config,indent=2))
            result=subprocess.run(['node',str(source/'tests/r4_browser_driver.cjs')],input=json.dumps(config),text=True,capture_output=True,cwd=source,env=env,timeout=260)
            (tmp_path/'browser-stdout.txt').write_text(result.stdout)
            (tmp_path/'browser-stderr.txt').write_text(result.stderr)
            assert result.returncode==0,result.stdout+'\n'+result.stderr
            proof=json.loads(result.stdout)
            assert proof['viewports'] == [390, 1440]
            assert proof['checks'] >= 250
            assert proof['layouts'] == [[320,568],[390,844],[430,932],[768,1024],[1024,768],[844,390],[1440,900]]
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
