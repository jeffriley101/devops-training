"""Request diagnostics without URL capabilities, query values or exception payloads.

Installed when the app is imported, after Uvicorn configures its default loggers.
Route templates are used instead of attempting to recognize every secret format.
"""
from contextvars import ContextVar
import logging
from pathlib import Path
from time import monotonic
import traceback
from uuid import uuid4


_request = ContextVar("safe_log_request", default=None)


def _route(scope):
    route = scope.get("route")
    # Never fall back to scope['path']: even an unmatched URL can contain a secret.
    return getattr(route, "path", "<unmatched>")


def _method(scope):
    method = scope.get("method", "UNKNOWN")
    return method if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"} else "UNKNOWN"


def _exception_summary(info):
    kind, _value, tb = info
    frames = ",".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(tb)
    )
    # No exception message, SQL parameters, source lines, locals or chained values.
    return f"exception_type={kind.__name__} frames={frames}"


class SafeUvicornFilter(logging.Filter):
    def filter(self, record):
        context = _request.get()
        if record.name == "uvicorn.access":
            args = record.args
            status = args[4] if isinstance(args, tuple) and len(args) == 5 else 0
            scope = context[0] if context else {}
            record.args = ("-", _method(scope), _route(scope), scope.get("http_version", "1.1"), status)
            record.msg = '%s - "%s %s HTTP/%s" %d'
            if context:
                record.msg += f" request_id={context[1]} duration_ms={(monotonic() - context[2]) * 1000:.1f}"
        elif record.name == "uvicorn.asgi":
            # Uvicorn's optional TRACE middleware otherwise dumps scopes/bodies.
            record.msg, record.args = "ASGI trace payload omitted", ()
        elif "WebSocket" in str(record.msg) and record.args:
            record.msg, record.args = "WebSocket request (path omitted)", ()
        if record.exc_info:
            record.msg, record.args = "request_failed " + _exception_summary(record.exc_info), ()
            record.exc_info = None
            record.exc_text = None
        return True


def install_safe_logging():
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn.asgi"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, SafeUvicornFilter) for item in logger.filters):
            logger.addFilter(SafeUvicornFilter())
        if name == "uvicorn.access":
            # Keep the correlation/timing suffix; Uvicorn's AccessFormatter builds
            # a separate request_line and would discard that suffix.
            for handler in logger.handlers:
                handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))


class SafeRequestLogContext:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        token = _request.set((scope, request_id, monotonic()))
        try:
            await self.app(scope, receive, send)
        except Exception as error:
            logging.getLogger("uvicorn.error").error(
                "request_failed method=%s route=%s status=500 request_id=%s duration_ms=%.1f %s",
                _method(scope), _route(scope), request_id,
                (monotonic() - _request.get()[2]) * 1000,
                _exception_summary((type(error), error, error.__traceback__)),
            )
            raise
        finally:
            _request.reset(token)
