"""Student controls for individual Classroom P-Chart sharing."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select

from . import classroom, classroom_chart_sharing as sharing, classroom_director, classroom_s2
from .classroom_models import ClassroomChartDirectorShare
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .models import PracticeChart
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token

router = APIRouter(prefix="/classroom/chart-sharing", route_class=PrivateRoute)


class SharingEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf: str
    assignment_ids: list[StrictInt] = Field(max_length=1)


def _enabled():
    if not classroom_director.enabled():
        raise HTTPException(404, "Classroom sharing unavailable.")


def _student(session, request):
    try:
        return classroom_s2.student_from_request(session, request)
    except classroom.ClassroomDenied as error:
        raise HTTPException(401, "Student sign-in is required.") from error


@router.get("")
def available(request: Request):
    _enabled()
    with SessionLocal() as session:
        student = _student(session, request)
        try:
            choices = sharing.choices(session, student)
            has_shares = session.scalar(select(ClassroomChartDirectorShare.id).join(
                PracticeChart, PracticeChart.id == ClassroomChartDirectorShare.chart_id).where(
                PracticeChart.profile_id == student.profile_id,
                ClassroomChartDirectorShare.withdrawn_at.is_(None)).limit(1)) is not None
            return {"choices": choices, "has_shares": has_shares, "csrf": csrf_token(request)}
        except classroom.ClassroomDenied:
            return {"choices": [], "csrf": csrf_token(request)}


@router.get("/{chart_id}")
def selected(request: Request, chart_id: int):
    _enabled()
    with SessionLocal() as session:
        student = _student(session, request)
        try:
            choices = sharing.choices(session, student)
        except classroom.ClassroomDenied as error:
            raise HTTPException(404, "Classroom sharing unavailable.") from error
        chart = session.scalar(select(PracticeChart.id).where(
            PracticeChart.id == chart_id, PracticeChart.profile_id == student.profile_id))
        if chart is None:
            raise HTTPException(404, "P-Chart unavailable.")
        valid_ids = {choice["assignment_id"] for choice in choices}
        refs = session.execute(select(ClassroomChartDirectorShare.id, ClassroomChartDirectorShare.assignment_id).where(
            ClassroomChartDirectorShare.chart_id == chart_id,
            ClassroomChartDirectorShare.withdrawn_at.is_(None))).all()
    # The initial list is a hint; recheck each selected Program in its own
    # Program-first transaction rather than holding cross-Program locks.
    selected_ids = []
    for share_id, assignment_id in refs:
        if assignment_id not in valid_ids:
            continue
        with SessionLocal() as session:
            try:
                student = _student(session, request)
                if sharing.selection_current(session, student=student, share_id=share_id):
                    selected_ids.append(assignment_id)
            except classroom.ClassroomDenied:
                continue
    return {"choices": choices, "selected": selected_ids, "csrf": csrf_token(request)}


@router.post("/{chart_id}")
def edit(request: Request, chart_id: int, payload: SharingEdit):
    _enabled()
    if not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.")
    check_csrf(request, payload.csrf)
    with SessionLocal() as session:
        student = _student(session, request)
        try:
            sharing.set_sharing(session, student=student, chart_id=chart_id,
                                assignment_ids=payload.assignment_ids)
            session.commit()
        except classroom.ClassroomDenied as error:
            session.rollback()
            raise HTTPException(404, "Classroom sharing unavailable.") from error
    return {"saved": True}
