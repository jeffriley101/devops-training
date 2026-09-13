import csv
from datetime import date
from io import StringIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main
from app.band_director_dashboard import CSV_COLUMNS
from app.models import StudentVerifierConnection
from test_band_director_roster import (roster_db, contest_roster, add_student, add_chart,
                                      join_roster_team, signed_client)

URL = "/band-director/dashboard.csv"


def rows(response):
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    return list(csv.DictReader(StringIO(response.content.decode("utf-8"))))


def test_csv_requires_verifier_login(roster_db):
    response = TestClient(main.app).get(URL, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/trusted-verifiers/login"


def test_csv_authorized_roster_only_and_no_private_fields(contest_roster):
    add_student(contest_roster, 'Zoë, "音楽"')
    for role in ("parent", "guardian", "private_teacher", "coach", "other_trusted_adult"):
        add_student(contest_roster, f"Excluded {role}", role=role)
    for status in ("pending", "rejected", "disconnected"):
        add_student(contest_roster, f"Excluded {status}", status=status)
    add_student(contest_roster, "Excluded inactive", active=False)
    hidden = add_student(contest_roster, "Excluded other verifier", verifier_id=2)
    response = signed_client().get(f"{URL}?verifier_id=2&profile_id={hidden}&search=Excluded&team=Other")
    result = rows(response)
    assert [row["Student"] for row in result] == ['Zoë, "音楽"']
    assert list(result[0]) == list(CSV_COLUMNS)
    assert result[0]["Instrument"] == "Trumpet" and result[0]["Level"] == "Beginner"
    assert result[0]["Team"] == "No team"
    for private in ("Excluded", "@example.com", "private-pin", "profile_id", "verifier_id", "WC-"):
        assert private not in response.text
    assert 'attachment; filename="woodshed-band-director-2026-09-07.csv"' == response.headers["content-disposition"]


def test_csv_empty_and_non_director_verifier_header_only(contest_roster):
    add_student(contest_roster, "Other Student", verifier_id=2)
    add_student(contest_roster, "Parent Student", role="parent")
    response = signed_client().get(URL)
    assert rows(response) == []
    assert list(csv.reader(StringIO(response.text))) == [list(CSV_COLUMNS)]
    assert "Export CSV" in signed_client().get("/band-director/dashboard").text


def test_csv_selected_week_matches_dashboard_and_current_rating_semantics(contest_roster):
    student = add_student(contest_roster, "Practice Student")
    join_roster_team(contest_roster, student, "Current Team")
    add_chart(contest_roster, student, date(2026, 8, 31), 30, status="approved", include_contests=False)
    add_chart(contest_roster, student, date(2026, 9, 7), 120)
    client = signed_client()
    for week, minutes, days, verified in [("2026-08-31", 30, 1, 30), ("2026-09-07", 120, 1, 0)]:
        page = client.get(f"/band-director/dashboard?week={week}")
        metric = page.context["students"][0]
        row = rows(client.get(f"{URL}?week={week}"))[0]
        assert row["Week Start"] == week
        assert row["Week End"] == page.context["week_end"].isoformat()
        assert int(row["Practice Minutes"]) == minutes
        assert int(row["Practice Days"]) == days
        assert int(row["Verified Minutes"]) == verified
        assert int(row["Pristine Minutes"]) == metric["weekly"]["pristine"]
        assert float(row["Practice Rating"]) == metric["rating"]
        assert row["Trend"] == metric["trend"]["label"]
        assert row["Team"] == "Current Team"
        assert f'href="{URL}?week={week}"' in page.text
    for invalid, code in [("2026-08-01", 400), ("2099-01-05", 400), ("invalid", 422)]:
        assert client.get(f"{URL}?week={invalid}").status_code == code


def test_disconnect_revokes_export_without_relogin(contest_roster):
    student = add_student(contest_roster, "Revoke Me")
    client = signed_client()
    assert len(rows(client.get(URL))) == 1
    with contest_roster() as session:
        session.scalar(select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id == student)).status = "disconnected"
        session.commit()
    assert rows(client.get(URL)) == []


@pytest.mark.parametrize("name", ["=1+1", "+SUM(1,2)", "-1+2", "@SUM(1)", "  =1+1", "\tformula"])
def test_csv_neutralizes_spreadsheet_formulas(contest_roster, name):
    add_student(contest_roster, name)
    assert rows(signed_client().get(URL))[0]["Student"] == "'" + name


def test_filter_controls_only_offer_authorized_team_values(contest_roster):
    student = add_student(contest_roster, "Filter Me")
    client = signed_client()
    page = client.get("/band-director/dashboard")
    assert 'id="bd-name-search"' in page.text
    assert 'id="bd-team-filter"' not in page.text
    join_roster_team(contest_roster, student, "Current Team")
    add_student(contest_roster, "No Team Student")
    hidden = add_student(contest_roster, "Other Adult Student", verifier_id=2)
    join_roster_team(contest_roster, hidden, "Snapshot Team")
    page = client.get("/band-director/dashboard")
    assert 'id="bd-team-filter"' in page.text
    assert '<option value="Current Team">' in page.text and '<option value="No team">' in page.text
    assert "Snapshot Team" not in page.text
    assert len(rows(client.get(URL + "?search=Filter Me&team=Current Team"))) == 2
