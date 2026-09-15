from datetime import date, timedelta
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from app.band_director_dashboard import dashboard_metrics, student_practice_rating, trend
from app.models import PracticeChart, PracticeChartVerification, StudentVerifierConnection
from test_band_director_roster import roster_db, add_student, add_chart, signed_client


TODAY = date(2026, 9, 9)


def metrics(factory, **kwargs):
    with factory() as session:
        return dashboard_metrics(session, verifier_id=1, today=TODAY, **kwargs)


def test_volume_frequency_bonuses_and_cap():
    assert student_practice_rating(0, 0, False, False) == 0
    assert student_practice_rating(120, 1, False, False) == 96.25
    assert student_practice_rating(120, 4, False, False) == 100
    assert student_practice_rating(120, 4, True, False) == 103
    assert student_practice_rating(120, 4, False, True) == 101
    assert student_practice_rating(120, 4, True, True) == 104
    assert student_practice_rating(999, 7, True, True) == 104
    assert student_practice_rating(60, 1, False, False) == 48.125


@pytest.mark.parametrize("delta,direction", [(3, "steady"), (-3, "steady"), (0, "steady"),
                                           (3.001, "up"), (-3.001, "down")])
def test_trend_thresholds(delta, direction):
    assert trend(delta)["direction"] == direction


def test_week_lifetime_categories_and_duplicate_reviews(roster_db):
    student = add_student(roster_db, "Metrics")
    for day, minutes, status in [(6, 11, "approved"), (7, 20, "approved"),
                                 (8, 30, "pending"), (9, 40, "rejected"), (14, 99, None)]:
        add_chart(roster_db, student, date(2026, 9, day), minutes,
                  status=status, include_contests=False)
    with roster_db() as session:
        verified = session.scalar(select(PracticeChart).where(PracticeChart.minutes == 20))
        session.add(PracticeChartVerification(practice_chart_id=verified.id, verifier_id=2, status="approved"))
        pristine = PracticeChart(profile_id=student, practice_date=TODAY, minutes=5,
                                 source="pristine", instrument="Trumpet", detected_playing_seconds=300)
        session.add(pristine)
        session.flush()
        session.add(PracticeChartVerification(practice_chart_id=pristine.id, verifier_id=1, status="approved"))
        session.commit()
    current = metrics(roster_db)
    row = current["students"][0]
    assert row["weekly"] == {"total": 95, "verified": 20, "pristine": 5, "charts": 4, "days": 3,
        "total_seconds": 5700, "verified_seconds": 1200, "pristine_seconds": 300}
    assert row["lifetime"] == {"total": 205, "verified": 31, "pristine": 5, "charts": 6, "days": 5,
        "total_seconds": 12300, "verified_seconds": 1860, "pristine_seconds": 300}
    past = metrics(roster_db, selected_week=date(2026, 8, 31))
    assert past["students"][0]["weekly"]["total"] == 11
    assert past["students"][0]["lifetime"] == row["lifetime"]
    assert current["previous_week"] == date(2026, 8, 31)
    assert past["next_week"] == date(2026, 9, 7)


def test_completed_rating_four_week_baseline_and_zero_students(roster_db):
    student = add_student(roster_db, "Practicing")
    add_student(roster_db, "Zero")
    add_chart(roster_db, student, date(2026, 8, 31), 120)
    for offset in range(1, 5):
        add_chart(roster_db, student, date(2026, 8, 31) - timedelta(weeks=offset), 60)
    add_chart(roster_db, student, TODAY, 999, status="approved")
    current = metrics(roster_db)
    historical = metrics(roster_db, selected_week=date(2026, 8, 31))
    assert current["program_rating"] == historical["program_rating"] == 96.25 / 2
    assert current["program_trend"]["delta"] == (96.25 - 48.125) / 2
    assert current["students"][0]["weekly"]["total"] == 999
    assert historical["students"][0]["weekly"]["total"] == 120
    assert current["students"][1]["rating"] == 0
    # Adding current-week work never changes rating or trend.
    add_chart(roster_db, student, TODAY, 120)
    assert metrics(roster_db)["program_trend"] == current["program_trend"]
    # An earlier selected completed week has its own rating and baseline.
    earlier = metrics(roster_db, selected_week=date(2026, 8, 24))
    assert earlier["program_rating"] == 48.125 / 2
    assert earlier["program_trend"]["delta"] == (48.125 - 3 * 48.125 / 4) / 2


def test_program_baseline_recomputes_same_authorized_cohort_after_roster_change(roster_db):
    first = add_student(roster_db, "First")
    second = add_student(roster_db, "Second")
    hidden = add_student(roster_db, "Hidden", verifier_id=2)
    for student, now, before in [(first, 120, 60), (second, 60, 120), (hidden, 999, 999)]:
        add_chart(roster_db, student, date(2026, 8, 31), now)
        for offset in range(1, 5):
            add_chart(roster_db, student, date(2026, 8, 31) - timedelta(weeks=offset), before)
    both = metrics(roster_db)
    assert both["program_trend"]["delta"] == 0
    with roster_db() as session:
        connection = session.scalar(select(StudentVerifierConnection).where(StudentVerifierConnection.profile_id == second))
        connection.status = "disconnected"
        session.commit()
    only_first = metrics(roster_db)
    assert only_first["program_rating"] == 96.25
    assert only_first["program_trend"]["delta"] == 96.25 - 48.125
    assert [row["display_name"] for row in only_first["students"]] == ["First"]


def test_rating_exceeds_100_and_counts_distinct_days(roster_db):
    student = add_student(roster_db, "Full")
    for day in range(31, 35):
        add_chart(roster_db, student, date(2026, 8, 31) + timedelta(days=day - 31), 30, status="approved")
    with roster_db() as session:
        session.add(PracticeChart(profile_id=student, practice_date=date(2026, 9, 1),
                                 minutes=5, source="pristine", instrument="Trumpet", detected_playing_seconds=300))
        session.commit()
    assert metrics(roster_db)["program_rating"] == 104


def test_week_validation_and_scoped_availability(roster_db):
    allowed = add_student(roster_db, "Allowed")
    hidden = add_student(roster_db, "Hidden", verifier_id=2)
    add_chart(roster_db, allowed, date(2026, 8, 31), 20)
    add_chart(roster_db, hidden, date(2020, 1, 1), 999)
    assert metrics(roster_db)["weeks"] == [date(2026, 9, 7), date(2026, 8, 31)]
    for day in [date(2026, 9, 14), date(2026, 9, 8), date(2020, 1, 1)]:
        with pytest.raises(ValueError):
            metrics(roster_db, selected_week=day)
    assert signed_client().get("/band-director/dashboard?week=2100-01-04").status_code == 400
    assert signed_client().get("/band-director/dashboard?week=bad").status_code == 422


class HeaderParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headers = []
        self.sort_types = []
        self.active = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button" and "data-sort" in attrs:
            self.active = True
            self.sort_types.append(attrs["data-sort"])

    def handle_endtag(self, tag):
        if tag == "button":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.headers.append(data)


def test_rendered_table_contract_and_week_request(roster_db):
    student = add_student(roster_db, "Visible")
    add_chart(roster_db, student, date(2026, 8, 31), 42)
    response = signed_client().get("/band-director/dashboard?week=2026-08-31")
    assert response.status_code == 200
    assert response.context["students"][0]["weekly"]["total"] == 42
    parser = HeaderParser()
    parser.feed(response.text)
    assert parser.headers == ["Name", "Rating", "Trend", "Total Time Wk", "Verified Time Wk",
                              "Pristine Time Wk", "Total Time Life", "Verified Time Life",
                              "Pristine Time Life", "Charts Wk", "Charts Life", "Team"]
    assert parser.sort_types == ["text"] + ["number"] * 10 + ["text"]
    for forbidden in ["Recent P-Charts", "response_note", "rating_week", "baseline", "core score"]:
        assert forbidden not in response.text
    assert '<option value="2026-08-31" selected>' in response.text
    assert "Aug 31–Sep 6, 2026" in response.text
    assert 'onchange="this.form.requestSubmit()"' in response.text
    assert ">Go</button>" not in response.text


@pytest.mark.parametrize("kwargs,visible", [
    ({}, True), ({"role": "parent"}, False), ({"role": "guardian"}, False),
    ({"role": "private_teacher"}, False), ({"status": "pending"}, False),
    ({"status": "rejected"}, False), ({"active": False}, False), ({"verifier_id": 2}, False),
])
def test_conditional_trusted_verifier_navigation(roster_db, kwargs, visible):
    add_student(roster_db, "Connected", **kwargs)
    html = signed_client().get("/trusted-verifiers/dashboard").text
    assert ('href="/band-director/dashboard"' in html) is visible


@pytest.mark.parametrize("width", [390, 1440])
def test_browser_table_layout_and_sorting(roster_db, tmp_path, width):
    """Exercise rendered roster, shared styles and dashboard JS in Chromium."""
    import json
    import re
    import shutil
    import subprocess
    from pathlib import Path

    chrome = shutil.which("google-chrome")
    if not chrome or not shutil.which("node"):
        pytest.skip("Chromium and Node are required for the browser smoke check")
    for index in range(100):
        student = add_student(roster_db, f"Musician {index:03}")
        if index < 2:
            add_chart(roster_db, student, TODAY, 100 if index == 0 else 9)
    with roster_db() as session:
        first = session.scalar(select(PracticeChart).where(PracticeChart.minutes == 100))
        for seconds in (59, 59):
            session.add(PracticeChart(profile_id=first.profile_id, practice_date=TODAY,
                minutes=0, source="pristine", instrument="Trumpet", detected_playing_seconds=seconds))
        session.commit()
    html = signed_client().get("/band-director/dashboard?week=2026-09-07").text
    assert "1 hour 41 minutes 58 seconds" in html
    # Load the actual shared CSS and dashboard assets without unrelated app
    # scripts/network requests. The authenticated response supplies the DOM.
    html = re.sub(r'<script src="[^"]+"></script>', "", html)
    html = html.replace("<head>", "<head><script>window.dashboardErrors=[]; "
                        "window.addEventListener('error', e => dashboardErrors.push(e.message));</script>")
    html = re.sub(r'<link\s+rel="stylesheet"\s+href="/([^"?]+)[^"]*"\s*/?>',
                  lambda match: "<style>" + Path(match[1]).read_text() + "</style>", html)
    html = html.replace("</body>", "<script>" + Path("static/js/band-director-dashboard.js").read_text() + "</script></body>")
    driver = r"""
const { spawn } = require("node:child_process");
let input = "";
process.stdin.on("data", chunk => input += chunk);
process.stdin.on("end", async () => {
  const config = JSON.parse(input);
  const chrome = spawn(config.chrome, ["--headless", "--no-sandbox",
    "--remote-debugging-pipe", "--no-first-run", "--disable-background-networking",
    "--user-data-dir=" + config.profile], { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });
  let sequence = 0, buffer = "";
  const pending = new Map();
  const timer = setTimeout(() => { chrome.kill(); process.exit(2); }, 20000);
  chrome.stdio[4].on("data", chunk => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf("\0")) >= 0) {
      const message = JSON.parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 1);
      const waiter = pending.get(message.id);
      if (waiter) {
        pending.delete(message.id);
        message.error ? waiter.reject(message.error) : waiter.resolve(message.result);
      }
    }
  });
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + "\0");
  });
  try {
    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    await send("Emulation.setDeviceMetricsOverride", {
      width: config.width, height: 844, deviceScaleFactor: 1, mobile: false,
    }, sessionId);
    await send("Runtime.evaluate", { expression: "document.open(); document.write(" + JSON.stringify(config.html) + "); document.close();" }, sessionId);
    const result = await send("Runtime.evaluate", { returnByValue: true, expression: `(() => {
      const table = document.querySelector('[data-director-table]');
      const scroll = document.querySelector('.bd-table-scroll');
      const buttons = table.querySelectorAll('thead button');
      const count = table.tBodies[0].rows.length;
      buttons[3].click(); buttons[3].click();
      const totals = [...table.tBodies[0].rows].map(row => Number(row.cells[3].dataset.sortValue));
      const heading = document.querySelector('.bd-heading').getBoundingClientRect();
      const rating = document.querySelector('.bd-rating');
      rating.firstElementChild.textContent = '104.0';
      const circle = rating.getBoundingClientRect();
      const programArrow = document.querySelector('.bd-rating-row > .bd-trend').getBoundingClientRect();
      const fits = [...rating.children].every(child => {
        const box = child.getBoundingClientRect();
        return box.left >= circle.left && box.right <= circle.right;
      });
      scroll.scrollLeft = 500;
      return { count, columns: buttons.length, totals: totals.slice(0, 3),
        width: innerWidth, pageWidth: document.documentElement.scrollWidth,
        scrollable: scroll.scrollWidth > scroll.clientWidth,
        firstColumnX: table.tBodies[0].rows[0].cells[0].getBoundingClientRect().left,
        scrollX: scroll.getBoundingClientRect().left,
        aligned: Math.abs(heading.left - circle.left) < 1,
        circleWidth: circle.width, circleHeight: circle.height, fits,
        arrowBeside: programArrow.left > circle.right,
        errors: window.dashboardErrors || [] };
    })()` }, sessionId);
    if (result.exceptionDetails) throw result.exceptionDetails;
    process.stdout.write(JSON.stringify(result.result.value));
  } catch (error) {
    process.stderr.write(JSON.stringify(error));
    process.exitCode = 1;
  } finally { clearTimeout(timer); chrome.kill(); }
});
"""
    result = subprocess.run(["node", "-e", driver], input=json.dumps({
        "chrome": chrome, "width": width, "profile": str(tmp_path / "chrome"), "html": html,
    }), text=True, capture_output=True, timeout=30, check=True)
    values = json.loads(result.stdout)
    assert values["width"] == width
    assert values["pageWidth"] <= width
    assert values["count"] == 100 and values["columns"] == 12
    assert values["totals"] == [6118 / 60, 9, 0]
    assert values["aligned"] and values["circleWidth"] == values["circleHeight"]
    assert values["fits"]
    assert values["arrowBeside"]
    assert abs(values["firstColumnX"] - values["scrollX"]) <= 2
    if width == 390:
        assert values["scrollable"]
    assert values["errors"] == []
