"""One synthetic Chromium journey: real Guest tools/network, sessions and old tabs."""
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
from app.models import WoodchuckProfile, WoodchuckState
from app.security import hash_pin
Base.metadata.create_all(engine)
with SessionLocal() as session:
    for label,credits in [('A',37),('B',83)]:
        p=WoodchuckProfile(woodchuck_id='WC-GUEST-'+label,display_name='Synthetic '+label,
            pin_hash=hash_pin('2468'),instrument='Flute',level='Beginner',goal='Practice every day')
        session.add(p);session.flush()
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
def test_real_guest_tools_sessions_and_old_tabs(tmp_path):
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
            result=subprocess.run(['node',str(source/'tests/guest_browser_driver.cjs')],input=json.dumps(config),text=True,capture_output=True,cwd=source,env=env,timeout=110)
            (tmp_path/'browser-stdout.txt').write_text(result.stdout)
            (tmp_path/'browser-stderr.txt').write_text(result.stderr)
            assert result.returncode==0,result.stdout+'\n'+result.stderr
            proof=json.loads(result.stdout)
            assert proof['guest_application_requests']==[]
            assert proof['all_database_tables_unchanged_during_guest'] is True
            assert proof['old_tab_blocked'] is True
            assert proof['pending_logout_tab_blocked'] is True
            assert proof['delayed_document_cases'] == ['signed out', 'A', 'B']
            assert proof['ordinary_logout_verified'] is True
            assert proof['failed_logout_stayed_signed_in'] is True
            assert proof['server_history_preserved'] is True
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
