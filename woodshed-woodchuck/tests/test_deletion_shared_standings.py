from datetime import timedelta
import json

import pytest
from sqlalchemy import select, text

from app.account_deletion import DELETED_PUBLIC_NAME, DeletionDataConflict
from app.age_privacy import declare_age
from app.contests import contest_results_payload, ensure_band_camp_data, finalize_contest_week, team_leaderboards
from app.models import ContestResult, ContestWeek, PracticeChart, RewardGrant, Season, Team, TeamReport, WoodchuckProfile
from app.parent_deletion_support import PRIOR_ACTION
from app.teams import create_and_join_team, select_team
from tests.test_associated_disposal_support import qualify_student_disposal
from tests.test_parent_deletion_support import support_case
from tests.test_team_contests import FINAL_NOW, NOW, add_chart


def test_student_deletion_and_prior_cleanup_preserve_shared_standings(play_pg, support_case):
    run, publish, case, register, teammate_id, role = support_case
    qualify_student_disposal(play_pg, role)
    profile_id = case["profile_id"]
    with play_pg() as session:
        season, _, week = ensure_band_camp_data(session, now=NOW)
        student, teammate = [session.get(WoodchuckProfile, pid) for pid in (profile_id, teammate_id)]
        for member in (student, teammate):
            member.created_at = NOW - timedelta(days=30)
            declare_age(session, member.id, "13to17", at=member.created_at)
        case["profile_created_at"] = student.created_at.isoformat()
        publish(case)
        team, _ = create_and_join_team(session, profile=student, season=season, name="Flute Team",
                                       emblem_key="shield:gold", now=NOW)
        select_team(session, profile=teammate, season=season, team=team, now=NOW)
        chart = add_chart(session, student, team.id, 45, created_at=NOW)
        add_chart(session, teammate, team.id, 30, created_at=NOW)
        session.commit()
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()

    def read_standings():
        with play_pg() as session:
            period = session.get(ContestWeek, week.id)
            results = [row for row in contest_results_payload(session, period)["results"]
                       if row["subject_type"] in ("team", "instrument")]
            board = team_leaderboards(session, season=session.get(Season, season.id), contest_week=period)
            awards = session.execute(select(RewardGrant.__table__).where(
                RewardGrant.profile_id == teammate_id).order_by(RewardGrant.id)).all()
            return results, board, awards

    baseline = read_standings()
    assert {row["subject_type"] for row in baseline[0]} == {"instrument", "team"}
    assert baseline[1]["team-weekly-practice"]["open"][0]["score"] == 75
    assert baseline[2]
    original = run()
    assert set(original["disposal"].values()) == {"verified"}
    assert read_standings() == baseline

    # An older generation can have private notes alongside correct, finalized scores.
    with play_pg.begin() as session:
        session.get(PracticeChart, chart.id).note = "Synthetic private residue"
        session.get(Team, team.id).creator_profile_id = profile_id
        report = TeamReport(team_id=team.id, category="other", reporter_profile_id=profile_id)
        session.add(report)
        result = session.scalars(select(ContestResult).where(
            ContestResult.subject_type == "student", ContestResult.profile_id == profile_id,
        )).first()
        result.display_name_snapshot = "Synthetic former name"
        result.subject_key = "conflicting-subject"
    ref = "original-standings-cleanup"
    approval = {
        "case": ref, "status": "approved", "action": PRIOR_ACTION,
        "profile_id": profile_id, "woodchuck_id": case["woodchuck_id"],
        "profile_created_at": case["profile_created_at"], "deleted_at": original["deleted_at"],
        "evidence_kind": "support_receipt", "deletion_evidence_ref": case["case"],
        "verified_at": original["deleted_at"], "verified_by": "case-reviewer",
    }
    approvals = register.parent / "approvals"
    (approvals / (ref + ".lock")).touch(mode=0o600)
    record = approvals / (ref + ".json")
    record.write_text(json.dumps(approval))
    record.chmod(0o600)
    with pytest.raises(DeletionDataConflict, match="contest result"):
        run(reference=ref, prior=True)
    with play_pg.begin() as session:
        assert session.get(TeamReport, report.id).reporter_profile_id == profile_id
        assert session.get(PracticeChart, chart.id).note == "Synthetic private residue"
        session.get(ContestResult, result.id).subject_key = str(profile_id)
    assert run(reference=ref, prior=True)["status"] == "prior_ordinary_cleanup_verified"
    assert read_standings() == baseline
    with play_pg() as session:
        assert session.get(PracticeChart, chart.id).note is None
        assert session.get(TeamReport, report.id).reporter_profile_id is None
        assert session.get(Team, team.id).creator_profile_id is None
        assert session.get(ContestResult, result.id).display_name_snapshot == DELETED_PUBLIC_NAME
    with role.connect() as executor:
        assert executor.execute(text("""
            SELECT has_column_privilege('contest_results','subject_key','UPDATE'),
                   has_table_privilege('reward_grants','DELETE'),
                   has_column_privilege('team_memberships','profile_id','UPDATE')
        """)).one() == (False, False, False)
