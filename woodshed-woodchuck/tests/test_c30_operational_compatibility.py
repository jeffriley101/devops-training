"""PTA continuity on real additive migrations; immutable evidence, no live gates."""
from copy import deepcopy
from datetime import timedelta

from alembic import command
import pytest
from sqlalchemy import MetaData, select, text

from app.operator_weekly_contract import PROFILE_COLUMNS_V1, PROFILE_COLUMNS_W25
from tests.test_c22_operational_compatibility import all_rows, calendar, config, cutover, set_clock
from tests.test_persistent_team_cutover import BOUNDARY_AT, NOW, activate, plan, stage
from tests.test_persistent_team_migration import disposable_sqlite_configuration
from tests.test_weekly2_operator_compatibility import install, migrated_db, populate_weekly
from tests.pta_legacy_receipts import activate_legacy_fixture
from app import pta_receipt_schema as issuance


CLASSROOM = tuple(f"c{number}class001" for number in range(25, 31))
LINEAGE = ("p21team001", "c22class001", "c23class001", "c24class001", "w25weekly005") + CLASSROOM + (issuance.REVISION,)
FULL_CONTRACT = "pta-w25weekly005-v1"


def install_at(db, revision):
    if revision in CLASSROOM:
        install(db, "w25weekly005")
        command.upgrade(config(), revision)
    else:
        install(db, revision)


def populate_extensions(db):
    """Populate installed sharing/consent tables, without ORM schema inference.

    These are synthetic preservation rows, not student authentication or grants
    obtained through a public flow. No Classroom feature gate is enabled.
    """
    with db[1].begin() as c:
        metadata = MetaData()
        metadata.reflect(bind=c, resolve_fks=False)
        tables = metadata.tables

        def first(name):
            return c.execute(select(tables[name])).mappings().first()

        room = first("classroom_classes")
        assignment = first("classroom_teaching_assignments")
        member = first("classroom_student_memberships")
        period = first("classroom_membership_periods")
        entitlement = first("classroom_entitlements")
        scope = dict(class_id=room["id"], program_id=room["program_id"])
        values = dict(chart_id=1, class_id=room["id"], assignment_id=assignment["id"],
            membership_period_id=period["id"], entitlement_id=entitlement["id"],
            account_declared_at=NOW, class_activation_at=NOW, selected_at=NOW)
        c.execute(tables["classroom_chart_shares"].insert().values(
            chart_id=1, class_id=room["id"], selected_at=NOW))
        c.execute(tables["classroom_chart_director_shares"].insert().values(**values))
        if "classroom_student_invitations" in tables:
            invitations = tables["classroom_student_invitations"]
            values = dict(**scope, assignment_id=assignment["id"], profile_id=24,
                issued_by_verifier_id=assignment["verifier_id"], issued_at=NOW,
                expires_at=NOW + timedelta(days=7), accepted_at=NOW, accepted_by_profile_id=24)
            if "deadline_accepted_at" in invitations.c:
                values["deadline_accepted_at"] = NOW
            invite = c.execute(invitations.insert().values(**values)).inserted_primary_key[0]
            shares = tables["classroom_chart_director_shares"]
            if "invitation_id" in shares.c:
                c.execute(shares.insert().values(chart_id=2, class_id=room["id"],
                    assignment_id=assignment["id"], invitation_id=invite,
                    entitlement_id=entitlement["id"], account_declared_at=NOW,
                    class_activation_at=NOW, selected_at=NOW))
        if "classroom_progress_authorizations" in tables:
            authorizations = tables["classroom_progress_authorizations"]
            values = dict(**scope, profile_id=24, membership_id=member["id"],
                membership_period_id=period["id"], entitlement_id=entitlement["id"],
                account_declared_at=NOW, class_activation_at=NOW,
                scope_version="synthetic-preservation", notice_version="synthetic-preservation",
                notice_digest="a" * 64, accepted_at=NOW, accepted_by_profile_id=24,
                acceptance_session_version=0)
            if "source_revision" in authorizations.c:
                values["source_revision"] = "b" * 64
            authorization = c.execute(authorizations.insert().values(**values)).inserted_primary_key[0]
            c.execute(tables["classroom_progress_recipients"].insert().values(
                **scope, authorization_id=authorization, assignment_id=assignment["id"]))
    rows = all_rows(db[1])
    assert all(values for name, values in rows.items() if name.startswith("classroom_"))


def verify(db, approved, receipt):
    return cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)


@pytest.mark.parametrize("revision", CLASSROOM)
def test_fresh_full_cycle_preserves_every_extension(migrated_db, monkeypatch, revision):
    db = migrated_db
    install_at(db, revision)
    populate_weekly(db)
    populate_extensions(db)
    set_clock(monkeypatch, NOW)
    before = all_rows(db[1])
    approved = plan(db)
    original = deepcopy(approved)
    assert approved["content"]["revision"] == revision
    calendar.provision(db[0], seasons=["halloween-2026"])
    assert all_rows(db[1]) == before
    stage(db, approved)
    command.upgrade(config(), issuance.REVISION)
    before = all_rows(db[1])
    receipt = activate(db, approved)
    after = all_rows(db[1])
    verified = verify(db, approved, receipt)
    assert verified["passed"] and all_rows(db[1]) == after
    assert receipt["verification"]["evidence_contract"] == FULL_CONTRACT
    assert set(after["woodchuck_profiles"][0]) == set(PROFILE_COLUMNS_W25)
    assert verified["receipt_coverage"] == dict(contract=FULL_CONTRACT,
        tables=list(cutover.TABLES), excluded_added_columns=[], extension_rows_attested=False)
    assert approved == original
    control = after["persistent_team_control"][0]
    assert control["staged_plan"] == original
    assert control["staged_plan_sha256"] == original["plan_sha256"]
    assert control["activated_at"] == cutover.normalized(BOUNDARY_AT)
    changed = {"teams", "team_memberships", "contest_weeks", "persistent_team_control",
               "team_week_membership_snapshots", issuance.NAME}
    for name in before.keys() - changed:
        assert after[name] == before[name], name
    calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
    final = all_rows(db[1])
    for name in before.keys() - {"contest_weeks"}:
        assert final[name] == after[name], name
    assert calendar.provision(db[0], apply=True, seasons=["halloween-2026"])["status"] == "ALREADY_COMPLETE"
    assert all_rows(db[1]) == final


@pytest.mark.parametrize("original", LINEAGE[:-1])
@pytest.mark.parametrize("state", ["unstaged", "staged", "activated"])
def test_immutable_approval_and_receipt_across_upgrade(migrated_db, original, state):
    db = migrated_db
    install_at(db, original)
    approved = plan(db)
    original_plan = deepcopy(approved)
    if state != "unstaged":
        stage(db, approved)
    receipt = activate_legacy_fixture(db, approved) if state == "activated" else None
    original_receipt = deepcopy(receipt)
    command.upgrade(config(), issuance.REVISION)
    before = all_rows(db[1])
    if state == "unstaged":
        with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
            stage(db, approved)
        assert all_rows(db[1]) == before
        assert plan(db)["plan_sha256"] != approved["plan_sha256"]
    else:
        if state == "staged":
            receipt = activate(db, approved)
            assert receipt["verification"]["evidence_contract"] == FULL_CONTRACT
        after = all_rows(db[1])
        if state == "activated":
            with pytest.raises(cutover.CutoverError, match="trusted_receipt_issuance_required"):
                verify(db, approved, receipt)
            assert after[issuance.NAME] == []
        else:
            result = verify(db, approved, receipt)
            assert result["passed"]
        assert all_rows(db[1]) == after
        assert after["persistent_team_control"][0]["staged_plan"] == original_plan
        assert receipt["verification"]["activated_at"] == original_plan["content"]["effective_at"]
        assert approved["content"]["target"] == original_plan["content"]["target"]
        if state == "activated":
            assert receipt == original_receipt
            legacy = original in LINEAGE[:4]
            if legacy:
                profiles = [{key: row[key] for key in PROFILE_COLUMNS_V1}
                            for row in after["woodchuck_profiles"]]
                assert receipt["verification"]["after"]["woodchuck_profiles"] == {
                    "count": len(profiles), "sha256": cutover.digest(profiles)}
    assert approved == original_plan


@pytest.mark.parametrize("original", CLASSROOM)
def test_pre_repair_unmarked_classroom_receipt_is_full_coverage(migrated_db, original):
    db = migrated_db
    install_at(db, original)
    stage(db, approved := plan(db))
    receipt = activate_legacy_fixture(db, approved)
    assert "evidence_contract" not in receipt["verification"]
    frozen = deepcopy((approved, receipt))
    command.upgrade(config(), issuance.REVISION)
    before = all_rows(db[1])
    assert receipt["verification"]["after"] == cutover._evidence(
        {name: before[name] for name in cutover.TABLES})
    with pytest.raises(cutover.CutoverError, match="trusted_receipt_issuance_required"):
        verify(db, approved, receipt)
    assert before[issuance.NAME] == []
    assert all_rows(db[1]) == before and (approved, receipt) == frozen
    with db[1].begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET game_mode='Challenge' WHERE id=24"))
    changed = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match="trusted_receipt_issuance_required"):
        verify(db, approved, receipt)
    assert all_rows(db[1]) == changed and (approved, receipt) == frozen


@pytest.mark.parametrize("original", ["c24class001", "w25weekly005", "c30class001"])
def test_coverage_never_falls_back_or_ignores_covered_changes(migrated_db, original):
    db = migrated_db
    install_at(db, original)
    stage(db, approved := plan(db))
    command.upgrade(config(), issuance.REVISION)
    receipt = activate(db, approved)
    frozen = deepcopy((approved, receipt))
    bad = deepcopy(receipt)
    bad["verification"]["evidence_contract"] = "forged-contract"
    with pytest.raises(cutover.CutoverError, match="activation_receipt_issuance_mismatch"):
        verify(db, approved, bad)
    if original == "w25weekly005":
        missing = deepcopy(receipt)
        del missing["verification"]["evidence_contract"]
        with pytest.raises(cutover.CutoverError, match="activation_receipt_issuance_mismatch"):
            verify(db, approved, missing)
    if original == "c24class001":
        bad["verification"]["evidence_contract"] = "pta-p21-through-c24-original"
        bad["verification"]["after"]["woodchuck_profiles"] = approved["content"]["before"]["woodchuck_profiles"]
        with pytest.raises(cutover.CutoverError, match="activation_receipt_issuance_mismatch"):
            verify(db, approved, bad)
    with db[1].begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET game_mode='Challenge', first_choice_instrument='Flute' WHERE id=24"))
    with pytest.raises(cutover.CutoverError, match="post_activation_state_changed"):
        verify(db, approved, receipt)
    with db[1].begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET display_name='covered-change' WHERE id=24"))
    changed = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match="post_activation_state_changed"):
        verify(db, approved, receipt)
    assert all_rows(db[1]) == changed and (approved, receipt) == frozen


def test_revision_matrix_is_explicit_forward_only():
    for source_index, source in enumerate(LINEAGE):
        for target_index, target in enumerate(LINEAGE):
            for staging in (False, True):
                if source_index > target_index or (staging and source != target):
                    with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
                        cutover._plan_schema_guard({"revision": source}, target, staging=staging)
                else:
                    cutover._plan_schema_guard({"revision": source}, target, staging=staging)
    for bad in ("unknown", "c31class001", "w25weekly001", "w25weekly004"):
        for source, target in ((bad, "c30class001"), ("c30class001", bad), (bad, bad)):
            with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
                cutover._plan_schema_guard({"revision": source}, target)


@pytest.mark.parametrize("operation,mutation", [
    ("stage", "UPDATE classroom_progress_authorizations SET source_revision='x'"),
    ("activate", "UPDATE classroom_student_invitations SET revoked_at=issued_at, revoked_by_verifier_id=issued_by_verifier_id"),
    ("activate", "DELETE FROM classroom_chart_director_shares"),
    ("calendar", "DELETE FROM classroom_progress_recipients"),
    ("calendar", "UPDATE weekly_plans SET generator_version='unexpected'"),
])
def test_forbidden_operator_mutation_rolls_back_all_rows(migrated_db, monkeypatch, operation, mutation):
    db = migrated_db
    install_at(db, "c30class001")
    command.upgrade(config(), issuance.REVISION)
    populate_weekly(db)
    populate_extensions(db)
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    if operation == "activate":
        stage(db, approved)
    target, action = ("contest_weeks", "INSERT") if operation == "calendar" else ("persistent_team_control", "UPDATE")
    with db[1].begin() as c:
        if c.dialect.name == "postgresql":
            c.execute(text(f"CREATE FUNCTION forbidden_c30_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {mutation}; RETURN NEW; END $$"))
            c.execute(text(f"CREATE TRIGGER forbidden_c30_write AFTER {action} ON {target} FOR EACH ROW EXECUTE FUNCTION forbidden_c30_write()"))
        else:
            c.execute(text(f"CREATE TRIGGER forbidden_c30_write AFTER {action} ON {target} BEGIN {mutation}; END"))
    before = all_rows(db[1])
    with pytest.raises(cutover.inventory.InventoryError, match="unexpected_row_change|unexpected_classroom_row_change"):
        if operation == "stage":
            stage(db, approved)
        elif operation == "activate":
            activate(db, approved)
        else:
            calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
    assert all_rows(db[1]) == before


@pytest.mark.parametrize("damage", ["hash", "target", "authority", "boundary", "unknown", "reverse", "false_stamp"])
def test_invalid_approvals_and_schema_refuse_without_writes(migrated_db, damage):
    db = migrated_db
    install_at(db, "w25weekly005")
    stage(db, approved := plan(db))
    command.upgrade(config(), issuance.REVISION)
    if damage == "hash":
        approved["content"]["team_ids"] = []
    elif damage in {"target", "authority", "boundary"}:
        # Rehashing a forged file does not change the actual stored approval.
        key = {"target": "target", "authority": "authority_sha256", "boundary": "effective_at"}[damage]
        approved["content"][key] = {} if damage == "target" else "forged"
        approved["plan_sha256"] = cutover.digest(approved["content"])
    else:
        with db[1].begin() as c:
            stamp = {"unknown": "c31class001", "reverse": "c24class001", "false_stamp": "c30class001"}[damage]
            c.execute(text("UPDATE alembic_version SET version_num=:stamp"), {"stamp": stamp})
            if damage == "false_stamp":
                c.execute(text("DROP INDEX uq_classroom_progress_open"))
    before = all_rows(db[1])
    with pytest.raises(cutover.inventory.InventoryError):
        activate(db, approved)
    assert all_rows(db[1]) == before
