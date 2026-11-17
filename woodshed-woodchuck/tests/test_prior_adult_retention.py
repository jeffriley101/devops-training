"""Prior spent-token disposal needs the verified original adult receipt and exact target."""
from datetime import timedelta
import json

import pytest

from app.models import TrustedVerifier, TrustedVerifierInvitation
from app.parent_deletion_support import PRIOR_ADULT_ACTION, SupportDenied
from tests.test_account_deletion import profile
from tests.test_adult_deletion_support import adult_case


@pytest.fixture
def previous_adult(play_pg, adult_case):
    c = adult_case
    now = c.now + timedelta(days=39)
    with play_pg.begin() as session:
        child = profile(session, 9)
        rows = []
        for adult, token in ((c.adult, "b"), (c.colleague, "c")):
            row = TrustedVerifierInvitation(profile_id=child.id, email=adult.email, role="verifier", status="accepted",
                    accepted_at=c.now - timedelta(days=3), accepted_verifier_id=adult.id, token_hash=token * 64,
                    expires_at=c.now - timedelta(days=2), updated_at=c.now - timedelta(days=3))
            session.add(row)
            rows.append(row)
    deleted = c.run()
    # Model an accepted credential still retained from earlier ordinary cleanup.
    with play_pg.begin() as session:
        row = session.get(TrustedVerifierInvitation, rows[0].id)
        row.token_hash = "b" * 64
        row.updated_at = rows[0].updated_at
    source = c.case["case"]
    case = {
        "case": "prior-adult-001", "action": PRIOR_ADULT_ACTION, "status": "approved",
        "verifier_id": c.adult.id, "verifier_created_at": c.case["verifier_created_at"],
        "deleted_at": deleted["deleted_at"], "deletion_evidence_ref": source, "evidence_kind": "support_receipt",
        "verified_by": "independent-reviewer", "verified_at": (now - timedelta(minutes=2)).isoformat(),
    }
    approvals = c.register.parent / "approvals"
    (approvals / (case["case"] + ".lock")).touch(mode=0o600)

    def clean(record=None, **options):
        path = approvals / (case["case"] + ".json")
        path.write_text(json.dumps(case if record is None else record))
        path.chmod(0o600)
        return c.run(prior=True, reference=case["case"], clock=lambda: now, **options)

    return c, case, rows, clean


def test_scoped_prior_adult_disposal_preserves_attribution_others_and_original_receipt(play_pg, previous_adult):
    c, case, rows, clean = previous_adult
    receipt = c.register / (c.case["case"] + ".result.json")
    original = receipt.read_bytes()
    with pytest.raises(SupportDenied, match="ordinary_cleanup_incomplete"):
        c.run(status_only=True)
    with play_pg() as session:
        tombstone = session.get(TrustedVerifier, c.adult.id)
        before = (tombstone.email, tombstone.pin_hash, tombstone.created_at, tombstone.updated_at)
        original_invites = [dict(session.execute(row.__table__.select().where(row.__table__.c.id == row.id)).mappings().one())
                            for row in rows]
    result = clean()
    assert result["status"] == "prior_adult_spent_invitations_verified" and result["deleted_at"] == case["deleted_at"]
    assert clean(status_only=True) == clean() == result
    assert c.run(status_only=True)["status"] == "adult_deletion_applied"
    assert receipt.read_bytes() == original
    with play_pg() as session:
        after = session.get(TrustedVerifier, c.adult.id)
        assert (after.email, after.pin_hash, after.created_at, after.updated_at) == before
        for index, row in enumerate(rows):
            current = dict(session.execute(row.__table__.select().where(row.__table__.c.id == row.id)).mappings().one())
            expected = original_invites[index]
            if index == 0:
                expected["token_hash"] = f"retired:{row.id}"
            assert current == expected


@pytest.mark.parametrize("problem", ["wrong_adult", "clock", "missing_receipt", "external_claim", "mismatched_tombstone"])
def test_prior_adult_refuses_missing_or_mismatched_original_evidence(play_pg, previous_adult, problem):
    c, case, rows, clean = previous_adult
    wrong = case.copy()
    if problem == "wrong_adult":
        wrong["verifier_id"] = c.colleague.id
    elif problem == "clock":
        wrong["deleted_at"] = (c.now - timedelta(minutes=5)).isoformat()
    elif problem == "missing_receipt":
        wrong["deletion_evidence_ref"] = "never-executed-case"
    elif problem == "external_claim":
        wrong["evidence_kind"] = "verified_deletion_record"
    else:
        with play_pg.begin() as session:
            session.get(TrustedVerifier, c.adult.id).pin_hash = "deleted$" + "e" * 64
    with pytest.raises(SupportDenied):
        clean(wrong)
    assert not (c.register / (case["case"] + ".intent.json")).exists()
    with play_pg() as session:
        assert session.get(TrustedVerifierInvitation, rows[0].id).token_hash == "b" * 64
