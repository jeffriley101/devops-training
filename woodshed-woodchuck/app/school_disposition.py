"""Supervised school disposition. No HTTP route or background worker."""
import argparse
import base64
import binascii
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import subprocess
from tempfile import NamedTemporaryFile

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from . import classroom, classroom_school_authorization as school
from . import school_disposition_fields as fields
from .classroom_role_membership import ADMINISTRATOR, UNEXPECTED_MEMBERSHIP
from .age_privacy import utc
from .classroom_models import ClassroomClass, ClassroomMembershipHold, ClassroomStudentMembership
from .classroom_school_models import ClassroomSchoolAuthorization as Receipt
from .support_case_files import SupportDenied, _digest, _encode, _protected_file, _read_json, _reference, _time, _write_new
from .parent_deletion_support import _check_register

CASE_APPROVAL = "woodshed-school-disposition-case-v2"
DESTINATION_APPROVAL = "woodshed-school-disposition-destination-v2"
CONFIG = Path("/etc/woodshed/school-disposition/application.json")
REVISION = "c37hold001"
ACTIONS = {"retire_receipt", "erase_hold_actor", "replace_receipt"} | set(fields.MODELS)


def require(ok, code):
    if not ok:
        raise SupportDenied(code)


def anniversary(date):
    try:
        return date.replace(year=date.year + 1)
    except ValueError:
        raise SupportDenied("disposition_anniversary_requires_policy_review") from None


def eligibility(case, now):
    """A dated review never silently releases an inspection request or legal hold."""
    keys = {"approval_contract", "approved_at", "purpose_ref", "calendar_zone", "exception_owner", "exception_action", "case", "operation", "program", "record", "class", "snapshot", "database", "oid",
            "trigger", "authorization_end", "relationship_end", "purpose_ends", "actor_due", "latest", "blocked", "obligation",
            "replacement_digest", "continuing_basis", "review_ref", "security_ref", "exception_ref", "exception_until", "hold_until"}
    if "destination" in case:
        keys.add("destination")  # Validated against the protected target before eligibility.
    security = "security_anchor" in case
    actor_authorization = "authorization_anchor" in case
    if actor_authorization:
        anchor = case["authorization_anchor"]
        require(not security and case["operation"] in {fields.S1_ACTOR, fields.S2_ACTOR, fields.TERMINAL}
                and case["actor_due"] is False and isinstance(anchor, dict)
                and set(anchor) == {"receipt", "digest", "reference"}
                and type(anchor["receipt"]) is int and anchor["receipt"] > 0
                and isinstance(anchor["digest"], str) and len(anchor["digest"]) == 64
                and _reference(anchor["reference"]), "disposition_authorization_scope_invalid")
        keys.add("authorization_anchor")
    if security:
        keys.add("security_anchor")
    if "event_fields" in case:
        columns = case["event_fields"]
        require(case.get("operation") == fields.S2_ACTOR and isinstance(columns, list)
                and 0 < len(columns) <= len(fields.MEMBERSHIP_EVENT_FIELDS)
                and all(isinstance(name, str) and name in fields.MEMBERSHIP_EVENT_FIELDS for name in columns)
                and len(set(columns)) == len(columns), "disposition_membership_field_scope_invalid")
        keys.add("event_fields")
    if "source_scope_ref" in case:
        require(case.get("operation") == fields.S2_ACTOR and _reference(case["source_scope_ref"]),
                "disposition_source_scope_invalid")
        keys.add("source_scope_ref")
    if case.get("operation") == "replace_receipt":
        keys.add("scope_verification")
        require(isinstance(case.get("scope_verification"), dict)
                and set(case["scope_verification"]) == {"decision", "independent_reference"}
                and case["scope_verification"]["decision"] == "verified_equivalent"
                and _reference(case["scope_verification"]["independent_reference"]),
                "disposition_delegation_clarification_required")
    require(set(case) == keys and _reference(case["case"]) and case["operation"] in ACTIONS,
            "disposition_case_invalid")
    require(all(type(case[k]) is int and case[k] > 0 for k in ("program", "record", "oid"))
            and (case["class"] is None if case["operation"] != "erase_hold_actor" else
                 type(case["class"]) is int and case["class"] > 0), "disposition_scope_invalid")
    require(all(_reference(case[k]) for k in ("review_ref", "security_ref", "obligation", "purpose_ref")),
            "disposition_review_missing")
    require(type(case["blocked"]) is bool and not case["blocked"], "disposition_preservation_required")
    require(_time(case["approved_at"]) <= now, "disposition_approval_in_future")
    try:
        zone = ZoneInfo(case["calendar_zone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise SupportDenied("disposition_calendar_unconfirmed") from None
    trigger, purpose, due = (_time(case[k]) for k in ("trigger", "purpose_ends", "latest"))
    if security:
        anchor = case["security_anchor"]
        require(case["operation"] in {fields.S1_ACTOR, fields.S2_ACTOR} and case["actor_due"] is False
                and case["authorization_end"] is None and case["relationship_end"] is None
                and isinstance(anchor, dict) and set(anchor) == {"event", "closure", "reference"}
                and _reference(anchor["reference"]), "disposition_security_scope_invalid")
        event = _time(anchor["event"])
        closure = _time(anchor["closure"]) if anchor["closure"] is not None else event
        require(event <= closure == trigger, "disposition_security_anchor_changed")
    replacement = case["operation"] == "replace_receipt"
    if replacement:
        require(case["relationship_end"] is None and case["actor_due"] is True
                and isinstance(case["replacement_digest"], str) and len(case["replacement_digest"]) == 64,
                "disposition_replacement_scope")
    else:
        require(case["replacement_digest"] is None, "disposition_replacement_scope")
    authorization_retirement = (actor_authorization or case["operation"] == "retire_receipt" or
                                case["operation"] in fields.AUTHORIZATION_RETIREMENT and case["actor_due"] is False)
    if not authorization_retirement:
        require(case["authorization_end"] is None, "disposition_scope_invalid")
    relationship_end = _time(case["relationship_end"]) if case["relationship_end"] is not None else None
    end = _time(case["authorization_end"]) if authorization_retirement else relationship_end
    if actor_authorization:
        require(end == trigger and purpose >= end and due >= end, "disposition_authorization_anchor_changed")
    if case["operation"] == "erase_hold_actor":
        require(relationship_end is not None, "disposition_scope_invalid")
    require(trigger <= now and (end is None or end <= now) and purpose <= now
            and (relationship_end is None or relationship_end <= now), "disposition_not_eligible")
    require(type(case["actor_due"]) is bool, "disposition_case_invalid")
    # D30 runs from the applicable school trigger. A12 has its own independently
    # verified authorization end. Mixed records must satisfy both original clocks.
    if relationship_end is not None:
        trigger = min(trigger, relationship_end)
    excess_deadline = (trigger.astimezone(zone) + timedelta(days=30)).astimezone(timezone.utc)
    limit = excess_deadline if end is None else anniversary(end.astimezone(zone)).astimezone(timezone.utc)
    if security:
        limit = (trigger.astimezone(zone) + timedelta(days=90)).astimezone(timezone.utc)
    if case["exception_ref"] is not None or case["exception_until"] is not None:
        require(_reference(case["exception_ref"]) and _reference(case["exception_owner"])
                and case["exception_action"] == "dispose" and case["exception_until"] is not None,
                "disposition_exception_invalid")
        exception_end = _time(case["exception_until"])
        require(exception_end >= trigger, "disposition_exception_invalid")
        # The signed case states preservation was required through this date.
        require(now >= exception_end, "disposition_exception_active")
        limit = max(limit, exception_end)
    else:
        require(case["exception_owner"] is None and case["exception_action"] is None, "disposition_exception_invalid")
    if case["actor_due"]:
        if case["exception_until"]:
            excess_deadline = max(excess_deadline, _time(case["exception_until"]))
        limit = min(limit, excess_deadline)
    require(due <= limit, "disposition_deadline_extended")
    if case["operation"] in fields.MODELS and not (authorization_retirement or security):
        require(case["actor_due"] is True, "disposition_excess_scope_required")
    if case["operation"] == "erase_hold_actor":
        require(case["actor_due"] and case["hold_until"] is not None, "disposition_hold_scope_invalid")
        # A signed, dated lawful-basis reference is mandatory for continued denial beyond a year.
        hold_until = _time(case["hold_until"])
        require(hold_until > now and (hold_until <= anniversary(end.astimezone(zone)) or
                _reference(case["continuing_basis"])), "disposition_hold_review_required")
    else:
        require(case["hold_until"] is None and case["continuing_basis"] is None, "disposition_scope_invalid")
    if (authorization_retirement or security) and not case["actor_due"]:
        # The justified purpose may end months before the calendar ceiling.
        # A dated preservation obligation displaces that date only through its end.
        purpose_due = max(purpose, _time(case["exception_until"])) if case["exception_until"] else purpose
        due = min(due, purpose_due)
    return "overdue" if now > min(due, limit) else "within_deadline"


def target_check(db, case, role, target=None):
    if target is not None:
        from .school_disposition_connection import EXECUTOR, VERIFIER
        require(role in (EXECUTOR, VERIFIER) and case.get("destination") == target.binding
                and (case["database"],case["oid"]) == (target.target["database"],target.target["oid"]),
                "disposition_destination_mismatch")
        connection = db.connection()
        target.validate(connection.connection.driver_connection,role)
        require(connection.get_isolation_level()=="READ COMMITTED", "disposition_isolation")
        if role == EXECUTOR:
            from . import classroom_residual
            classroom_residual.validate(connection,SupportDenied)
        if case["operation"] == fields.TERMINAL:
            db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        return
    require("destination" not in case,"disposition_destination_mismatch")
    require(role == "disposition_custodian", "disposition_custodian_required")
    db.execute(text("SET LOCAL search_path TO pg_catalog, public, pg_temp"))
    if case["operation"] == fields.TERMINAL:
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    row = db.execute(text("""SELECT current_database(), oid, session_user, current_user,
        inet_server_addr() IS NULL, current_setting('listen_addresses'),
        EXISTS (SELECT FROM pg_roles WHERE (rolsuper OR rolcreaterole OR rolcreatedb OR
        rolbypassrls OR rolreplication) AND pg_has_role(oid,'MEMBER'))
        FROM pg_database WHERE datname=current_database()""")).one()
    require(tuple(row) == (case["database"], case["oid"], role, role, True, "", False),
            "disposition_target_mismatch")
    shared = db.scalar(text("""SELECT EXISTS (SELECT FROM pg_roles WHERE rolname <> session_user
         AND rolname <> :administrator AND NOT rolsuper AND pg_has_role(oid, current_user::regrole, 'MEMBER'))"""),
        {"administrator": ADMINISTRATOR})
    member = db.scalar(text(f"""SELECT {UNEXPECTED_MEMBERSHIP} FROM pg_catalog.pg_roles r
        WHERE r.rolname=session_user"""))
    require(shared is False and member is False, "disposition_custodian_membership_unqualified")
    from . import classroom_residual
    revisions = db.scalars(text("SELECT version_num FROM public.alembic_version")).all()
    require(revisions == [classroom_residual.REVISION] or
            (revisions == [REVISION] and case["operation"] not in fields.RESIDUAL_OPERATIONS
             and (case["operation"] != fields.PROGRESS or case["actor_due"])),
            "disposition_revision_mismatch")
    if revisions == [classroom_residual.REVISION]:
        classroom_residual.validate(db.connection() if isinstance(db, Session) else db, SupportDenied)
    require(db.connection().get_isolation_level() == "READ COMMITTED", "disposition_isolation")


def check_operator_privileges(db, case, config):
    # These are independently inventoried ordinary runtime and Support principals.
    # An application-only signed handler cannot remove a raw SQL privilege.
    roles = config["ordinary_roles"]
    require(isinstance(roles, list) and len(set(roles)) >= 2 and config["role"] not in roles,
            "disposition_principals_unqualified")
    erasing = case["operation"] == "erase_hold_actor"
    if case["operation"] in fields.MODELS:
        fields.check_principals(db, roles)
    from . import classroom_hold_guard
    if erasing or case["operation"] == fields.TERMINAL:
        classroom_hold_guard.validate(db.connection() if isinstance(db, Session) else db, SupportDenied)
    from .classroom_writer_sessions import HOLD, AUTHORITY, qualified, separated_runtime
    installed = set(db.scalars(text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN (:h,:a)"),
                               {"h":HOLD,"a":AUTHORITY}))
    require(installed == {HOLD, AUTHORITY} and installed.issubset(roles), "disposition_writer_inventory_incomplete")
    for role in roles:
        if role in installed:
            require(qualified(db, role), "disposition_restricted_writer_unqualified")
        else:
            require(separated_runtime(db,role), "disposition_runtime_privileges_unqualified")
        receipt_delete = db.scalar(text("""SELECT has_table_privilege(oid,
            'public.classroom_school_authorizations', 'DELETE') FROM pg_catalog.pg_roles WHERE rolname=:role"""),
            {"role": role})
        require(receipt_delete is False, "disposition_receipt_privilege_unqualified")


def inspect_record(db, case, clock, *, lock):
    if case["operation"] in fields.MODELS:
        return fields.inspect(db,case,clock)
    program, ident = case["program"], case["record"]
    if case["operation"] != "erase_hold_actor":
        query = select(Receipt).where(Receipt.program_id == program, Receipt.id == ident)
    else:
        query = select(ClassroomMembershipHold).join(ClassroomStudentMembership).join(ClassroomClass).where(
            ClassroomClass.program_id == program, ClassroomClass.id == case["class"],
            ClassroomMembershipHold.membership_id == ident)
    if lock:
        query = query.with_for_update(of=query.column_descriptions[0]["entity"])
    row = db.scalar(query.execution_options(populate_existing=True))
    require(row is not None, "disposition_original_missing")
    now = clock()
    if isinstance(row, Receipt):
        require(row.evidence_digest == school._evidence_digest(row)
                and (row.agreement_version, row.agreement_digest) == (school.AGREEMENT_VERSION, school.AGREEMENT_DIGEST),
                "disposition_integrity")
        ended = min(utc(row.expires_at), utc(row.revoked_at)) if row.revoked_at else utc(row.expires_at)
        if case["operation"] == "replace_receipt":
            require(school.status(row, now) == "authorized", "disposition_replacement_requires_active_authority")
        else:
            require(ended <= now and _time(case["authorization_end"]) == ended, "disposition_receipt_active_or_changed")
        require(not db.scalar(text("""SELECT EXISTS (SELECT FROM pg_constraint WHERE contype='f'
            AND confrelid='public.classroom_school_authorizations'::regclass)""")), "disposition_dependencies_changed")
        # _latest is an application dependency without a foreign key. Deletion must never
        # expose an older unrevoked verification, including pending or future authority.
        siblings = db.scalars(select(Receipt).where(Receipt.program_id == program, Receipt.year_start == row.year_start)
                             .order_by(Receipt.id.desc())).all()
        if case["operation"] == "replace_receipt":
            require(siblings[0].id == ident, "disposition_source_superseded")
        if siblings[0].id == ident:
            require(all(r.revoked_at is not None for r in siblings[1:]), "disposition_would_reveal_authority")
        snapshot = [school.revision(row), row.revoked_by_id, row.revocation_reason, row.agreement_digest,
                    school.revision(siblings[0]), ended.isoformat()]
        next_head = next((r for r in siblings if r.id != row.id), None)
        retained = [row.year_start, school.revision(next_head)]
    else:
        require(row.released_at is None and row.held_by_verifier_id is not None, "disposition_hold_changed")
        member = db.get(ClassroomStudentMembership, row.membership_id, populate_existing=True)
        retained = [row.state, row.reason, utc(row.held_at).isoformat(), member.profile_id]
        snapshot = [row.membership_id, member.profile_id, utc(row.held_at).isoformat(), row.state, row.reason,
                    row.held_by_verifier_id, row.released_at, row.released_by_verifier_id]
    return row, _digest([program, ident, case["class"], snapshot]), retained


def administrator_pin(config):
    with _protected_file(Path(config["signers"]), config["owner_uid"]) as file:
        raw = file.read(4097)
    require(len(raw) <= 4096 and sha256(raw).hexdigest() == config["signers_sha256"],
            "disposition_signer_pins_changed")
    lines = raw.splitlines()
    require(len(lines) == 1, "disposition_administrator_pin_invalid")
    fields = lines[0].split()
    require(len(fields) == 3 and fields[:2] == [b"administrator", b"ssh-ed25519"],
            "disposition_administrator_pin_invalid")
    try:
        blob = base64.b64decode(fields[2], validate=True)
    except binascii.Error:
        raise SupportDenied("disposition_administrator_pin_invalid") from None
    # SSH length-prefixed algorithm name and 32-byte Ed25519 public key.
    require(len(blob) == 51 and blob.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"),
            "disposition_administrator_pin_invalid")
    return raw


def verify_approval(config, manifest, directory, contract):
    require(contract in (CASE_APPROVAL, DESTINATION_APPROVAL)
            and manifest.get("approval_contract") == contract
            and _reference(manifest.get("case"))
            and (manifest["case"] == "DESTINATION") == (contract == DESTINATION_APPROVAL),
            "disposition_approval_contract_invalid")
    with NamedTemporaryFile(dir=config["register"]) as stable:
        stable.write(administrator_pin(config))
        stable.flush()
        signature = directory / (manifest["case"] + ".administrator.sig")
        result = subprocess.run(["/usr/bin/ssh-keygen", "-Y", "verify", "-f", stable.name,
            "-I", "administrator", "-n", contract, "-s", str(signature)],
            input=_encode(manifest), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"}, check=False)
        require(result.returncode == 0, "disposition_administrator_signature_required")


@contextmanager
def approved_case(config, program, reference):
    # The approval publisher uses LOCK_EX on this same stable Program lock to
    # publish or withdraw ANY case or preservation instruction for this Program.
    require(type(program) is int and type(config.get("pilot_program")) is int
            and program == config["pilot_program"] and program > 0
            and _reference(reference) and reference != "DESTINATION", "disposition_scope_invalid")
    owner = config["owner_uid"]
    directory = Path(config["approvals"])
    with _protected_file(directory / f"{program}.lock", owner) as file:
        fcntl.flock(file, fcntl.LOCK_SH)
        with _protected_file(directory / f"{program}.json", owner) as stream:
            case = _read_json(stream)
        require(isinstance(case, dict) and case.get("case") == reference and case.get("program") == program,
                "disposition_scope_mismatch")
        require(case.get("approval_contract") == CASE_APPROVAL, "disposition_approval_contract_invalid")
        yield case, directory


def successor_binding(register, case, record):
    return register / f"origin-{case['oid']}-{case['program']}-{record}.json"


def check_origin(register, case):
    path = successor_binding(register, case, case["record"])
    if case["operation"] in fields.MODELS or case["operation"] == "erase_hold_actor" or not path.exists():
        return
    with _protected_file(path, os.geteuid()) as stream:
        origin = _read_json(stream)
    if case["actor_due"]:
        # Renaming a case cannot discharge a still-open obligation. A genuinely
        # new instruction may shorten it. The separate case custodian must
        # reconcile the binding before a later, independent obligation can govern.
        if origin["obligation"] == case["obligation"]:
            require(_time(case["trigger"]) == _time(origin["trigger"]), "disposition_original_trigger_changed")
        else:
            require(_time(case["trigger"]) <= _time(origin["trigger"]), "disposition_original_trigger_changed")
        if case["exception_until"] is None:
            require(_time(case["latest"]) <= _time(origin["latest"]), "disposition_original_deadline_changed")


def readback(db, case, original, successor, now):
    if case["operation"] != "erase_hold_actor":
        require(db.get(Receipt, case["record"]) is None, "disposition_readback_failed")
        if not successor:
            require(school.revision(school._latest(db, case["program"], original[0])) == original[1],
                    "disposition_authority_readback_changed")
        else:
            row = db.get(Receipt, successor[0])
            require(row is not None and row.program_id == case["program"] and school.revision(row) == successor
                    and school.status(row, now) == "authorized"
                    and school._latest(db, row.program_id, row.year_start).id == row.id,
                    "disposition_successor_changed")
    else:
        row = db.get(ClassroomMembershipHold, case["record"])
        require(row is not None and row.held_by_verifier_id is None and row.released_at is None
                and [row.state, row.reason, utc(row.held_at).isoformat(),
                     db.get(ClassroomStudentMembership, row.membership_id).profile_id] == original,
                "disposition_readback_failed")


def check_verifier_access(target, case, plan):
    """Exercise the exact readback tables before allowing a serving mutation."""
    from .school_disposition_connection import VERIFIER
    structural = None
    if case["operation"] in fields.MODELS:
        row, children, cleared, preserved, _, structural = plan
        models = {type(item) for item in [row, *children, *preserved]}
        models.update(type(item) for item, _ in cleared)
    elif case["operation"] == "erase_hold_actor":
        models = {ClassroomMembershipHold, ClassroomStudentMembership}
    else:
        models = {Receipt}

    engine = target.engine(VERIFIER)
    try:
        with Session(engine) as verifier, verifier.begin():
            for model in models:
                verifier.execute(select(model).limit(0)).close()
            if structural is not None:
                from .classroom_residual import historical_teacher_count, next_code_generation
                kind, identifier, _ = structural
                if kind == "teachers":
                    historical_teacher_count(verifier, identifier)
                else:
                    next_code_generation(verifier, case["program"], identifier)
    finally:
        engine.dispose()


def run(engine, config, program, reference, *, execute=False, interaction=None, target=None, clock=lambda: datetime.now(timezone.utc)):
    require(isinstance(engine.pool, NullPool), "disposition_fresh_connections_required")
    require(("external" in config) == (target is not None), "disposition_transport_mismatch")
    register = Path(config["register"])
    _check_register(register)
    with approved_case(config, program, reference) as (previewed, _):
        scope = _digest(previewed)
    intent = register / (reference + ".intent.json")
    result = register / (reference + ".result.json")
    with ExitStack() as locks:
        exclusive = locks.enter_context(open(register / (reference + ".lock"), "a+b"))
        fcntl.flock(exclusive, fcntl.LOCK_EX)
        with Session(engine,expire_on_commit=False) as db, db.begin():
            if not execute:
                db.execute(text("SET TRANSACTION READ ONLY"))
            target_check(db, previewed, config["role"], target)
            if target is not None:
                db.info["disposition_target"] = target
            check_operator_privileges(db, previewed, config)
            if execute:
                classroom._lock_program(db, program)
            # Recheck after the database wait. Allow the reviewer to register a new
            # preservation instruction while the school participants authenticate.
            with approved_case(config, program, reference) as (case, directory):
                require(scope == _digest(case), "disposition_approval_changed")
                if execute:
                    verify_approval(config, case, directory, CASE_APPROVAL)
                else:
                    administrator_pin(config)
            if result.exists():
                with _protected_file(result, os.geteuid()) as stream:
                    saved = _read_json(stream)
                require(saved["scope"] == scope, "disposition_case_reused")
                return {"case": reference, "status": "previously_verified", "time": saved["time"]}
            if target is not None and execute:
                require(not intent.exists(), "disposition_interrupted_case_requires_reconciliation")
            eligibility(case, clock())
            check_origin(register, case)
            row, fingerprint, original = inspect_record(db, case, clock, lock=execute)
            if not execute:
                return {"case": reference, "snapshot": fingerprint, "status": "preview"}
            require(case["snapshot"] == fingerprint, "disposition_preview_changed")
            if target is not None:
                check_verifier_access(target, case, row)
            if intent.exists():
                with _protected_file(intent, os.geteuid()) as stream:
                    require(_read_json(stream) == {"scope": scope}, "disposition_case_reused")
            else:
                _write_new(intent, {"scope": scope})
            successor = None
            if case["operation"] == "replace_receipt":
                from .school_disposition_replacement import replace, TerminalParticipants
                successor = school.revision(replace(db, row, case["replacement_digest"],
                                                    interaction or TerminalParticipants()))
            check_operator_privileges(db, case, config)
            current, directory = locks.enter_context(approved_case(config, program, reference))
            require(_digest(current) == scope, "disposition_approval_changed")
            verify_approval(config, current, directory, CASE_APPROVAL)
            if successor:
                replacement = db.get(Receipt, successor[0])
                require(school.status(replacement, clock()) == "authorized", "disposition_successor_changed")
                # Persist the nonauthorizing clock binding before commit. A rolled-back
                # PostgreSQL sequence value is consumed. An orphan binding is never proof of success.
                _write_new(successor_binding(register, case, successor[0]), {
                    "obligation": case["obligation"], "trigger": case["trigger"], "latest": case["latest"]})
            if target is not None:
                target_check(db, case, config["role"], target)
            timing = eligibility(case, clock())
            if case["operation"] in fields.MODELS:
                row, refreshed, _ = fields.inspect(db, case, clock)
                require(refreshed == case["snapshot"], "disposition_preview_changed")
                fields.apply(db,row)
            elif isinstance(row, Receipt):
                db.delete(row)
            else:
                # Ordinary ORM writers cannot clear this field. Only the exact,
                # approved generation of a continuing hold can be minimized.
                from .classroom_hold_writer import witness
                with witness(db, "erase", program, case["class"], row, erase=True):
                    changed = db.execute(update(ClassroomMembershipHold).where(
                        ClassroomMembershipHold.membership_id == row.membership_id,
                        ClassroomMembershipHold.held_at == row.held_at,
                        ClassroomMembershipHold.held_by_verifier_id == row.held_by_verifier_id,
                        ClassroomMembershipHold.released_at.is_(None)).values(held_by_verifier_id=None))
                require(changed.rowcount == 1, "disposition_hold_changed")
            db.flush()
        verifier = engine
        verification_role = config["role"]
        if target is not None:
            from .school_disposition_connection import VERIFIER
            verification_role = VERIFIER
            verifier = target.engine(VERIFIER)
            locks.callback(verifier.dispose)
        with Session(verifier) as verify, verify.begin():
            verify.execute(text("SET TRANSACTION READ ONLY"))
            target_check(verify, case, verification_role, target)
            if case["operation"] in fields.MODELS:
                fields.verify(verify,case,row)
            else:
                readback(verify, case, original, successor, clock())
        completed = {"scope": scope, "time": clock().isoformat(), "timing": timing}
        if successor:
            completed.update(successor=successor[0], original_trigger=case["trigger"], original_due=case["latest"])
        _write_new(result, completed)
        return {"case": reference, "status": "verified", "timing": timing}


def run_external(config, program, reference, *, execute=False):
    from .school_disposition_connection import approved_target, EXECUTOR
    _check_register(Path(config["register"]))
    with approved_target(config) as target:
        engine = target.engine(EXECUTOR)
        try:
            return run(engine, config, program, reference, execute=execute, target=target)
        finally:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "execute"))
    parser.add_argument("program", type=int)
    parser.add_argument("case")
    args = parser.parse_args()
    try:
        with _protected_file(CONFIG, 0) as stream:
            config = _read_json(stream)
        require(config["role"] == "disposition_custodian", "disposition_custodian_required")
        require(os.getuid() == os.geteuid() == config["service_uid"] != 0
                and config["owner_uid"] != config["service_uid"], "disposition_service_identity_required")
        require(not any(k.startswith("PG") for k in os.environ), "disposition_environment_unqualified")
        if "external" in config:
            print(json.dumps(run_external(config, args.program, args.case, execute=args.action == "execute")))
            return 0
        from sqlalchemy.engine import URL
        socket = Path(config["socket"])
        require(socket.parent == Path("/tmp") and socket.name.startswith("ww-play-")
                and socket.stat().st_mode & 0o777 == 0o700, "disposition_private_fixture_required")
        url = URL.create("postgresql+psycopg", username=config["role"], database=config["database"],
                         query={"host": str(socket)})
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
        try:
            print(json.dumps(run(engine, config, args.program, args.case, execute=args.action == "execute")))
        finally:
            engine.dispose()
    except SupportDenied as exc:
        print(str(exc))
        return 1
    except Exception:
        print("disposition_unavailable_or_unreconciled")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
