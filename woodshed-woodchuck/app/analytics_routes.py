"""One read-only report inside the existing site-admin boundary."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from .analytics import build_report
from .db import SessionLocal
from .site_admin import require_site_admin


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/admin/analytics")
def analytics_page(request: Request):
    require_site_admin(request)
    report = build_report(SessionLocal)
    return templates.TemplateResponse(
        request=request, name="analytics_admin.html",
        context={"title": "Pre-beta activity", "report": report},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
