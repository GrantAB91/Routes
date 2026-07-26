"""Structured API errors (§16.7, §19.4).

Contour never returns a bare 500 with "something went wrong". Every failure
carries a stable machine code, a sentence a rider can act on, and where relevant
the exact thing that is missing. That last part is what turns "routing failed"
into "no routing tiles are built for this area; run infra/valhalla/build-tiles.sh".
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse

from .config import DisabledCapabilityError
from .io.validation import FileRejectedError
from .providers.routing import NoRouteFoundError, RoutingError


class ContourError(Exception):
    """Base for errors that map onto a documented API response."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "error"

    def __init__(self, message: str, **context: Any) -> None:
        self.message = message
        self.context = context
        super().__init__(message)


class NotFoundError(ContourError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class PermissionDeniedError(ContourError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class LicenceRestrictionError(ContourError):
    """An action a source licence does not permit (§20.9).

    Distinct from a permission error: the user is allowed, the *data* is not.
    The response quotes the restriction so the difference is visible.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "licence_restriction"


def error_body(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    **context: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if context:
        body["error"]["context"] = context
    if request_id:
        body["error"]["request_id"] = request_id
    return body


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def handle_contour_error(request: Request, exc: ContourError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, request_id=_request_id(request), **exc.context),
    )


async def handle_disabled_capability(
    request: Request, exc: DisabledCapabilityError
) -> JSONResponse:
    """A capability that is switched off, not broken.

    503 rather than 500: the request was valid and would work in a deployment
    with this capability configured. The remedy is returned verbatim so the
    operator does not have to guess which variable is missing (§21.8).
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=error_body(
            "capability_disabled",
            str(exc),
            request_id=_request_id(request),
            capability=exc.capability,
            reason=exc.reason,
            remedy=exc.remedy,
        ),
    )


async def handle_no_route_found(request: Request, exc: NoRouteFoundError) -> JSONResponse:
    """No connection exists between the requested points.

    422 rather than 404: the request was well formed and the resource exists,
    but the geography does not permit an answer. Distinct from a route that
    exists and breaks a constraint, which is a 200 with a feasibility verdict.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_body(exc.code, str(exc), request_id=_request_id(request)),
    )


async def handle_routing_error(request: Request, exc: RoutingError) -> JSONResponse:
    return JSONResponse(
        status_code=(
            status.HTTP_503_SERVICE_UNAVAILABLE if exc.retryable else status.HTTP_400_BAD_REQUEST
        ),
        content=error_body(
            exc.code, str(exc), request_id=_request_id(request), retryable=exc.retryable
        ),
    )


async def handle_file_rejected(request: Request, exc: FileRejectedError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_body(exc.code.value, str(exc), request_id=_request_id(request)),
    )


EXCEPTION_HANDLERS = {
    ContourError: handle_contour_error,
    DisabledCapabilityError: handle_disabled_capability,
    NoRouteFoundError: handle_no_route_found,
    RoutingError: handle_routing_error,
    FileRejectedError: handle_file_rejected,
}
