"""Native school-name form using the accepted director session/PIN boundary."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import classroom as s1, classroom_identity as service, classroom_management_routes as management
from . import classroom_management_views as views
from .classroom_enrollment_routes import _adult, _number
from .db import SessionLocal
from .membership_routes import PrivateRoute

router = APIRouter(route_class=PrivateRoute)


@router.post('/classroom/programs/{program_id}/classes/{class_id}/identity')
async def save_identity(request: Request, program_id: int, class_id: int):
    if not service.enabled():
        raise HTTPException(404, 'Private roster identity unavailable.')
    form = await management._form(request)
    management._exact(form, {'director', 'pin', 'woodchuck_id', 'school_name', 'expected_version'})
    _number(str(program_id)); _number(str(class_id))
    value = form['expected_version']
    if not value.isascii() or not value.isdecimal() or len(value) > 10 or int(value) > 2147483647:
        raise HTTPException(400, 'Reopen the current roster.')
    unavailable = False
    with SessionLocal() as session:
        _, actor = _adult(request, session, form)
        try:
            service.set_school_name(session, actor=actor, program_id=program_id, class_id=class_id,
                woodchuck_id=form['woodchuck_id'], school_name=form['school_name'], expected_version=int(value))
            detail = views.detail(session, actor=actor, program_id=program_id, class_id=class_id)
            session.commit()
        except (s1.ClassroomDenied, IntegrityError):
            session.rollback()
            failed = True
        except SQLAlchemyError:
            # Do not propagate SQL text/parameters containing a school name into
            # generic exception logging or the response during a storage failure.
            session.rollback()
            failed = unavailable = True
        else:
            failed = False
    if failed:
        return management._page(request, mode='detail', detail=None, status_code=503 if unavailable else 409,
            error='School name not saved. Reopen the roster; check current membership, authority, account permission and name version.')
    return management._page(request, mode='detail', detail=detail, message='School name saved.')
