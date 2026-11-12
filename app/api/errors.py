"""Uniform JSON error envelope: {"error": {"code", "message", "request_id"}}.

Nothing here ever includes stack traces, provider messages, or secrets.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError, VisionRateLimitError

logger = logging.getLogger(__name__)

RATE_LIMIT_RETRY_AFTER_SECONDS = "30"


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers=headers,
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = (
            {"Retry-After": RATE_LIMIT_RETRY_AFTER_SECONDS}
            if isinstance(exc, VisionRateLimitError)
            else None
        )
        logger.info(
            "request_failed",
            extra={"error_code": exc.error_code, "status_code": exc.status_code},
        )
        return error_response(
            exc.status_code, exc.error_code, exc.public_message, _request_id(request), headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path.rstrip("/").endswith("expand-description"):
            message = "Invalid request. Send the listing facts as JSON."
        else:
            message = "Invalid request. Send one or more image files in the multipart field 'images'."
        return error_response(400, "invalid_request", message, _request_id(request))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed"}
        return error_response(
            exc.status_code,
            codes.get(exc.status_code, "http_error"),
            str(exc.detail),
            _request_id(request),
            headers=getattr(exc, "headers", None),
        )
