import hashlib
import json
import re

from fastapi.testclient import TestClient

from app.account_routes import world_entry_scope
from app.age_privacy import declare_age
from app.main import app
from app.models import WoodchuckProfile
from tests.test_account_deletion import NOW, profile


def test_only_committed_native_deletion_delivers_a_one_time_browser_receipt(play_pg):
    with play_pg() as session, session.begin():
        student = profile(session, 1)
        other = profile(session, 2)
        declare_age(session, student.id, 'adult', at=NOW)
        declare_age(session, other.id, 'adult', at=NOW)

    client = TestClient(app)
    receipt_id = 'id="deletion-browser-receipt"'
    assert receipt_id not in client.get('/?account_deleted=1').text
    credentials = {'woodchuck_id': student.woodchuck_id, 'pin': '1234'}
    assert client.post('/account/login', data=credentials).status_code == 200
    assert client.post('/account/delete', data={**credentials, 'pin': '9999', 'confirmation': 'DELETE'}).status_code == 400
    assert receipt_id not in client.get('/?account_deleted=1').text
    with play_pg() as session:
        assert session.get(WoodchuckProfile, student.id).status == 'active'

    response = client.post('/account/delete', data={**credentials, 'confirmation': 'DELETE'}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['Location'] == '/'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'clear-site-data' not in response.headers
    with play_pg() as session:
        assert session.get(WoodchuckProfile, student.id).status == 'deleted'
        assert session.get(WoodchuckProfile, other.id).status == 'active'

    welcome = client.get('/')
    assert welcome.status_code == 200 and welcome.headers['Cache-Control'] == 'no-store'
    assert student.woodchuck_id not in welcome.text
    assert 'clear-site-data' not in welcome.headers
    match = re.search(receipt_id + r'>([^<]+)</script>', welcome.text)
    assert match
    receipt = json.loads(match.group(1))
    assert set(receipt) == {'salt', 'account', 'entry'}
    assert re.fullmatch(r'[a-f0-9]{32}', receipt['salt'])
    assert receipt['account'] == hashlib.sha256(f"{receipt['salt']}:{student.woodchuck_id}".encode()).hexdigest()
    entry_scope = world_entry_scope(student.id)
    assert receipt['entry'] == hashlib.sha256(f"{receipt['salt']}:{entry_scope}".encode()).hexdigest()
    assert entry_scope not in welcome.text
    assert receipt_id not in client.get('/?account_deleted=1').text
