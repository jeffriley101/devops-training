from datetime import date, datetime, timedelta, timezone
import base64
import json

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
import pytest
from sqlalchemy import select

from app import main, trusted_verifier_dashboard
from app.band_director_dashboard import dashboard_metrics
from app.models import (CrownAward, PracticeChart, PracticeChartVerification, RewardGrant, Season,
                        StudentVerifierConnection, WoodchuckProfile)
from app.trusted_verifier_dashboard import verifier_dashboard_snapshot
from test_band_director_roster import (roster_db, add_student as add_roster_student, add_chart, signed_client,
                                      contest_roster, join_roster_team)

def add_student(factory, name, **kwargs):
    kwargs.setdefault("role", "verifier")
    return add_roster_student(factory, name, **kwargs)


TODAY = date(2026, 9, 9)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 9, 18, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(trusted_verifier_dashboard, "datetime", FixedDatetime)


def connection(factory, profile_id):
    with factory() as session:
        return session.scalar(select(StudentVerifierConnection.id).where(
            StudentVerifierConnection.profile_id == profile_id))


def snapshot(factory, **kwargs):
    with factory() as session:
        result = verifier_dashboard_snapshot(session, verifier_id=1, today=TODAY, **kwargs)
        assert not session.new and not session.dirty and not session.deleted
        return result


@pytest.mark.parametrize("role", ["verifier"])
def test_all_accepted_roles_have_free_snapshot(roster_db, role):
    add_student(roster_db, "Musician", role=role)
    response = signed_client().get("/trusted-verifiers/dashboard")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.context["student"]["display_name"] == "Musician"
    assert response.context["student"]["role"] == role
    assert '<h1>Verifier Dashboard</h1>' in response.text
    assert 'class="ww-dashboard-header"' in response.text
    assert 'id="verifier-student-selector"' not in response.text
    for label in ("Practice streak", "Lifetime P-Charts", "Verified minutes this week",
                  "Pristine minutes this week", "A little practice is a great place to start.",
                  "Earned achievements will be celebrated here.", "No current season.", "No team yet"):
        assert label in response.text
    assert ("Band Director Dashboard</a>" in response.text) == (role == "band_director")
    assert '<strong>0</strong>' in response.text
    assert 'Based on the most recently completed week' in response.text
    sections = ['verifier-student-name', 'verifier-rating-heading', 'verifier-week-heading',
                'verifier-quality-heading', 'verifier-season-heading', 'verifier-lifetime-heading',
                'verifier-achievements-heading', 'verifier-pchart-heading']
    positions = [response.text.index(f'id="{heading}"') for heading in sections]
    assert positions == sorted(positions)


def test_selector_and_cross_verifier_isolation(roster_db):
    first = add_student(roster_db, "Alpha", role="verifier")
    second = add_student(roster_db, "Beta", role="verifier")
    hidden = add_student(roster_db, "Secret Other Child", verifier_id=2)
    add_chart(roster_db, first, TODAY, 11)
    add_chart(roster_db, second, TODAY, 23)
    add_chart(roster_db, hidden, TODAY, 999)
    client = signed_client()
    page = client.get("/trusted-verifiers/dashboard")
    assert page.context["student"]["weekly"]["total"] == 11
    assert 'name="connection_id"' in page.text
    assert "Secret Other Child" not in page.text
    page = client.get(f"/trusted-verifiers/dashboard?connection_id={connection(roster_db, second)}")
    assert page.context["student"]["weekly"]["total"] == 23
    assert page.context["student"]["display_name"] == "Beta"
    for route in ("dashboard", "practice-charts"):
        response = client.get(f"/trusted-verifiers/{route}?connection_id={connection(roster_db, hidden)}")
        assert response.status_code == 404
        assert "Secret Other Child" not in response.text


@pytest.mark.parametrize("rating,direction,display,label", [
    (104.0, "up", "104", "Improving"),
    (60.5, "steady", "60.5", "Steady"),
    (20.0, "down", "20", "Declining"),
])
def test_rating_presentation_only(roster_db, monkeypatch, rating, direction, display, label):
    add_student(roster_db, "Presentation", role="verifier")
    original = main.verifier_dashboard_snapshot
    def presentation(*args, **kwargs):
        data = original(*args, **kwargs)
        data["student"]["rating"] = rating
        data["student"]["trend"]["direction"] = direction
        return data
    monkeypatch.setattr(main, "verifier_dashboard_snapshot", presentation)
    html = signed_client().get("/trusted-verifiers/dashboard").text
    assert f'<strong>{display}</strong>' in html
    assert f'<span>{label}</span>' in html
    assert f'verifier-trend-{direction}' in html


@pytest.mark.parametrize("status,active", [("pending", True), ("rejected", True),
                                           ("disconnected", True), ("accepted", False)])
def test_relationship_reauthorization_revokes_access_without_login(roster_db, status, active):
    student = add_student(roster_db, "Revoked")
    selected = connection(roster_db, student)
    client = signed_client()
    assert client.get(f"/trusted-verifiers/dashboard?connection_id={selected}").status_code == 200
    with roster_db() as session:
        session.get(StudentVerifierConnection, selected).status = status
        session.get(WoodchuckProfile, student).status = "active" if active else "deleted"
        session.commit()
    for route in ("dashboard", "practice-charts"):
        assert client.get(f"/trusted-verifiers/{route}?connection_id={selected}").status_code == 404
    page = client.get("/trusted-verifiers/dashboard")
    assert page.context["student"] is None
    assert "Revoked" not in page.text


def test_shared_metrics_completed_rating_and_private_note_isolation(roster_db):
    student = add_student(roster_db, "Metrics")
    add_chart(roster_db, student, date(2026, 8, 31), 120, status="approved",
              reviewer=2, note="OTHER VERIFIER PRIVATE NOTE")
    for offset in range(1, 5):
        add_chart(roster_db, student, date(2026, 8, 31) - timedelta(weeks=offset), 60)
    add_chart(roster_db, student, TODAY, 40, status="approved", include_contests=False)
    add_chart(roster_db, student, TODAY, 0)
    add_chart(roster_db, student, TODAY, -1)
    with roster_db() as session:
        pristine = PracticeChart(profile_id=student, practice_date=TODAY, minutes=7,
                                 source="pristine", instrument="Trumpet", detected_playing_seconds=420)
        session.add(pristine)
        session.flush()
        session.add(PracticeChartVerification(practice_chart_id=pristine.id, verifier_id=2, status="approved"))
        session.commit()
        session.add(StudentVerifierConnection(profile_id=student, verifier_id=2, role="band_director", status="accepted"))
        session.commit()
        director = dashboard_metrics(session, verifier_id=2, today=TODAY)["students"][0]
    parent = snapshot(roster_db)["student"]
    for key in ("weekly", "lifetime", "rating", "trend"):
        assert parent[key] == director[key]
    assert parent["weekly"] == {"total": 47, "verified": 40, "pristine": 7, "charts": 4, "days": 1}
    assert parent["lifetime"]["total"] == 407
    assert parent["lifetime"]["charts"] == 9
    assert parent["rating"] == 99.25
    assert parent["trend"]["delta"] == 99.25 - 48.125
    add_chart(roster_db, student, TODAY, 1000, status="approved")
    assert snapshot(roster_db)["student"]["rating"] == parent["rating"]
    client = signed_client()
    page = client.get("/trusted-verifiers/dashboard")
    queue = client.get(f"/trusted-verifiers/practice-charts?connection_id={connection(roster_db, student)}")
    for value in ("OTHER VERIFIER PRIVATE NOTE", "other@example.com", "Director Two", "last_email_error_code"):
        assert value not in page.text + queue.text


def test_central_week_and_positive_practice_streak(roster_db, monkeypatch):
    student = add_student(roster_db, "Streak")
    for day, minutes in [(6, 12), (7, 20), (7, 5), (8, 30), (9, 0)]:
        add_chart(roster_db, student, date(2026, 9, day), minutes)
    current = snapshot(roster_db)["student"]
    assert current["practice_streak"] == 3
    assert current["weekly"]["total"] == 55
    class SundayCentral(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 7, 4, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(trusted_verifier_dashboard, "datetime", SundayCentral)
    with roster_db() as session:
        assert verifier_dashboard_snapshot(session, verifier_id=1)["student"]["weekly"]["total"] == 12


def test_durable_season_team_and_bounded_earned_events(contest_roster):
    student = add_student(contest_roster, "Season Child", role="verifier")
    other = add_student(contest_roster, "Other Child", verifier_id=2)
    join_roster_team(contest_roster, student, "Current Team")
    join_roster_team(contest_roster, student, "Old Team")
    add_chart(contest_roster, student, date(2026, 8, 2), 100)
    add_chart(contest_roster, student, date(2026, 8, 3), 10)
    add_chart(contest_roster, student, TODAY, 20)
    add_chart(contest_roster, student, date(2026, 10, 1), 99)
    with contest_roster() as session:
        session.scalar(select(Season).where(Season.name == "School")).ends_on = date(2026, 9, 30)
        for offset in range(6):
            session.add(CrownAward(profile_id=student, category_key="weekly-points-leaders",
                                   source_key=f"crown-{offset}",
                                   earned_at=datetime(2026, 9, 1 + offset, tzinfo=timezone.utc)))
        for reward_type in ("crown_win", "trophy", "goat", "traveling_cup", "dandelion"):
            session.add(RewardGrant(profile_id=student, source_key="crown-5", reward_type=reward_type,
                                    amount=2, created_at=datetime(2026, 9, 9, tzinfo=timezone.utc)))
        session.add(RewardGrant(profile_id=other, source_key="other", reward_type="goat", amount=99))
        session.add(RewardGrant(profile_id=student, source_key="zero", reward_type="goat", amount=0))
        session.commit()
    result = snapshot(contest_roster)["student"]
    assert result["season"]["name"] == "School"
    assert result["season"]["starts_on"] == date(2026, 8, 3)
    assert result["season"]["ends_on"] == date(2026, 9, 30)
    assert result["season"]["minutes"] == 30
    assert result["season"]["charts"] == 2
    assert result["season"]["days"] == 2
    assert result["season"]["verified"] == 0
    assert result["season"]["pristine"] == 0
    assert result["team"]["name"] == "Current Team"
    events = result["achievements"]
    assert len(events) == 5
    assert [event["type"] for event in events[:2]] == ["goat", "trophy"]
    assert all(event["quantity"] != 99 for event in events)
    assert sum(event["type"] == "crown" for event in events) == 3
    assert all(set(event) == {"key", "type", "label", "icon", "quantity", "earned_at"} for event in events)


def test_selected_review_queue_preserves_assignment_authorization(roster_db):
    first = add_student(roster_db, "First", role="verifier")
    second = add_student(roster_db, "Second", role="verifier")
    add_chart(roster_db, first, TODAY, 10, status="pending")
    add_chart(roster_db, first, TODAY, 20, status="pending", reviewer=2)
    add_chart(roster_db, second, TODAY, 30, status="pending")
    client = signed_client()
    url = f"/trusted-verifiers/practice-charts?connection_id={connection(roster_db, first)}"
    response = client.get(url)
    assert response.headers["cache-control"] == "no-store"
    queue = response.json()["pending_charts"]
    assert len(queue) == 1 and queue[0]["chart"]["minutes"] == 10
    with roster_db() as session:
        other_review = session.scalar(select(PracticeChartVerification).where(PracticeChartVerification.verifier_id == 2))
        other_id = other_review.id
    assert client.post(f"/trusted-verifiers/practice-charts/{other_id}/respond",
                       json={"decision": "approved"}).status_code in (403, 404)
    review_id = queue[0]["verification_id"]
    assert client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                       json={"decision": "approved", "response_note": "Well done"}).status_code == 200
    assert client.get(url).json()["pending_charts"] == []
    assert client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                       json={"decision": "rejected"}).status_code in (400, 403, 409)


def test_adult_only_render_ignores_unrelated_student_session(roster_db, monkeypatch):
    own = add_student(roster_db, "Authorized Child", role="verifier")
    unrelated = add_student(roster_db, "Unrelated Signed In Player", connected=False)
    client = TestClient(main.app)
    assert client.get("/trusted-verifiers/dashboard", follow_redirects=False).status_code == 303
    cookie = TimestampSigner(main.SESSION_SECRET).sign(base64.b64encode(json.dumps({
        "woodchuck_profile_id": unrelated, "woodchuck_session_version": 0, "trusted_verifier_id": 1,
    }).encode())).decode()
    client.cookies.set("session", cookie)
    def forbidden(*args, **kwargs):
        raise AssertionError("Generic student rendering must never run")
    monkeypatch.setattr(main, "_render", forbidden)
    from app import account_routes, login_streaks
    monkeypatch.setattr(account_routes, "apply_daily_login", forbidden)
    monkeypatch.setattr(login_streaks, "apply_daily_login", forbidden)
    response = client.get("/trusted-verifiers/dashboard")
    assert response.status_code == 200
    assert response.context["student"]["connection_id"] == connection(roster_db, own)
    assert "Unrelated Signed In Player" not in response.text
    assert 'data-authenticated="false"' in response.text
    assert "authenticated_profile" not in response.context
    assert "profile_id" not in response.context["student"]


@pytest.mark.parametrize("width", [390, 1100])
def test_rendered_dashboard_mobile_and_desktop(roster_db, tmp_path, width):
    """Real Chromium layout and dashboard JS, with only unrelated app scripts omitted."""
    import re
    import shutil
    import subprocess
    from pathlib import Path

    chrome = shutil.which("google-chrome")
    if not chrome or not shutil.which("node"):
        pytest.skip("Chromium and Node required")
    student_id = add_student(roster_db, "First Musician", role="verifier")
    add_student(roster_db, "Second Musician", role="verifier")
    add_chart(roster_db, student_id, date(2026, 8, 31), 120, status="approved")
    add_chart(roster_db, student_id, date(2026, 9, 8), 20, status="approved")
    add_chart(roster_db, student_id, TODAY, 30)
    with roster_db() as session:
        session.add(Season(key="back-to-school-2026", name="Back to School", status="active",
                           starts_on=date(2026, 8, 3), ends_on=date(2026, 11, 1)))
        session.add(CrownAward(profile_id=student_id, category_key="weekly-points-leaders",
                               source_key="browser-crown", earned_at=datetime(2026, 9, 7, tzinfo=timezone.utc)))
        session.add(RewardGrant(profile_id=student_id, source_key="browser-goat", reward_type="goat",
                                amount=1, created_at=datetime(2026, 9, 8, tzinfo=timezone.utc)))
        session.commit()
    html = signed_client().get("/trusted-verifiers/dashboard").text
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', html)
    html = re.sub(r'<link\s+rel="stylesheet"\s+href="/([^"?]+)[^"]*"\s*/?>',
                  lambda match: '<style>' + Path(match[1]).read_text() + '</style>', html)
    setup = """window.errors=[]; window.addEventListener('error', e => errors.push(e.message));
      window.fetch = async () => ({ok:true, status:200, json:async () => ({pending_charts:[
        {verification_id:42, student:{display_name:'First Musician'},
         chart:{practice_date:'2026-09-09',minutes:20,instrument:'Trumpet',practice_details:[]}}
      ]})});"""
    html = html.replace('</body>', '<script>' + setup + '</script><script>' +
                        Path('static/js/trusted-verifier-dashboard.js').read_text() + '</script></body>')
    driver = r"""
const {spawn} = require('node:child_process');
let input=''; process.stdin.on('data', chunk => input+=chunk);
process.stdin.on('end', async () => {
 const config=JSON.parse(input);
 const chrome=spawn(config.chrome,['--headless','--no-sandbox','--remote-debugging-pipe',
   '--no-first-run','--disable-background-networking','--user-data-dir='+config.profile],
   {stdio:['ignore','ignore','ignore','pipe','pipe']});
 let sequence=0, buffer=''; const pending=new Map();
 const timer=setTimeout(() => {chrome.kill();process.exit(2);},20000);
 chrome.stdio[4].on('data', chunk => {
   buffer+=chunk; let end;
   while((end=buffer.indexOf('\0'))>=0) {
     const message=JSON.parse(buffer.slice(0,end)); buffer=buffer.slice(end+1);
     const waiter=pending.get(message.id);
     if(waiter) {pending.delete(message.id); message.error?waiter.reject(message.error):waiter.resolve(message.result);}
   }
 });
 const send=(method,params={},sessionId) => new Promise((resolve,reject) => {
   const id=++sequence; pending.set(id,{resolve,reject});
   chrome.stdio[3].write(JSON.stringify({id,method,params,sessionId})+'\0');
 });
 try {
   const {targetId}=await send('Target.createTarget',{url:'about:blank'});
   const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
   await send('Emulation.setDeviceMetricsOverride',{width:config.width,height:844,deviceScaleFactor:1,mobile:false},sessionId);
   await send('Runtime.evaluate',{expression:'document.open();document.write('+JSON.stringify(config.html)+');document.close();'},sessionId);
   const result=await send('Runtime.evaluate',{returnByValue:true,awaitPromise:true,expression:`(async () => {
     await new Promise(resolve => setTimeout(resolve,100));
     const form=document.querySelector('#verifier-student-selector');
     let submitted=false; form.addEventListener('submit', event => {event.preventDefault();submitted=true;});
     const select=form.querySelector('select'); select.selectedIndex=1;
     select.dispatchEvent(new Event('change', {bubbles:true}));
     const selected=select.value; select.selectedIndex=0;
     return {pageWidth:document.documentElement.scrollWidth,submitted,
       selected,noButton:!form.querySelector('button'),
       surface:getComputedStyle(document.querySelector('.verifier-dashboard')).backgroundColor,
       metrics:document.querySelectorAll('.verifier-metrics dd').length,
       weekly:document.querySelectorAll('.verifier-week-metrics dd').length,
       ratingSize:parseFloat(getComputedStyle(document.querySelector('.verifier-rating > strong')).fontSize),
       achievementCount:document.querySelectorAll('.verifier-achievements li').length,
       notice:document.querySelector('#verifier-review-notice').textContent.trim(),
       noticeVisible:!document.querySelector('#verifier-review-notice').hidden,
       tapSizes:[...document.querySelectorAll('#verifier-practice-chart-list button, #verifier-connection')].map(b=>b.getBoundingClientRect().height),
       labelsFit:[...document.querySelectorAll('.verifier-metrics dt')].every(e=>e.scrollWidth<=e.clientWidth),
       actions:[...document.querySelectorAll('#verifier-practice-chart-list button')].map(b=>b.textContent),
       tables:document.querySelectorAll('table').length,errors:window.errors};
   })()`},sessionId);
   if(result.exceptionDetails) throw result.exceptionDetails;
   const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},sessionId);
   process.stdout.write(JSON.stringify({...result.result.value,screenshot:shot.data}));
 } catch(error) {process.stderr.write(JSON.stringify(error));process.exitCode=1;}
 finally {clearTimeout(timer);chrome.kill();}
});
"""
    process = subprocess.run(['node', '-e', driver], input=json.dumps({
        'chrome': chrome, 'profile': str(tmp_path / 'chrome'), 'width': width, 'html': html,
    }), text=True, capture_output=True, timeout=30, check=True)
    result = json.loads(process.stdout)
    (tmp_path / f'verifier-{width}.png').write_bytes(base64.b64decode(result.pop('screenshot')))
    assert result['pageWidth'] <= width
    assert result['surface'] == 'rgb(245, 236, 216)'
    assert result['submitted'] and result['noButton']
    assert result['metrics'] == 15 and result['tables'] == 0
    assert result['actions'] == ['Approve', 'Reject']
    assert result['weekly'] == 3 and result['ratingSize'] >= 55
    assert result['achievementCount'] == 2
    assert result['noticeVisible'] and result['notice'] == '1 P-Chart needs your review'
    assert all(size >= 44 for size in result['tapSizes'])
    assert result['labelsFit']
    assert result['errors'] == []
