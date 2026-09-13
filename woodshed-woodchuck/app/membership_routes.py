"""Membership owner, invitation, and site-admin surfaces."""
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.routing import APIRoute
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from .db import SessionLocal
from .account_routes import current_profile
from .verifier_routes import current_verifier
from .models import (WoodchuckProfile, TrustedVerifier, BillingAccount, Membership, MembershipSeat,
                     MembershipSeatInvitation, ProviderSubscription, MembershipAuditEvent)
from . import memberships as service
from .billing_config import BillingConfig, PLANS, available_plans
from .billing_providers import BillingUnavailable, checkout, process_webhook
from .site_admin import csrf_token, check_csrf, sign_in_site_admin, require_site_admin
from .email_service import EmailService, public_link


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def private(request):
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
                raise
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return private


router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def identities(request, session):
    result = []
    student = current_profile(request, session)
    adult = current_verifier(request, session)
    if student:
        result.append((service.Actor("student", student.id), student.display_name))
    if adult:
        result.append((service.Actor("adult", adult.id), adult.display_name))
    return result


def actor_for(request, session, kind=None):
    choices = identities(request, session)
    if not choices:
        raise HTTPException(401, "Student or Verifier sign-in is required.")
    if kind:
        for actor, _ in choices:
            if actor.kind == kind:
                return actor
        raise HTTPException(403, "This account is not signed in.")
    if len(choices) != 1:
        raise HTTPException(400, "Choose which signed-in account to use.")
    return choices[0][0]


def render(request, template, **context):
    return templates.TemplateResponse(request=request, name=template, context={
        "title": "Membership", "csrf": csrf_token(request),
        "message": request.session.pop("membership_message", None), **context})


def detail(session, membership):
    seats = session.execute(select(MembershipSeat, WoodchuckProfile.display_name).join(
        WoodchuckProfile, WoodchuckProfile.id == MembershipSeat.profile_id).where(
        MembershipSeat.membership_id == membership.id, MembershipSeat.removed_at.is_(None)
    ).order_by(MembershipSeat.slot_number)).all()
    invitations = session.scalars(select(MembershipSeatInvitation).where(
        MembershipSeatInvitation.membership_id == membership.id,
        MembershipSeatInvitation.status == "pending", MembershipSeatInvitation.expires_at > service.clock()
    ).order_by(MembershipSeatInvitation.created_at.desc())).all()
    owner_profile_id = session.scalar(select(BillingAccount.profile_id).where(BillingAccount.id == membership.billing_account_id))
    return {"membership": membership, "seats": seats, "owner_profile_id": owner_profile_id, "invitations": invitations,
            "active": service.membership_is_active(membership),
            "plan_label": PLANS[membership.plan_code].label if membership.plan_code in PLANS else "Complimentary Full membership"}


@router.get("/membership")
def membership_page(request: Request, as_account: str | None = None):
    with SessionLocal() as session:
        choices = identities(request, session)
        if not choices:
            return RedirectResponse("/login", status_code=303)
        if len(choices) > 1 and not as_account:
            return render(request, "membership.html", choices=choices, choose_account=True)
        actor = actor_for(request, session, as_account)
        account = service.billing_account(session, actor)
        memberships = list(session.scalars(select(Membership).where(Membership.billing_account_id == account.id)
                                           .order_by(Membership.created_at.desc()))) if account else []
        full = actor.kind == "student" and service.student_has_full_access(session, actor.id)
        return render(request, "membership.html", actor=actor, choices=choices,
                      full=full, memberships=[detail(session, row) for row in memberships],
                      connected=service.connected_students(session, actor),
                      plans=available_plans(BillingConfig.from_environment()))


def integer(form, key):
    try:
        value = int(form.get(key, ""))
        if value <= 0:
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise ValueError("Choose a valid account or student spot.")


@router.post("/membership/actions")
async def membership_action(request: Request):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    with SessionLocal() as session:
        actor = actor_for(request, session, form.get("as_account"))
        try:
            membership_id = integer(form, "membership_id")
            action = form.get("action")
            delivery = None
            if action == "add":
                service.add_seat(session, membership_id, integer(form, "profile_id"), actor)
            elif action == "remove":
                service.remove_seat(session, membership_id, integer(form, "seat_id"), actor)
            elif action == "invite":
                invitation, token = service.invite_student(session, membership_id, str(form.get("email", "")), actor)
                delivery = (invitation.id, invitation.email, token)
            elif action == "cancel_invitation":
                service.cancel_invitation(session, membership_id, integer(form, "invitation_id"), actor)
            else:
                raise ValueError("Unknown membership action.")
            session.commit()
            request.session["membership_message"] = "Membership updated."
            if delivery:
                invitation_id, email, token = delivery
                result = EmailService().send_membership_invitation(recipient=email, acceptance_url=public_link(
                    f"/membership/invitations/{token}", local_base_url=str(request.base_url)))
                service.audit(session, session.get(Membership, membership_id), actor,
                              "invitation_sent" if result.sent else "invitation_delivery_failed",
                              invitation_id=invitation_id, delivery_code=result.code)
                session.commit()
                request.session["membership_message"] = ("Invitation sent." if result.sent else
                    "Invitation saved, but email could not be sent. Cancel it and try again when email is available.")
        except LookupError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, IntegrityError) as error:
            session.rollback()
            raise HTTPException(409, str(error) if isinstance(error, ValueError) else "The student spot changed. Reload and try again.") from error
    return RedirectResponse(f"/membership?as_account={actor.kind}", 303)


@router.get("/membership/invitations/{token}")
def invitation_page(request: Request, token: str):
    # Do not disclose payer identity or invite email to the recipient.
    with SessionLocal() as session:
        student = current_profile(request, session)
        return render(request, "membership_invitation.html", student_name=student.display_name if student else None)


@router.post("/membership/invitations/{token}")
async def invitation_claim(request: Request, token: str):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    with SessionLocal() as session:
        actor = actor_for(request, session, "student")
        try:
            service.claim_invitation(session, token, actor)
            session.commit()
        except (ValueError, LookupError, IntegrityError) as error:
            session.rollback()
            raise HTTPException(409, "This invitation cannot be claimed. It may have expired, been used, or have no available spot.") from error
    return RedirectResponse("/membership?as_account=student", 303)


@router.post("/membership/checkout")
async def membership_checkout(request: Request):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    # Reject unexpected fields, including client-supplied prices.
    if set(form) - {"csrf", "as_account", "plan_code", "provider"}:
        raise HTTPException(400, "Only a plan and provider may be selected.")
    with SessionLocal() as session:
        actor = actor_for(request, session, form.get("as_account"))
        try:
            url = checkout(session, actor, form.get("provider"), form.get("plan_code"),
                           idempotency_key=f"{actor.kind}:{actor.id}:{csrf_token(request)}")
            session.commit()
        except (BillingUnavailable, ValueError) as error:
            session.rollback()
            raise HTTPException(503 if isinstance(error, BillingUnavailable) else 400, str(error)) from error
    return RedirectResponse(url, 303)


@router.post("/membership/webhooks/{provider}")
async def provider_webhook(request: Request, provider: str):
    # Adapters verify signatures before parsing or storing any event.
    from .billing_providers import enabled_provider
    config = BillingConfig.from_environment()
    try:
        enabled_provider(provider, config)
        body = await request.body()
        if len(body) > 256_000:
            raise HTTPException(413, "Event too large.")
        with SessionLocal() as session:
            record, fresh = process_webhook(session, provider, body, dict(request.headers), config=config)
            session.commit()
            return {"received": True, "duplicate": not fresh}
    except BillingUnavailable as error:
        raise HTTPException(503, str(error)) from error
    except (ValueError, IntegrityError) as error:
        raise HTTPException(400, "Provider event could not be accepted.") from error


@router.get("/admin/login")
def admin_login_page(request: Request):
    return render(request, "site_admin_login.html")


@router.post("/admin/login")
async def admin_login(request: Request):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    sign_in_site_admin(request, str(form.get("token", "")))
    return RedirectResponse("/admin/membership", 303)


@router.post("/admin/logout")
async def admin_logout(request: Request):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    request.session.pop("site_admin_fingerprint", None)
    return RedirectResponse("/admin/login", 303)


@router.get("/admin/membership")
def admin_page(request: Request, q: str = "", membership_id: int | None = None):
    require_site_admin(request)
    with SessionLocal() as session:
        students = adults = []
        if q.strip():
            query = q.strip()[:100]
            students = session.scalars(select(WoodchuckProfile).where(WoodchuckProfile.status == "active", or_(
                WoodchuckProfile.display_name.icontains(query, autoescape=True),
                WoodchuckProfile.woodchuck_id.icontains(query, autoescape=True))).limit(25)).all()
            adults = session.scalars(select(TrustedVerifier).where(or_(
                TrustedVerifier.display_name.icontains(query, autoescape=True),
                TrustedVerifier.email.icontains(query, autoescape=True))).limit(25)).all()
        rows = session.scalars(select(Membership).order_by(Membership.id.desc()).limit(50)).all()
        try:
            selected = service.owned_membership(session, membership_id, service.Actor("admin")) if membership_id else None
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        found_memberships = {}
        for kind, owners in (("student", students), ("adult", adults)):
            for owner in owners:
                billing = service.billing_account(session, service.Actor(kind, owner.id))
                found_memberships[(kind, owner.id)] = list(session.scalars(select(Membership.id).where(
                    Membership.billing_account_id == billing.id))) if billing else []
        return render(request, "membership_admin.html", q=q, students=students, adults=adults, rows=rows,
            found_memberships=found_memberships,
            selected=detail(session, selected) if selected else None,
            account=session.get(BillingAccount, selected.billing_account_id) if selected else None,
            subscription=session.scalar(select(ProviderSubscription).where(ProviderSubscription.membership_id == selected.id)) if selected else None,
            history=session.scalars(select(MembershipAuditEvent).where(MembershipAuditEvent.membership_id == selected.id)
                .order_by(MembershipAuditEvent.id.desc()).limit(50)).all() if selected else [])


@router.post("/admin/membership")
async def admin_action(request: Request):
    require_site_admin(request)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    with SessionLocal() as session:
        actor = service.Actor("admin")
        try:
            action = form.get("action")
            if action == "grant":
                expiry = datetime.fromisoformat(str(form["access_until"])) if form.get("access_until") else None
                if expiry and expiry.tzinfo is None:
                    raise ValueError("Expiry must include a timezone.")
                member = service.create_complimentary_membership(session,
                    service.Actor(str(form.get("owner_kind")), integer(form, "owner_id")), actor, access_until=expiry)
                membership_id = member.id
            else:
                membership_id = integer(form, "membership_id")
                if action == "revoke":
                    service.revoke_complimentary_membership(session, membership_id, actor)
                elif action == "add":
                    service.add_seat(session, membership_id, integer(form, "profile_id"), actor)
                elif action == "remove":
                    service.remove_seat(session, membership_id, integer(form, "seat_id"), actor)
                else:
                    raise ValueError("Unknown administrator action.")
            session.commit()
        except (ValueError, LookupError, IntegrityError) as error:
            session.rollback()
            raise HTTPException(409, str(error) if not isinstance(error, IntegrityError) else "Account or spot changed; reload and try again.") from error
    return RedirectResponse(f"/admin/membership?membership_id={membership_id}", 303)
