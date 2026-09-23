"""One read-only report inside the existing site-admin boundary."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates

from .analytics import build_report
from .db import SessionLocal
from .site_admin import require_site_admin
from .tester_enrollments import c001_registration_open


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/admin/analytics")
def analytics_page(request: Request, cohort: str | None = None):
    require_site_admin(request)
    allowed = {None, "PILOT-D1", "C001"}
    if cohort not in allowed:
        raise HTTPException(400, "Unknown tester cohort.")
    report = build_report(SessionLocal, cohort_key=cohort)
    return templates.TemplateResponse(
        request=request, name="analytics_admin.html",
        context={
            "title": "Pre-beta activity",
            "report": report,
            "selected_cohort": cohort,
            "c001_registration_open": c001_registration_open(),
        },
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
