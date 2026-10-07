"""Small live entry point for manually approved Classroom Program onboarding."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .classroom import ClassroomDenied, accept_program_invitation
from .classroom_invitation import create_program_invitation, parse_program_invitation
from .classroom_models import ClassroomProgram
from .db import SessionLocal
from .email_service import DeliveryResult, EmailService, public_link
from .login_limits import enforce_login_limit
from .membership_routes import PrivateRoute
from .models import Organization
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token, require_site_admin
from .verifier_routes import SESSION_VERIFIER_ID
from .verifiers import validate_email


router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
logger = logging.getLogger(__name__)


def _same_origin(request: Request) -> None:
    if not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.")


def _program_available(session, organization_id: int) -> bool:
    return (session.scalar(select(Organization.id).where(Organization.id == organization_id)) is not None
            and session.get(ClassroomProgram, organization_id) is None)


def _admin_page(request: Request, *, delivery: DeliveryResult | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request, name="classroom_invitation_admin.html", status_code=status_code,
        context={"title": "Music Program invitation", "csrf": csrf_token(request),
                 "message": request.session.pop("classroom_invitation_message", None),
                 "delivery": delivery},
    )


@router.get("/admin/classroom/invitations")
def admin_program_invitation_page(request: Request):
    require_site_admin(request)
    return _admin_page(request)


@router.post("/admin/classroom/invitations")
async def issue_program_invitation(request: Request):
    require_site_admin(request)
    _same_origin(request)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    if set(form) != {"csrf", "organization_id", "email"}:
        raise HTTPException(400, "An exact Organization ID and director email are required.")
    try:
        raw_id = str(form["organization_id"])
        if not raw_id.isdecimal() or int(raw_id) <= 0:
            raise ValueError()
        organization_id = int(raw_id)
        email = validate_email(str(form["email"]))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "A valid Organization ID and director email are required.") from error
    with SessionLocal() as session:
        if session.scalar(select(Organization.id).where(Organization.id == organization_id)) is None:
            raise HTTPException(404, "Organization is unavailable for invitation.")
        if session.get(ClassroomProgram, organization_id) is not None:
            raise HTTPException(409, "Program has already been provisioned.")

    token = create_program_invitation(organization_id, email)
    # public_link uses the configured public URL, or the fixed Woodshed URL. It
    # never reflects a request Host into this mailbox-control capability link.
    acceptance_url = public_link(f"/classroom/invitations/{token}")
    try:
        result = EmailService().send_program_invitation(
            recipient=email, acceptance_url=acceptance_url,
        )
    except Exception:
        # SMTP/internal errors must not expose transport or configuration data.
        result = DeliveryResult(False, "delivery_failed")
    if not result.sent:
        logger.warning("classroom_program_invitation_email_failed code=%s", result.code)
        return _admin_page(request, delivery=result, status_code=503)
    # Request logging carries the route template and correlation ID, never the
    # private token, Program, or mailbox. Operator attribution needs later schema.
    logger.info("classroom_program_invitation_email_sent")
    request.session["classroom_invitation_message"] = "Program invitation email sent."
    return RedirectResponse("/admin/classroom/invitations", 303)


@router.get("/classroom/invitations/{token}")
def program_invitation_page(request: Request, token: str):
    try:
        invitation = parse_program_invitation(token)
    except ValueError as error:
        raise HTTPException(410, "This Program invitation is invalid or expired.") from error
    with SessionLocal() as session:
        if not _program_available(session, invitation.organization_id):
            raise HTTPException(410, "This Program invitation is unavailable.")
    return templates.TemplateResponse(
        request=request, name="classroom_invitation.html",
        context={"title": "Music Program invitation", "csrf": csrf_token(request)},
    )


@router.post("/classroom/invitations/{token}")
async def accept_program_invitation_route(request: Request, token: str):
    _same_origin(request)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    if set(form) - {"csrf", "pin", "display_name"} or "pin" not in form:
        raise HTTPException(400, "Only the invited adult's credentials may be submitted.")
    try:
        invitation = parse_program_invitation(token)
    except ValueError as error:
        raise HTTPException(410, "This Program invitation is invalid or expired.") from error
    enforce_login_limit(request, "verifier", invitation.email)
    with SessionLocal() as session:
        try:
            program = accept_program_invitation(
                session, token=token, pin=str(form["pin"]),
                display_name=str(form["display_name"]) if "display_name" in form else None,
            )
            session.commit()
            owner_id = program.owner_verifier_id
        except ClassroomDenied as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "This invitation cannot be accepted. Reload and try again.") from error
    request.session[SESSION_VERIFIER_ID] = owner_id
    return RedirectResponse("/trusted-verifiers/dashboard", 303)
