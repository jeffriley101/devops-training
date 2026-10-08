"""First-party browser policy, applied before sessions and enrollment limiting."""
from starlette.requests import Request
from starlette.responses import JSONResponse

from .tester_enrollments import PUBLIC_PREBETA_COHORTS


def same_origin_mutation(request: Request) -> bool:
    origin = request.headers.get("origin")
    site = request.headers.get("sec-fetch-site")
    # Reject contradictory browser metadata even when Origin appears valid.
    if site is not None and site != "same-origin":
        return False
    if origin == str(request.base_url).rstrip("/"):
        return True
    # Referrer-Policy:no-referrer can produce an opaque Origin for a native
    # form. Only browser-owned same-origin document-navigation metadata suffices.
    return (origin == "null" and site == "same-origin"
            and request.headers.get("sec-fetch-mode") == "navigate"
            and request.headers.get("sec-fetch-dest") == "document")


class SessionOriginProtection:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "").rstrip("/")
            # KWS provider callbacks retain their existing signature validation;
            # they are not browser forms and do not authenticate browser sessions.
            webhook = path in {"/family/kws/test/webhook", "/family/kws/production/webhook"}
            protected = (path.startswith(("/account/", "/guest/", "/family/"))
                         or path in {"/setup", "/admin/age/correct"}) and not webhook
            if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and protected:
                if not same_origin_mutation(Request(scope)):
                    return await JSONResponse(
                        {"detail": "A same-origin request is required."}, 403,
                        headers={"Cache-Control": "no-store"},
                    )(scope, receive, send)
            # Public Pre-Beta invitation GETs intentionally accept top-level links.
            # A foreign subresource must not silently establish attribution.
            cohort_key = path.removeprefix("/prebeta/")
            if cohort_key in PUBLIC_PREBETA_COHORTS and scope["method"] in {"GET", "HEAD"}:
                request = Request(scope)
                site = request.headers.get("sec-fetch-site")
                if site in {"same-site", "cross-site"} and not (
                    request.headers.get("sec-fetch-mode") == "navigate"
                    and request.headers.get("sec-fetch-dest") == "document"
                ):
                    return await JSONResponse({"detail": "Open the invitation directly."}, 403)(scope, receive, send)
        await self.app(scope, receive, send)
