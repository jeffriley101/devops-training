"""KWS-only callbacks. Signature verification replaces CSRF only on these two paths."""
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.exc import IntegrityError
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .family_routes import page
from .kws_client import Config, KWSUnavailable
from . import kws_verification as kws

router = APIRouter(route_class=PrivateRoute)


@router.post('/family/kws/test/webhook')
async def webhook(request: Request):
    try:
        cfg = Config.load()
        headers = request.headers.getlist('x-kws-signature')
        if len(headers) != 1:
            kws.fail()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                kws.fail()
        result = kws.webhook_result(bytes(raw), headers[0], cfg)
        with SessionLocal() as session:
            kws.complete(session, cfg, *result)
            session.commit()
        return {'received': True}
    except KWSUnavailable:
        raise HTTPException(503, 'KWS Test callback is unavailable.') from None
    except (kws.InvalidResult, UnicodeError):
        raise HTTPException(400, 'Invalid KWS Test result.') from None
    except IntegrityError:
        raise HTTPException(409, 'KWS Test transaction was already bound to another request.') from None


@router.get('/family/kws/test/response')
def verification_response(request: Request):
    try:
        cfg = Config.load()
        result = kws.redirect_result(request.query_params)
        with SessionLocal() as session:
            token = kws.complete(session, cfg, *result)
            session.commit()
        return page(request, 'kws-result', activation_token=token)
    except KWSUnavailable:
        raise HTTPException(503, 'KWS Test callback is unavailable.') from None
    except kws.InvalidResult:
        raise HTTPException(400, 'Invalid KWS Test result.') from None
    except IntegrityError:
        raise HTTPException(409, 'KWS Test transaction was already bound to another request.') from None
