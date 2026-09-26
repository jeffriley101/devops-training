"""KWS-only callbacks. Signature verification replaces CSRF only on these paths."""
import logging
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.exc import IntegrityError
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .family_routes import page
from .kws_client import Config, KWSUnavailable
from . import kws_verification as kws

router = APIRouter(route_class=PrivateRoute)
logger = logging.getLogger(__name__)


async def handle_webhook(request: Request, environment: str):
    try:
        cfg = Config.load(environment)
        headers = request.headers.getlist('x-kws-signature')
        if len(headers) != 1:
            kws.fail(kws.RejectionStage.MALFORMED_SIGNATURE_HEADER)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                kws.fail(kws.RejectionStage.OVERSIZED_BODY)
        result = kws.webhook_result(bytes(raw), headers[0], cfg)
        with SessionLocal() as session:
            kws.complete(session, cfg, *result)
            session.commit()
        return {'received': True}
    except KWSUnavailable:
        raise HTTPException(503, 'KWS callback is unavailable.') from None
    except kws.InvalidResult as error:
        logger.warning('%s', error.stage.value)
        raise HTTPException(400, 'Invalid KWS result.') from None
    except UnicodeError:
        logger.warning('%s', kws.RejectionStage.INVALID_PAYLOAD_SHAPE.value)
        raise HTTPException(400, 'Invalid KWS result.') from None
    except IntegrityError:
        raise HTTPException(409, 'KWS transaction was already bound to another request.') from None


def handle_response(request: Request, environment: str):
    try:
        cfg = Config.load(environment)
        result = kws.redirect_result(request.query_params, cfg)
        with SessionLocal() as session:
            token = kws.complete(session, cfg, *result)
            session.commit()
        return page(request, 'kws-result', activation_token=token)
    except KWSUnavailable:
        raise HTTPException(503, 'KWS callback is unavailable.') from None
    except kws.InvalidResult:
        raise HTTPException(400, 'Invalid KWS result.') from None
    except IntegrityError:
        raise HTTPException(409, 'KWS transaction was already bound to another request.') from None


@router.post('/family/kws/test/webhook')
async def test_webhook(request: Request):
    return await handle_webhook(request, 'test')


@router.get('/family/kws/test/response')
def test_verification_response(request: Request):
    return handle_response(request, 'test')


@router.post('/family/kws/production/webhook')
async def production_webhook(request: Request):
    return await handle_webhook(request, 'production')


@router.get('/family/kws/production/response')
def production_verification_response(request: Request):
    return handle_response(request, 'production')
