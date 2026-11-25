"""Exact school-only field treatments authorized in D11 §§3.1–3.3.

The calling custodian holds the Program lock and the independently signed case
through commit. Each original has an exact fingerprint. PostgreSQL must reject
additional dependents, never dispose of them implicitly.
"""
from contextlib import nullcontext
from datetime import datetime

from sqlalchemy import and_, delete, inspect as sa_inspect, select, text, update

from .age_privacy import utc
from .classroom_identity_models import ClassroomRosterIdentity as Identity, ClassroomRosterIdentityAudit as IdentityAudit
from .classroom_enrollment_models import ClassroomEnrollmentInvitation as Enrollment, ClassroomEnrollmentAuditEvent as EnrollmentAudit
from .classroom_models import (ClassroomClass, ClassroomChartDirectorShare as Share, ClassroomS2AuditEvent as Event,
                               ClassroomStudentInvitation as Invitation, ClassroomTeachingAssignment,
                               ClassroomProgressAuthorization as Progress, ClassroomProgressRecipient as Recipient,
                               ClassroomMembershipPeriod as Period, ClassroomAuditEvent as RoleEvent,
                               ClassroomRoleGrant as RoleGrant, ClassroomReportingPeriod as ReportingPeriod,
                               ClassroomS3AuditEvent as ReportingEvent, ClassroomStudentMembership as Membership,
                               ClassroomMembershipHold as Hold, ClassroomClassState as ClassState,
                               ClassroomEntryCode as EntryCode, ClassroomEntitlement as Entitlement)
from .support_case_files import SupportDenied, _digest, _time

ROSTER = "retire_roster_identity"
ENROLLMENT = "retire_closed_enrollment_offer"
INVITATION = "retire_revoked_sharing_offer"
CHART = "retire_withdrawn_chart_link"
ACTOR = "retire_superseded_removal_event"
PROGRESS = "retire_withdrawn_progress"
TEACHER_EVENT = "retire_superseded_teacher_issue_event"
ROLE = "retire_ended_role_grant"
REPORT_EVENT = "retire_ended_reporting_event"
ENTRY_EVENT = "retire_closed_class_entry_event"
LEGACY_REPORT = "retire_withdrawn_reporting_period"
MEMBERSHIP_PERIOD = "retire_ended_membership_period"
TERMINAL = "retire_terminal_membership"
S1_ACTOR = "erase_historical_s1_actor"
S2_ACTOR = "erase_historical_s2_actor"
ARCHIVE_ACTOR = "erase_archived_class_actor"
TEACHING = "retire_ended_teaching_assignment"
ENTRY_CODE = "retire_obsolete_entry_code"
ENTITLEMENT = "retire_ended_institutional_entitlement"
MEMBERSHIP_EVENTS = {"student_joined", "student_left", "student_removed", "student_suspended", "student_reinstated"}
MEMBERSHIP_EVENT_FIELDS = {"membership_id", "profile_id", "actor_verifier_id", "actor_profile_id", "actor_admin"}
RESIDUAL_OPERATIONS = {
    TERMINAL: Membership, LEGACY_REPORT: ReportingPeriod, MEMBERSHIP_PERIOD: Period, S1_ACTOR: RoleEvent, S2_ACTOR: Event,
    ARCHIVE_ACTOR: ClassState, TEACHING: ClassroomTeachingAssignment, ENTRY_CODE: EntryCode,
    ENTITLEMENT: Entitlement,
}
AUTHORIZATION_RETIREMENT = {PROGRESS, LEGACY_REPORT, MEMBERSHIP_PERIOD, TEACHING, ENTITLEMENT}
MODELS = {ENTRY_EVENT: Event, TEACHER_EVENT: RoleEvent, ROLE: RoleGrant, REPORT_EVENT: ReportingEvent, PROGRESS: Progress, ROSTER: Identity, ENROLLMENT: Enrollment, INVITATION: Invitation, CHART: Share, ACTOR: Event}
MODELS.update(RESIDUAL_OPERATIONS)


def require(ok):
    if not ok:
        raise SupportDenied("disposition_school_fields_changed_or_ineligible")


def values(row):
    return {col.name: getattr(row,col.name) for col in row.__table__.columns}


def digest(row):
    return _digest({k:v.isoformat() if isinstance(v,datetime) else v for k,v in values(row).items()})


def check_profile_removal(db, event, now, clearing):
    """Check disclosure and interruption lookups for every incarnation in this Class."""
    from .classroom_progress_authorization import _source_event_watermark

    scope = {"program": event.program_id, "room": event.class_id,
             "student": event.profile_id, "event": event.id, "at": now, "clearing": clearing}
    active = db.scalar(select(Period.id).join(Membership, Membership.id == Period.membership_id).where(
        Membership.class_id == event.class_id, Membership.profile_id == event.profile_id,
        Period.ended_at.is_(None)).limit(1))
    if active and _source_event_watermark(db, event.program_id, event.class_id, event.profile_id) == event.id:
        raise SupportDenied("disposition_profile_required_for_reporting_revision")
    if event.action not in {"student_left", "student_removed", "student_suspended"}:
        return
    # An invitation-based chart selection predating this departure must keep
    # a surviving interruption. Exclude all rows in this approved edit so
    # two removals in the same transaction cannot vouch for one another.
    required = db.scalar(text("""
        SELECT EXISTS (
            SELECT FROM public.classroom_chart_director_shares sh
            JOIN public.classroom_student_invitations i ON i.id = sh.invitation_id
            JOIN public.classroom_teaching_assignments teaching ON teaching.id=sh.assignment_id
            JOIN public.classroom_entitlements entitlement ON entitlement.id=sh.entitlement_id
            WHERE i.program_id=:program AND i.class_id=:room AND i.profile_id=:student
              AND sh.class_id=:room AND sh.withdrawn_at IS NULL AND i.revoked_at IS NULL
              AND teaching.ended_at IS NULL AND entitlement.status='active' AND entitlement.ends_at > :at
              AND i.accepted_at IS NOT NULL AND (i.deadline_accepted_at=i.accepted_at OR i.expires_at > :at)
              AND coalesce(sh.source_audit_id, 0) < :event AND NOT EXISTS (
                  SELECT FROM public.classroom_s2_audit_events barrier
                  WHERE barrier.program_id=:program AND barrier.id > coalesce(sh.source_audit_id, 0)
                    AND barrier.occurred_at <= :at AND (
                      (barrier.class_id=:room AND barrier.action='class_archived') OR
                      (barrier.class_id=:room AND barrier.profile_id=:student
                       AND barrier.id <> ALL(CAST(:clearing AS integer[]))
                       AND barrier.action IN ('student_left','student_removed','student_suspended')) OR
                       barrier.action IN ('institutional_created','institutional_changed','institutional_ended'))))
    """), scope)
    if required:
        raise SupportDenied("disposition_profile_required_for_chart_interruption")


def membership_event_columns(db, event, case, now):
    columns = tuple(sorted(case["event_fields"]))
    require(event.action in MEMBERSHIP_EVENTS and utc(event.occurred_at) <= _time(case["trigger"])
            and all(getattr(event, name) is not None for name in columns))
    room = db.get(ClassroomClass, event.class_id)
    require(room is not None and room.program_id == case["program"])
    if event.membership_id is not None:
        anchor = db.get(Membership, event.membership_id)
        require(anchor is not None and anchor.class_id == room.id
                and event.profile_id in (None, anchor.profile_id))
    if "profile_id" in columns:
        check_profile_removal(db, event, now, [event.id])
    return columns


def inspect(db, case, clock):
    op, program = case["operation"], case["program"]
    model = MODELS[op]
    primary = next(iter(model.__table__.primary_key.columns))
    stmt = select(model).where(primary == case["record"])
    if op == CHART:
        stmt = stmt.join(ClassroomClass, ClassroomClass.id == Share.class_id).join(
            ClassroomTeachingAssignment, and_(ClassroomTeachingAssignment.id == Share.assignment_id,
                ClassroomTeachingAssignment.program_id == program, ClassroomTeachingAssignment.class_id == Share.class_id)
        ).where(ClassroomClass.program_id == program, Share.withdrawn_at.is_not(None))
    elif op == TERMINAL:
        stmt = stmt.join(ClassroomClass, ClassroomClass.id == Membership.class_id).where(ClassroomClass.program_id == program)
    elif op == MEMBERSHIP_PERIOD:
        stmt = stmt.join(Membership, Membership.id == Period.membership_id).join(
            ClassroomClass, ClassroomClass.id == Membership.class_id).where(ClassroomClass.program_id == program)
    else:
        stmt = stmt.where(model.program_id == program)
    row = db.scalar(stmt.execution_options(populate_existing=True))
    require(row is not None)
    children = []
    clear_fields = []
    preserve = []
    structural = None
    if op == ROSTER:
        children = list(db.scalars(select(IdentityAudit).where(IdentityAudit.identity_id==row.id).order_by(IdentityAudit.version)))
        require([r.version for r in children] == list(range(1,row.version+1))
                and all(r.program_id==program and r.profile_id==row.profile_id for r in children))
    elif op == ENROLLMENT:
        require(row.state in {"declined","expired","revoked"} and row.resolved_at is not None and row.accepted_period_id is None)
        children = list(db.scalars(select(EnrollmentAudit).where(EnrollmentAudit.invitation_id==row.id).order_by(EnrollmentAudit.id)))
        require(all(r.program_id==program and r.profile_id==row.profile_id and r.class_id==row.class_id for r in children))
    elif op == PROGRESS:
        from .classroom_residual import enabled
        period = db.get(Period, row.membership_period_id)
        anchor = db.get(Membership, row.membership_id)
        require(row.withdrawn_at is not None and utc(row.withdrawn_at) <= clock()
                and row.accepted_by_profile_id == row.profile_id and period is not None
                and anchor is not None and period.membership_id == anchor.id
                and (anchor.class_id, anchor.profile_id) == (row.class_id, row.profile_id)
                and db.get(ClassroomClass, anchor.class_id).program_id == program)
        if not enabled(db):
            require(period.ended_at is not None and not db.scalar(select(Progress.id).where(
                Progress.id != row.id, Progress.program_id == program, Progress.class_id == row.class_id,
                Progress.profile_id == row.profile_id).limit(1)))
        children = list(db.scalars(select(Recipient).where(Recipient.authorization_id==row.id)
                                   .order_by(Recipient.assignment_id)))
        require(all(r.program_id==program and r.class_id==row.class_id for r in children))
    elif op == TEACHER_EVENT:
        require(row.action=="teacher_assigned" and row.assignment_id is not None)
        assignment=db.get(ClassroomTeachingAssignment,row.assignment_id)
        require(assignment is not None and assignment.program_id==program and assignment.ended_at is not None
                and assignment.ended_at<=clock() and assignment.class_id==row.class_id)
        # Only obsolete issuance: the later ending event remains the higher
        # assignment revision for both this Class and shared Program capacity.
        require(db.scalar(select(RoleEvent.id).where(RoleEvent.program_id==program,RoleEvent.class_id==row.class_id,
            RoleEvent.assignment_id==row.assignment_id,RoleEvent.id>row.id,RoleEvent.occurred_at>=row.occurred_at,
            RoleEvent.occurred_at<=clock(),RoleEvent.action.in_(("teacher_revoked","teacher_departed"))).limit(1)))
    elif op == ROLE:
        require(row.ended_at is not None and row.ended_at<=clock())
        children=list(db.scalars(select(RoleEvent).where(RoleEvent.role_grant_id==row.id).order_by(RoleEvent.id)))
        require(all(r.program_id==program and r.target_verifier_id==row.verifier_id and
                    r.action in {"role_granted","role_revoked"} for r in children))
    elif op == REPORT_EVENT:
        period=db.get(ReportingPeriod,row.period_id)
        require(period is not None and period.ended_at is not None and period.ended_at<=clock()
                and (period.program_id,period.class_id,period.profile_id)==(program,row.class_id,row.profile_id))
    elif op == LEGACY_REPORT:
        period = db.get(Period, row.membership_period_id)
        require(row.ended_at is not None and utc(row.ended_at) <= clock()
                and period is not None and period.membership_id == row.membership_id)
        anchor = db.get(Membership, row.membership_id)
        require(anchor is not None and (anchor.class_id, anchor.profile_id) == (row.class_id, row.profile_id)
                and db.get(ClassroomClass, anchor.class_id).program_id == program)
        children = list(db.scalars(select(ReportingEvent).where(ReportingEvent.period_id == row.id)
                                    .order_by(ReportingEvent.id)))
        require(all((r.program_id, r.class_id, r.profile_id) == (program, row.class_id, row.profile_id)
                    for r in children))
    elif op == TERMINAL:
        end = _time(case["trigger"])
        if case["relationship_end"] is not None:
            end = min(end, _time(case["relationship_end"]))
        require(not db.scalar(select(Period.id).where(Period.membership_id == row.id).limit(1)))
        hold = db.get(Hold, row.id)
        if hold is not None:
            require(hold.released_by_verifier_id is not None and hold.released_at is not None
                    and utc(hold.released_at) <= clock())
            children.append(hold)
        # Refuse outstanding authority, including a previously reviewed code.
        require(not db.scalar(select(Enrollment.id).where(Enrollment.program_id == program,
            Enrollment.class_id == row.class_id, Enrollment.profile_id == row.profile_id,
            Enrollment.state.in_(("pending", "deferred", "accepted"))).limit(1)))
        require(not db.scalar(select(Invitation.id).where(Invitation.program_id == program,
            Invitation.class_id == row.class_id, Invitation.profile_id == row.profile_id,
            Invitation.revoked_at.is_(None), Invitation.expires_at > clock()).limit(1)))
        require(not db.scalar(select(EntryCode.id).where(EntryCode.program_id == program,
            EntryCode.class_id == row.class_id, EntryCode.is_current.is_(True),
            EntryCode.issued_at <= end).limit(1)))
        require(not db.scalar(text("""SELECT EXISTS (SELECT FROM public.classroom_chart_director_shares sh
            JOIN public.practice_charts pc ON pc.id=sh.chart_id WHERE sh.class_id=:room
            AND pc.profile_id=:student AND sh.withdrawn_at IS NULL)"""),
            {"room":row.class_id, "student":row.profile_id}))
        # Restrictive FKs also guard the commit; detect dependent rows at preview.
        for dependent in (Progress, ReportingPeriod, Event):
            require(not db.scalar(select(dependent.id).where(dependent.membership_id == row.id).limit(1)))
    elif op == MEMBERSHIP_PERIOD:
        require(row.ended_at is not None and utc(row.ended_at) <= clock())
        anchor = db.get(Membership, row.membership_id)
        preserve = [anchor]
        hold = db.get(Hold, row.membership_id)
        if hold is not None:
            preserve.append(hold)
        # An accepted offer grants no future membership. Retire its exact
        # resolved transition before the old membership period disappears.
        offers = list(db.scalars(select(Enrollment).where(Enrollment.accepted_period_id == row.id)
                                 .order_by(Enrollment.id)))
        for offer in offers:
            require(offer.state == "accepted" and offer.resolved_at is not None
                    and (offer.program_id, offer.class_id, offer.profile_id)
                        == (program, anchor.class_id, anchor.profile_id))
            audits = list(db.scalars(select(EnrollmentAudit).where(EnrollmentAudit.invitation_id == offer.id)
                                     .order_by(EnrollmentAudit.id)))
            require(all((item.program_id, item.class_id, item.profile_id) ==
                        (program, anchor.class_id, anchor.profile_id)
                        and item.membership_period_id in (None, row.id) for item in audits))
            children.extend([*audits, offer])
        # Open and close events refer to the stable anchor. Bound the scope
        # to this validated incarnation before allowing an identity edit.
        following = db.scalar(select(Period.starts_at).where(Period.membership_id == anchor.id,
            Period.id != row.id, Period.starts_at >= row.ended_at).order_by(Period.starts_at).limit(1))
        events = db.scalars(select(Event).where(Event.membership_id == anchor.id,
            Event.occurred_at >= row.starts_at).order_by(Event.id)).all()
        candidates = [item for item in events if following is None or utc(item.occurred_at) < utc(following)]
        openings = [item for item in candidates if item.action in {"student_joined", "student_reinstated"}
                    and utc(item.occurred_at) < utc(row.ended_at)]
        departures = [item for item in candidates if item.action in {"student_left", "student_removed", "student_suspended"}
                      and utc(item.occurred_at) >= utc(row.ended_at)]
        require(len(openings) <= 1 and len(departures) <= 1 and len(candidates) == len(openings) + len(departures))
        # A departure at a tied boundary needs its own signed event review.
        # Once its anchor link has been independently cleared, a remaining
        # opening at that boundary belongs outside this period's edit scope.
        if following is not None and utc(following) == utc(row.ended_at):
            require(not any(item.action in {"student_left", "student_removed", "student_suspended"}
                            and utc(item.occurred_at) == utc(following) for item in events))
        if departures:
            departure = departures[0]
            require((row.ended_reason == "departed") == (departure.action == "student_left")
                    and db.scalar(text("""SELECT e.xmin=p.xmin AND age(p.xmin) BETWEEN 0 AND 1000000000
                        AND NOT EXISTS (SELECT FROM public.classroom_membership_periods other
                            WHERE other.membership_id=p.membership_id AND other.id<>p.id AND other.xmin=p.xmin)
                        FROM public.classroom_s2_audit_events e, public.classroom_membership_periods p
                        WHERE e.id=:event AND p.id=:period"""), {"event":departure.id, "period":row.id}))
        clearing = [item.id for item in candidates]
        for item in candidates:
            require((item.program_id, item.class_id) == (program, anchor.class_id)
                    and item.profile_id in (None, anchor.profile_id) and utc(item.occurred_at) <= clock())
            if item.profile_id is not None:
                check_profile_removal(db, item, clock(), clearing)
            clear_fields.append((item, ("actor_verifier_id", "actor_profile_id", "actor_admin", "membership_id", "profile_id")))
    elif op == S1_ACTOR:
        require(row.actor_verifier_id is not None and row.action in {
            "program_provisioned", "role_granted", "teacher_revoked", "teacher_departed", "class_created",
        } and utc(row.occurred_at) <= clock())
        columns = ("actor_verifier_id",)
        if "authorization_anchor" not in case and "security_anchor" not in case:
            if row.action == "program_provisioned":
                columns += ("target_verifier_id", "new_owner_id")
            elif row.action == "role_granted":
                columns += ("target_verifier_id", "role_grant_id")
        clear_fields.append((row, columns))
    elif op == S2_ACTOR:
        if "event_fields" in case:
            columns = membership_event_columns(db, row, case, clock())
        else:
            require(row.action in {"class_activated", "class_archived", "class_reactivated",
                                   "institutional_created", "institutional_changed", "institutional_ended"}
                    and utc(row.occurred_at) <= clock() and (row.actor_verifier_id is not None or row.actor_admin is not None))
            columns = ("actor_verifier_id", "actor_profile_id", "actor_admin")
        # Source consumers retain the structural event. Bind their exact
        # inventory into the independently signed preview and verify it again
        # after commit. A new reference requires a new scope approval.
        for consumer in (ReportingPeriod, Progress, Share):
            references = db.scalars(select(consumer).where(consumer.source_audit_id == row.id)
                                    .order_by(consumer.id)).all()
            for reference in references:
                source_program = (db.get(ClassroomClass, reference.class_id).program_id
                                  if consumer is Share else reference.program_id)
                require(source_program == program)
            preserve.extend(references)
        if preserve:
            require("source_scope_ref" in case)
        clear_fields.append((row, columns))
    elif op == ARCHIVE_ACTOR:
        require(row.state == "archived" and row.changed_by_verifier_id is not None
                and not row.enrollment_open and utc(row.updated_at) <= clock())
        clear_fields.append((row, ("changed_by_verifier_id",)))
    elif op == TEACHING:
        require(row.ended_at is not None and utc(row.ended_at) <= clock())
        events = db.scalars(select(RoleEvent).where(RoleEvent.assignment_id == row.id)
                            .order_by(RoleEvent.id)).all()
        require(all((event.program_id, event.class_id) == (program, row.class_id)
                    and event.action in {"teacher_assigned", "teacher_revoked", "teacher_departed"}
                    and event.target_verifier_id == row.verifier_id
                    for event in events))
        clear_fields.extend((event, ("actor_verifier_id", "target_verifier_id", "assignment_id")) for event in events)
        from .classroom_residual import historical_teacher_count
        structural = ("teachers", program, historical_teacher_count(db, program))
    elif op in (ENTRY_CODE, ENTITLEMENT):
        if op == ENTRY_CODE:
            require(not row.is_current and row.revoked_at is not None and utc(row.revoked_at) <= clock())
            source = Event.code_id
            allowed = {"code_issued", "code_rotated", "code_revoked", "student_joined"}
            from .classroom_residual import next_code_generation
            next_generation = next_code_generation(db, program, row.class_id)
            require(next_generation is not None and next_generation > row.generation)
            structural = ("code", row.class_id, next_generation)
        else:
            require(row.source == "institutional" and row.status == "ended"
                    and row.ended_at is not None and utc(row.ended_at) <= clock())
            source = Event.entitlement_id
            allowed = {"institutional_created", "institutional_changed", "institutional_ended",
                       "class_activated", "class_reactivated", "enrollment_opened", "code_issued",
                       "code_rotated", "student_joined", "student_reinstated"}
        events = db.scalars(select(Event).where(source == row.id).order_by(Event.id)).all()
        require(all(event.program_id == program and event.action in allowed
                    and (op != ENTRY_CODE or event.class_id == row.class_id) for event in events))
        for event in events:
            columns = (source.key,)
            if event.action.startswith(("code_", "institutional_")):
                columns += ("actor_verifier_id", "actor_profile_id", "actor_admin")
            clear_fields.append((event, columns))
    elif op == INVITATION:
        require(row.revoked_at is not None)
    elif op == ENTRY_EVENT:
        require(row.action in {"enrollment_closed","enrollment_opened","code_issued","code_rotated","code_revoked"}
                and row.actor_verifier_id is not None)
        require(db.scalar(text("""SELECT EXISTS (SELECT FROM public.classroom_class_states
            WHERE program_id=:p AND class_id=:c AND state='archived' AND NOT enrollment_open)
            AND EXISTS (SELECT FROM public.classroom_s2_audit_events WHERE program_id=:p AND id>:i)
            AND NOT EXISTS (SELECT FROM public.classroom_entry_codes WHERE id=:code AND is_current)"""),
            {"p":program,"c":row.class_id,"i":row.id,"code":row.code_id}))
    elif op == ACTOR:
        require(row.action in {"student_removed","student_suspended"} and row.actor_verifier_id is not None)
        # Archive is a later interruption for every sharing route and a later
        # source revision for every student in the Class. Keep unsuperseded facts.
        require(db.scalar(text("""SELECT EXISTS (SELECT FROM public.classroom_class_states cs
            JOIN public.classroom_s2_audit_events archive ON archive.class_id=cs.class_id AND archive.program_id=cs.program_id
            WHERE cs.program_id=:p AND cs.class_id=:c AND cs.state='archived' AND archive.action='class_archived'
              AND archive.id>:id AND archive.occurred_at>=:at AND archive.occurred_at<=:now)
            AND NOT EXISTS (SELECT FROM public.classroom_chart_director_shares sh
                JOIN public.practice_charts pc ON pc.id=sh.chart_id
                WHERE sh.class_id=:c AND pc.profile_id=:student AND sh.withdrawn_at IS NULL)"""),
            {"p":program,"c":row.class_id,"id":row.id,"at":row.occurred_at,"now":clock(),"student":row.profile_id}))
    if model is Event and op != S2_ACTOR:
        require(not db.scalar(text("""SELECT EXISTS (SELECT FROM public.classroom_chart_director_shares WHERE source_audit_id=:i
            UNION ALL SELECT FROM public.classroom_progress_authorizations WHERE source_audit_id=:i
            UNION ALL SELECT FROM public.classroom_reporting_periods WHERE source_audit_id=:i)"""),{"i":row.id}))
    if op in AUTHORIZATION_RETIREMENT and not case["actor_due"]:
        ended = utc(row.withdrawn_at if op == PROGRESS else row.ended_at)
        if op == ENTITLEMENT:
            ended = min(ended, utc(row.ends_at))
        require(_time(case["authorization_end"]) == ended)
    if "authorization_anchor" in case:
        reference = case["authorization_anchor"]
        from .classroom_school_models import ClassroomSchoolAuthorization
        from . import classroom_school_authorization as school
        receipt = db.get(ClassroomSchoolAuthorization, reference["receipt"])
        require(receipt is not None and receipt.approved_at is not None and receipt.program_id == program
                and receipt.evidence_digest == reference["digest"] and school._evidence_digest(receipt) == receipt.evidence_digest
                and utc(row.created_at if op == TERMINAL else row.occurred_at) <= _time(case["authorization_end"])
                and min(utc(receipt.revoked_at or receipt.expires_at), utc(receipt.expires_at)) == _time(case["authorization_end"]))
    if "security_anchor" in case:
        require(_time(case["security_anchor"]["event"]) == utc(row.occurred_at))
    # Pure attribution clearing preserves dependent identity keys and scoped
    # revision values. Deletions must encounter only restrictive foreign keys.
    update_only = op in {S1_ACTOR, S2_ACTOR, ARCHIVE_ACTOR}
    tables = {r.__table__.name for r in ([] if update_only else [row,*children])}
    # NO ACTION/RESTRICT FKs are safe: a missed dependent rolls the entire case back.
    require(not db.scalar(text("""SELECT EXISTS (SELECT FROM pg_catalog.pg_constraint f
      JOIN pg_catalog.pg_class c ON c.oid=f.confrelid
      WHERE f.contype='f' AND c.relnamespace='public'::regnamespace AND c.relname=ANY(:tables)
        AND f.confdeltype NOT IN ('a','r'))"""),{"tables":list(tables)}))
    snapshot = [op, program, digest(row), [digest(r) for r in children]]
    if op in RESIDUAL_OPERATIONS:
        snapshot.extend(([[digest(record), names] for record, names in clear_fields],
                         [digest(record) for record in preserve], structural))
    return (row, children, clear_fields, preserve, update_only, structural), _digest(snapshot), None


def apply(db, originals):
    from .classroom_residual import WITNESSED_DELETE_TABLES, enabled, evidence_witness

    row, children, cleared, _, update_only, _ = originals
    for original, names in cleared:
        table = original.__table__
        criteria = [col == getattr(original, col.name) for col in table.primary_key]
        changes = dict.fromkeys(names)
        with evidence_witness(db, table, criteria, changes):
            changed = db.execute(update(table).where(*criteria).values(changes)
                                 .returning(*table.c)).mappings().one_or_none()
        require(changed is not None and dict(changed) == (values(original) | dict.fromkeys(names)))
    for original in ([] if update_only else [*children, row]):
        table=original.__table__
        criteria = [col == getattr(original, col.name) for col in table.primary_key]
        witness = evidence_witness(db, table, criteria) if (table.name in WITNESSED_DELETE_TABLES or table.name == Hold.__tablename__) and enabled(db) else nullcontext()
        with witness:
            removed = db.execute(delete(table).where(*criteria).returning(*table.c)).mappings().one_or_none()
        require(removed is not None and dict(removed)==values(original))


def verify(db, case, originals):
    row, children, cleared, preserve, update_only, structural = originals
    for original in ([] if update_only else [row, *children]):
        require(db.get(type(original), sa_inspect(original).identity) is None)
    for original, names in [*cleared, *((record, ()) for record in preserve)]:
        current = db.get(type(original), sa_inspect(original).identity)
        require(current is not None and values(current) == (values(original) | dict.fromkeys(names)))
    if structural is not None:
        from .classroom_residual import historical_teacher_count, next_code_generation
        kind, identifier, expected = structural
        observed = (historical_teacher_count(db, identifier) if kind == "teachers" else
                    next_code_generation(db, case["program"], identifier))
        require(observed == expected)


def check_principals(db, roles):
    for role in roles:
        allowed = db.scalar(text("""SELECT NOT (
          EXISTS (SELECT FROM pg_auth_members m WHERE m.member=r.oid AND
                  pg_has_role(r.oid,'disposition_custodian','MEMBER')) OR
          has_column_privilege(r.oid,'public.classroom_s2_audit_events','actor_verifier_id','UPDATE')
          OR EXISTS (SELECT FROM unnest(CAST(:tables AS text[])) AS t(name)
                     WHERE has_table_privilege(r.oid,t.name,'DELETE,TRUNCATE,TRIGGER')))
          FROM pg_roles r WHERE r.rolname=:role"""),{"role":role,"tables":[
              "public." + m.__table__.name for m in set(MODELS.values()) | {IdentityAudit, EnrollmentAudit, Recipient}]})
        require(allowed is True)
